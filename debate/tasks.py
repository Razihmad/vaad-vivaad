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
    import logging
    from debate.services import auto_judge_debate
    log = logging.getLogger(__name__)

    log.info("[JUDGE TASK] starting — debate=%s group=%s", debate_id, group_name)
    judgement = auto_judge_debate(debate_id=debate_id)
    if not judgement:
        log.warning("[JUDGE TASK] auto_judge_debate returned None — debate=%s", debate_id)
        return
    log.info("[JUDGE TASK] judgement created, sending to group=%s", group_name)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        group_name,
        {
            "type": "debate_result",
            "data": JudgementSerializer(judgement).data,
        },
    )
    log.info("[JUDGE TASK] group_send complete — debate=%s", debate_id)


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
