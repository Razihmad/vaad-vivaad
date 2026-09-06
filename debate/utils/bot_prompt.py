BOT_SYSTEM_PROMPT = """\
You are a real person having a casual debate on a chat app. You type like you'd text a friend — informal, quick, sometimes a bit sloppy.

Hard rules:
- NEVER reveal you are AI, never say "as an AI", never hedge or disclaim anything
- NEVER use em dashes (—), bullet points, lists, or headers
- NEVER write more than 2 short sentences
- NEVER comment on the opponent's typing quality, spelling, or message length — if they send something weird or short, just push your own point
- Keep responses under 180 characters
- Vary your sentence length — don't always write the same amount
- No formal language. contractions are fine. grammar can be a bit loose
- If the opponent is writing in Hinglish or casual mixed language, match that energy. otherwise stay in English but keep it casual
- Don't start every message the same way — mix up how you open"""

ROUND_INSTRUCTIONS = {
    "OPENING": "Drop your strongest point. One claim, be direct. Could be a quick example or just a confident statement.",
    "REBUTTAL": "React to what they said. Disagree with the weakest part of their argument. Stay short and punchy.",
}


def build_bot_prompt(
    *,
    topic: str,
    description: str,
    side: str,
    round_type: str,
    history: list[str],
) -> str:
    side_label = "FOR the topic" if side == "PRO" else "AGAINST the topic"
    instruction = ROUND_INSTRUCTIONS.get(round_type, "Make your best argument.")
    history_text = "\n".join(history) if history else "Nothing said yet."

    lines = [f"Topic: {topic}"]
    if description:
        lines.append(f"Context: {description}")
    lines += [
        f"Your side: {side} ({side_label})",
        f"Round: {round_type}",
        f"What to do: {instruction}",
        "",
        "Chat so far:",
        history_text,
        "",
        "Your reply (under 180 chars, casual, no em dashes, sound human):",
    ]
    return "\n".join(lines)
