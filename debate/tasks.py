import random

from asgiref.sync import async_to_sync
from celery import shared_task
from channels.layers import get_channel_layer

from debate.serializers import JudgementSerializer, MessageSerializer, RoundSerializer


@shared_task
def send_advance_round_event(group_name: str, data: dict) -> None:
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "round.advance",
            "data": data,
        },
    )


@shared_task
def start_judgement_of_debate_and_share_result(debate_id: int, group_name: str):
    from debate.services import auto_judge_debate

    judgement = auto_judge_debate(debate_id=debate_id)
    if not judgement:
        return
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "debate_result",
            "data": JudgementSerializer(judgement).data,
        },
    )


@shared_task
def abandon_debate_if_still_disconnected(
    debate_id: int, user_id: int, disconnected_at: str, group_name: str
) -> None:
    """Fires ``DISCONNECT_GRACE_SECONDS`` after a participant's socket drops. No-ops if
    they rejoined in the meantime (rejoining clears the marker this task compares
    against) or if a later disconnect superseded this one."""
    from debate.selectors import get_participant_disconnected_at
    from debate.services import abandon_debate_and_schedule_judgement

    marker = get_participant_disconnected_at(debate_id=debate_id, user_id=user_id)
    if marker is None or marker.isoformat() != disconnected_at:
        return
    abandon_debate_and_schedule_judgement(debate_id=debate_id, group_name=group_name)


@shared_task
def assign_bot_if_no_match(queue_id: int) -> None:
    from debate.services import match_with_bot

    match_with_bot(queue_id=queue_id)


@shared_task
def bot_respond(debate_id: int) -> None:
    from debate.services import (
        generate_and_submit_bot_message,
        get_bot_user_in_debate,
        _is_user_turn,
    )
    from debate.selectors import get_debate_by_id, get_current_round

    result = generate_and_submit_bot_message(debate_id=debate_id)
    if not result:
        return

    message, next_round = result
    channel_layer = get_channel_layer()
    debate_group = f"debate_{debate_id}"

    async_to_sync(channel_layer.group_send)(
        debate_group,
        {"type": "message.new", "message": MessageSerializer(message).data},
    )
    if not next_round:
        return
    async_to_sync(channel_layer.group_send)(
        debate_group,
        {"type": "round.advance", "data": RoundSerializer(next_round).data},
    )
    # Check whether the bot goes first in the new round (e.g. CON opens CLOSING)
    debate = get_debate_by_id(debate_id=debate_id)
    if debate:
        bot_user = get_bot_user_in_debate(debate=debate)
        current_round = get_current_round(debate=debate)
        if (
            bot_user
            and current_round
            and _is_user_turn(debate=debate, current_round=current_round, user=bot_user)
        ):
            bot_respond.apply_async(args=[debate_id], countdown=random.randint(10, 20))
