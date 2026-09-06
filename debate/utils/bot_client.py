import anthropic
from django.conf import settings

from debate.utils.bot_prompt import BOT_SYSTEM_PROMPT, build_bot_prompt


class DebateBotClient:
    def __init__(self) -> None:
        self._client: anthropic.Anthropic | None = None

    def _get_client(self) -> anthropic.Anthropic:
        if self._client is None:
            self._client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
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
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=120,
            system=[
                {
                    "type": "text",
                    "text": BOT_SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()[:200]


bot_client = DebateBotClient()
