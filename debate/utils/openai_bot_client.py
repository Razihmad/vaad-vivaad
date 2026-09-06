import openai
from django.conf import settings

from debate.utils.bot_prompt import BOT_SYSTEM_PROMPT, build_bot_prompt


class OpenAIBotClient:
    def __init__(self) -> None:
        self._client: openai.OpenAI | None = None

    def _get_client(self) -> openai.OpenAI:
        if self._client is None:
            self._client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        return self._client

    def generate(
        self,
        *,
        topic: str,
        description: str,
        side: str,
        round_type: str,
        history: list[str],
    ) -> str:
        client = self._get_client()
        prompt = build_bot_prompt(
            topic=topic,
            description=description,
            side=side,
            round_type=round_type,
            history=history,
        )
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=120,
            messages=[
                {"role": "system", "content": BOT_SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
        )
        return response.choices[0].message.content.strip()[:200]


openai_bot_client = OpenAIBotClient()
