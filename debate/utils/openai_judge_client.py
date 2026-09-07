from typing import Dict

import openai
from django.conf import settings

from debate.utils.judge_prompt import JUDGE_SYSTEM_PROMPT, parse_judge_response


class OpenAIJudgeClient:
    """Calls OpenAI to judge a debate transcript."""

    def __init__(self) -> None:
        self._client: openai.OpenAI | None = None

    def _get_client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    def judge(self, *, transcript: str, judge_config: Dict) -> dict:
        client = self._get_client()
        system_prompt = judge_config.get("system_prompt", JUDGE_SYSTEM_PROMPT)
        model = judge_config.get("model", "gpt-4o-mini")
        response = client.chat.completions.create(
            model=model,
            max_tokens=1024,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": f"Evaluate the following debate transcript:\n\n{transcript}",
                },
            ],
        )

        text = response.choices[0].message.content
        if not text or not text.strip():
            raise RuntimeError(
                f"Judge model returned no text to parse (finish_reason={response.choices[0].finish_reason})"
            )
        return parse_judge_response(text)


openai_judge_client = OpenAIJudgeClient()
