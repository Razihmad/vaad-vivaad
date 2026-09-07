from typing import Dict

import anthropic
from django.conf import settings

from debate.utils.judge_prompt import JUDGE_SYSTEM_PROMPT, parse_judge_response


class ClaudeJudgeClient:
    """Calls Claude to judge a debate transcript.

    The system prompt is marked for prompt caching — it never changes between
    debates, so repeated judging calls pay only for the transcript tokens.
    """

    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        return self._client

    def judge(self, *, transcript: str, judge_config: Dict) -> dict:
        client = self._get_client()
        system_prompt = judge_config.get("system_prompt", JUDGE_SYSTEM_PROMPT)
        model = judge_config.get("model", "claude-sonnet-4-6")
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            system=[
                {
                    "type": "text",
                    "text": system_prompt,
                    # Cache the system prompt — it is identical for every debate.
                    # First call writes the cache; subsequent calls read it at ~10% cost.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[
                {
                    "role": "user",
                    "content": f"Evaluate the following debate transcript:\n\n{transcript}",
                }
            ],
        )

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            raise RuntimeError(
                f"Judge model declined to evaluate this debate (stop_reason=refusal, category={category})"
            )

        text = next(
            (block.text for block in response.content if block.type == "text"), None
        )
        if not text or not text.strip():
            raise RuntimeError(
                f"Judge model returned no text to parse (stop_reason={response.stop_reason})"
            )
        return parse_judge_response(text)


judge_client = ClaudeJudgeClient()
