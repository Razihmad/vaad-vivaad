import json
import re

_JSON_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)

JUDGE_SYSTEM_PROMPT = """\
You are a strict but fair debate judge. Evaluate 1v1 structured debates objectively.

Score each debater 1-10 on four dimensions:
- Argument Strength (30%): coherence and support for their position
- Rebuttal/Engagement (30%): addressing the opponent's points
- Persuasiveness (25%): moving the needle on the topic
- Clarity (15%): ease of following the argument

Respond ONLY with valid JSON, no extra text:
{
  "winner": "pro" or "con",
  "pro": {
    "argument_score": <1-10>,
    "rebuttal_score": <1-10>,
    "clarity_score": <1-10>,
    "persuasion_score": <1-10>
  },
  "con": {
    "argument_score": <1-10>,
    "rebuttal_score": <1-10>,
    "clarity_score": <1-10>,
    "persuasion_score": <1-10>
  },
  "reasoning": "<2-3 sentence verdict rationale>",
  "strongest_moment": "<exact quote of the single best argument from the debate>",
  "coaching_tip_pro": "<one specific actionable improvement tip for the pro debater>",
  "coaching_tip_con": "<one specific actionable improvement tip for the con debater>"
}"""


def parse_judge_response(text: str) -> dict:
    cleaned = _JSON_FENCE_RE.sub("", text.strip()).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        raise RuntimeError(
            f"Judge model returned non-JSON text: {cleaned[:500]!r}"
        ) from e
