"""
Two-pass AI judge for Samvaad debates.

Architecture (mirrors BasicJudge / Ivan):
  Pass 1 — Reasoning:  prose analysis, no scores yet.
  Pass 2 — Scoring:    JSON scores derived from the pass-1 analysis.

Forcing reasoning before scoring prevents score fabrication and produces
verdicts that are consistent with the rationale.  Validated against the
Debatrix benchmark (Liang et al., ACL 2024) and supported by
Sternlicht et al. (2025).

The output dict matches exactly what selectors.apply_judgement_outcome expects.
"""

import json
import logging
import re
from typing import Dict

import anthropic
from django.conf import settings

logger = logging.getLogger(__name__)

# ── Prompts ────────────────────────────────────────────────────────────────────

_REASONING_PROMPT = """\
You are analyzing a 1v1 short-form debate to determine who made the stronger case.
You will NOT assign scores in this step — prose analysis only.

## Debate format

This debate has exactly 2 rounds — there is NO closing round:

- **OPENING**: each debater sends one statement simultaneously (neither sees the other's
  before writing their own). Judge them as standalone opening arguments.
- **REBUTTAL**: free-flowing back-and-forth. Each side may send multiple messages in
  turns. Judge the REBUTTAL round holistically — look at how each debater responds to
  the opponent across all their REBUTTAL messages combined, not message by message.

Do not penalise either side for the absence of a closing statement — the format
does not have one.

## Your task

Read the debate carefully, then write a structured analysis covering:

1. **Each debater's strongest point** — quote it briefly and say what made it strong.
2. **Each debater's weakest point** — what did they miss, dodge, or fail to support?
3. **Engagement quality** — did each debater actually address the other's main argument,
   or did they talk past them?
4. **Verdict reasoning** — who made the stronger overall case, and *why specifically*.
   If it is genuinely too close to call (both made comparable contributions of comparable
   quality), say so explicitly and explain what would have tipped it.

## Critical rules

- Do not favour the side you personally agree with. Your views on the motion are irrelevant.
- Do not reward confident tone over weak reasoning.
- Length is not a virtue. A short sharp speech can beat a long rambling one.
- Be honest about uncertainty. If it is genuinely 50/50, say so.
- Be specific. Reference actual content from the speeches.

## Output

Plain prose. No JSON. 150–350 words. End with a clear sentence stating who made
the stronger case (or that it is genuinely too close to call)."""


_SCORING_PROMPT = """\
You are scoring a 1v1 debate. You have already analyzed the debate in a prior step
(shown to you in the user message). Your job now is to assign dimension scores and
produce the final JSON verdict.

## Debate format

2 rounds only — OPENING (one message each, simultaneous) + REBUTTAL (multiple
turn-based messages). No closing round. Do not penalise for its absence.

The scores you assign **must be consistent with your prior analysis.**
If your analysis said Pro made the stronger case, Pro's weighted total must be higher.
If your analysis said it was genuinely too close to call, scores should be very close.

## Dimensions (score each 1–10, integer)

ARGUMENT (weight 30%) — Does the claim support their side with coherent reasoning?
- 1–3: Off-topic, contradictory, or actively undermines their side
- 4–6: Supports their side but with logical gaps or unsupported claims
- 7–10: Supports their side with clear, complete reasoning

REBUTTAL (weight 30%) — Did they actually engage what the opponent said?
- 1–3: Ignores opponent entirely; parallel monologue
- 4–6: Partial response; addresses weaker points but skips the strongest
- 7–10: Directly engages the opponent's strongest points and answers them

PERSUASIVENESS (weight 25%) — After reading, does their side feel stronger?
- 1–3: Weak or counterproductive — hurts their own case
- 4–6: Makes their case but doesn't noticeably move the needle
- 7–10: Noticeably strengthens their side; makes you reconsider

CLARITY (weight 15%) — Can the meaning be followed without effort?
- 1–3: Hard to parse; unclear what is even being claimed
- 4–6: Mostly clear with some confusing moments
- 7–10: Easy to follow; points are distinct and well-structured

## Critical rules

- Score each dimension independently. Surface real differences between the debaters.
- Do not produce mirrored scores (e.g. 7/6/7/8 vs 7/6/7/8) unless your prior
  analysis genuinely concluded the debate was a perfect tie on every axis.
- The rationale field must restate the verdict from your prior analysis in 2 sentences.
  Do not contradict yourself.
- For strongest_moment: pick the single best line from the entire debate (either side).

## Output schema (raw JSON only — no prose, no markdown fences)

{
  "winner": "pro" or "con",
  "pro": {
    "argument_score": <integer 1-10>,
    "rebuttal_score": <integer 1-10>,
    "clarity_score": <integer 1-10>,
    "persuasion_score": <integer 1-10>
  },
  "con": {
    "argument_score": <integer 1-10>,
    "rebuttal_score": <integer 1-10>,
    "clarity_score": <integer 1-10>,
    "persuasion_score": <integer 1-10>
  },
  "reasoning": "<2 sentences restating who made the stronger case and why>",
  "strongest_moment": "<exact quoted line from the debate, max 30 words>",
  "coaching_tip_pro": "<one specific actionable tip for Pro, max 30 words>",
  "coaching_tip_con": "<one specific actionable tip for Con, max 30 words>"
}"""


# ── JSON extraction ────────────────────────────────────────────────────────────

def _extract_json(raw: str) -> str:
    """Extract the first balanced JSON object from a string.
    Tolerates markdown fences and surrounding prose."""
    cleaned = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```\s*$', '', cleaned)

    start = cleaned.find('{')
    if start == -1:
        raise ValueError("No JSON object found in model output")

    depth = 0
    in_string = False
    escape = False
    for i, ch in enumerate(cleaned[start:], start):
        if escape:
            escape = False
            continue
        if ch == '\\':
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
        if in_string:
            continue
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return cleaned[start:i + 1]

    raise ValueError("Unbalanced JSON in model output")


def _validate_output(data: dict) -> None:
    """Minimal validation to catch obvious schema problems before DB write."""
    required_top = {"winner", "pro", "con", "reasoning", "strongest_moment",
                    "coaching_tip_pro", "coaching_tip_con"}
    missing = required_top - data.keys()
    if missing:
        raise ValueError(f"Judge output missing keys: {missing}")

    if data["winner"] not in ("pro", "con"):
        raise ValueError(f"Invalid winner value: {data['winner']!r}")

    score_keys = {"argument_score", "rebuttal_score", "clarity_score", "persuasion_score"}
    for side in ("pro", "con"):
        if not isinstance(data[side], dict):
            raise ValueError(f"Judge output[{side!r}] is not a dict")
        missing_scores = score_keys - data[side].keys()
        if missing_scores:
            raise ValueError(f"Judge output[{side!r}] missing score keys: {missing_scores}")


# ── Client ─────────────────────────────────────────────────────────────────────

class ClaudeJudgeClient:
    """
    Two-pass judge following the BasicJudge / Ivan architecture.

    Pass 1 (reasoning):  claude reads the transcript and writes prose analysis.
    Pass 2 (scoring):    claude sees its own analysis and produces JSON scores.

    This forces score-after-rationale at the architecture level, which produces
    verdicts that are internally consistent.

    The system prompts are marked for prompt caching — they never change between
    debates, so repeated judging calls pay only for the transcript + reasoning tokens.
    """

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def _call(self, *, system: str, user_message: str, model: str, max_tokens: int) -> str:
        client = self._get_client()
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=0,
            system=[
                {
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        return "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()

    def judge(self, *, transcript: str, judge_config: Dict) -> dict:
        model = judge_config.get("model", "claude-haiku-4-5-20251001")
        reasoning_text = ""
        scoring_text = ""

        try:
            # ── Pass 1: Reasoning ──────────────────────────────────────────────
            reasoning_system = judge_config.get("reasoning_prompt", _REASONING_PROMPT)
            reasoning_text = self._call(
                system=reasoning_system,
                user_message=transcript,
                model=model,
                max_tokens=768,
            )
            logger.debug("Judge reasoning pass:\n%s", reasoning_text)
            if not reasoning_text:
                raise ValueError("Pass 1 (reasoning) returned an empty response")

            # ── Pass 2: Scoring ────────────────────────────────────────────────
            scoring_system = judge_config.get("scoring_prompt", _SCORING_PROMPT)
            scoring_user_message = "\n\n".join([
                transcript,
                "--- YOUR PRIOR ANALYSIS ---",
                reasoning_text,
                "--- END ANALYSIS ---",
                "Now produce the JSON scores. They must be consistent with your analysis above.",
            ])
            scoring_text = self._call(
                system=scoring_system,
                user_message=scoring_user_message,
                model=model,
                max_tokens=768,
            )
            logger.debug("Judge scoring pass:\n%s", scoring_text)
            if not scoring_text:
                raise ValueError("Pass 2 (scoring) returned an empty response")

            # ── Parse & validate ───────────────────────────────────────────────
            json_text = _extract_json(scoring_text)
            data = json.loads(json_text)
            _validate_output(data)

        except Exception as exc:
            logger.error(
                "Judge failed.\nReasoning:\n%s\n\nScoring output:\n%s",
                reasoning_text or "<not reached>",
                scoring_text or "<not reached>",
            )
            raise ValueError(f"Failed to judge debate: {exc}") from exc

        return data


judge_client = ClaudeJudgeClient()
