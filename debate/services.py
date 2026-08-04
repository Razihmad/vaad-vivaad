from functools import partial
from typing import Dict, List
import random
import logging
from typing import Optional

from django.utils import timezone
from django.db import transaction
from django.contrib.auth.models import User
from django.conf import settings

from base.events import send_queue_matched_event
from base.exception import ServiceException
from debate.constants import (
    DebateStatus,
    DebateViewerStatus,
    MatchQueueStatus,
    ProOrCon,
    RoundType,
)
from debate.models import (
    Category,
    Debate,
    DebateViewer,
    Judgement,
    Message,
    MatchQueue,
    Round,
    Topic,
)
from debate import selectors
from debate.serializers import (
    CategorySerializer,
    DebateListSerializer,
    TopicSerializer,
    serialize_messages_of_debate,
    serialize_round_time,
)
from debate.tasks import (
    check_rebuttal_deadline,
    start_judgement_of_debate_and_share_result,
)
from users.constants import ApplicationConfigName
from users.selectors import get_application_config_by_name

logger = logging.getLogger(__name__)

BOT_USERNAME = getattr(settings, "DEBATE_BOT_USERNAME", "vaad_bot")
BOT_QUEUE_WAIT_SECONDS = getattr(settings, "BOT_QUEUE_WAIT_SECONDS", 60)
DISCONNECT_GRACE_SECONDS = getattr(settings, "DISCONNECT_GRACE_SECONDS", 20)

JUDGE_MODEL_DEFAULT = "claude-haiku-4-5-20251001"
JUDGE_MODEL_ESCALATION = "claude-sonnet-4-6"

ROUND_SEQUENCE = [
    (RoundType.OPENING, 1),
    (RoundType.REBUTTAL, 2),
]


# ── Queue / Matchmaking ──────────────────────────────────────────────────────


@transaction.atomic
def join_queue(
    *,
    user: User,
    topic_id: Optional[int],
    category_id: Optional[int],
    pro_or_con: ProOrCon,
) -> MatchQueue:
    if selectors.get_active_queue_entry(user=user):
        raise ServiceException(message="You are already in the queue")

    topic = selectors.get_topic_by_id_or_category_id(
        topic_id=topic_id, category_id=category_id, pro_or_con=pro_or_con
    )
    if not topic:
        raise ServiceException(message="Topic not found or inactive")

    opponent_entry = selectors.get_pending_match_for_topic(
        topic_id=topic.id, exclude_user=user, pro_or_con=pro_or_con
    )
    if opponent_entry:
        return _create_match(
            user=user, opponent_entry=opponent_entry, topic=topic, pro_or_con=pro_or_con
        )

    entry = selectors.create_match_queue_entry(
        user=user, topic=topic, pro_or_con=pro_or_con, status=MatchQueueStatus.PENDING
    )
    bot_config = get_bot_config()
    bot_queue_wait_seconds = bot_config.get(
        "bot_queue_wait_sec", BOT_QUEUE_WAIT_SECONDS
    )
    # Schedule bot fallback — if no human joins within the wait window, match with bot
    from debate.tasks import assign_bot_if_no_match

    assign_bot_if_no_match.apply_async(
        args=[entry.id], countdown=bot_queue_wait_seconds
    )
    return entry


@transaction.atomic
def _create_match(
    *, user: User, opponent_entry: MatchQueue, topic: Topic, pro_or_con: ProOrCon
) -> MatchQueue:
    user_pro, user_con = (
        (user, opponent_entry.user)
        if pro_or_con == ProOrCon.PRO
        else (opponent_entry.user, user)
    )
    _, debate_time_seconds = get_debate_ground_rules()
    return selectors.create_debate_for_queue_match(
        topic=topic,
        user=user,
        opponent_entry=opponent_entry,
        user_pro=user_pro,
        user_con=user_con,
        pro_or_con_for_joiner=pro_or_con,
        matched_at=timezone.now(),
        debate_time_seconds=debate_time_seconds,
    )


def leave_queue(*, user: User) -> None:
    entry = selectors.get_active_queue_entry(user=user)
    if not entry:
        raise ServiceException(message="You are not in the queue")
    selectors.set_match_queue_entry_status(
        entry=entry, status=MatchQueueStatus.CANCELLED
    )


# ── Messages / Round progression ────────────────────────────────────────────


def submit_message_and_maybe_advance(
    *, user: User, debate_id: int, content: str
) -> tuple[Message, Optional[Round]]:
    """Opening: simultaneous — advance to Rebuttal once both players have sent 1 message.
    Rebuttal: unlimited messages until the user calls End Turn."""
    message = submit_message(user=user, debate_id=debate_id, content=content)
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate:
        return message, None
    current_round = selectors.get_current_round(debate=debate)
    if not current_round or current_round.round_type != RoundType.OPENING:
        return message, None
    next_round = _try_advance_from_opening(debate=debate, current_round=current_round)
    return message, next_round


@transaction.atomic
def _try_advance_from_opening(
    *, debate: Debate, current_round: Round
) -> Optional[Round]:
    """Atomically advance to Rebuttal once both players have sent their opening message."""
    round_locked = Round.objects.select_for_update().get(id=current_round.id)
    if round_locked.ended_at:
        return None  # Other player already triggered the advance
    pro_sent = Message.objects.filter(round=round_locked, user=debate.user_pro).exists()
    con_sent = Message.objects.filter(round=round_locked, user=debate.user_con).exists()
    if not (pro_sent and con_sent):
        return None
    now = timezone.now()
    selectors.mark_round_ended(round_obj=round_locked, ended_at=now)
    # Whoever sent their opening first opens REBUTTAL
    first_msg = (
        Message.objects.filter(round=round_locked).order_by("created_at").first()
    )
    first_speaker = (
        first_msg.user
        if first_msg
        else _speaker_order(debate=debate, round_type=RoundType.REBUTTAL)[0]
    )
    next_round = selectors.create_next_round(
        debate=debate,
        round_type=RoundType.REBUTTAL,
        order=2,
        started_at=now,
        current_speaker=first_speaker,
    )
    selectors.start_rebuttal_clock(debate=debate, seconds=debate.debate_time_seconds)
    _schedule_rebuttal_deadline(
        debate=debate, round_obj=next_round, speaker=first_speaker
    )
    return next_round


def submit_message(*, user: User, debate_id: int, content: str) -> Message:
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate:
        raise ServiceException(message="Debate not found")

    if debate.status != DebateStatus.ONGOING:
        raise ServiceException(message="This debate is not active")

    if user not in (debate.user_pro, debate.user_con):
        raise ServiceException(message="You are not a participant in this debate")

    current_round = selectors.get_current_round(debate=debate)
    if not current_round:
        raise ServiceException(message="No active round found")

    if current_round.round_type == RoundType.OPENING:
        if selectors.user_has_message_in_round(round_obj=current_round, user=user):
            raise ServiceException(
                message="You've already submitted your opening statement"
            )
    elif not _is_user_turn(debate=debate, current_round=current_round, user=user):
        raise ServiceException(message="It is not your turn to submit")

    message = selectors.create_message_in_round(
        debate=debate, round_obj=current_round, user=user, content=content
    )
    # In REBUTTAL: toggle the floor to the opponent after each human message
    if current_round.round_type == RoundType.REBUTTAL:
        opponent = debate.user_con if user.id == debate.user_pro.id else debate.user_pro
        now = timezone.now()
        elapsed = (
            (now - current_round.turn_started_at).total_seconds()
            if current_round.turn_started_at
            else 0.0
        )
        selectors.deduct_time_remaining(
            debate=debate, user=user, elapsed_seconds=elapsed
        )
        selectors.set_round_current_speaker(
            round_obj=current_round, speaker=opponent, turn_started_at=now
        )
        _schedule_rebuttal_deadline(
            debate=debate, round_obj=current_round, speaker=opponent
        )
    return message


def end_turn(*, user: User, debate_id: int) -> Optional[Round]:
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate:
        raise ServiceException(message="Debate not found")

    if debate.status != DebateStatus.ONGOING:
        raise ServiceException(message="This debate is not active")

    if user not in (debate.user_pro, debate.user_con):
        raise ServiceException(message="You are not a participant in this debate")

    current_round = selectors.get_current_round(debate=debate)
    if not current_round:
        raise ServiceException(message="No active round found")

    if current_round.round_type != RoundType.REBUTTAL:
        raise ServiceException(message="You can only end your turn during rebuttal")

    if not _is_user_turn(debate=debate, current_round=current_round, user=user):
        raise ServiceException(message="It is not your turn")

    if not selectors.user_has_message_in_round(round_obj=current_round, user=user):
        raise ServiceException(
            message="You must send at least one message before ending your turn"
        )

    # End REBUTTAL and trigger judgment
    selectors.mark_round_ended(round_obj=current_round, ended_at=timezone.now())
    group_name = f"debate_{debate.id}"
    start_judgement_of_debate_and_share_result.apply_async(
        args=[debate.id, group_name], countdown=20
    )
    return None


def expire_rebuttal_turn(
    *,
    debate_id: int,
    round_id: int,
    expected_turn_started_at: str,
    timed_out_user_id: int,
) -> bool:
    """Fires when a scheduled rebuttal deadline elapses. No-ops if the turn already
    moved on (a message arrived, End Turn was called, or the debate otherwise ended)
    before the deadline fired — same staleness guard as
    abandon_debate_if_still_disconnected. Returns True if the debate was genuinely
    timed out by this call."""
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate or debate.status != DebateStatus.ONGOING:
        return False

    current_round = selectors.get_current_round(debate=debate)
    if not current_round or current_round.id != round_id or current_round.ended_at:
        return False
    if (
        current_round.turn_started_at is None
        or current_round.turn_started_at.isoformat() != expected_turn_started_at
    ):
        return False

    timed_out_side = (
        ProOrCon.PRO if timed_out_user_id == debate.user_pro_id else ProOrCon.CON
    )
    selectors.mark_round_ended(round_obj=current_round, ended_at=timezone.now())
    selectors.set_timed_out_side(debate=debate, side=timed_out_side)
    group_name = f"debate_{debate.id}"
    start_judgement_of_debate_and_share_result.apply_async(
        args=[debate.id, group_name], countdown=5
    )
    return True


def _schedule_rebuttal_deadline(
    *, debate: Debate, round_obj: Round, speaker: User
) -> None:
    """(Re)schedules the deadline check for whoever's turn it now is. A stale check from
    a previous turn no-ops in expire_rebuttal_turn since turn_started_at will no longer
    match by the time it fires."""
    remaining = selectors.get_time_remaining(debate=debate, user=speaker)
    check_rebuttal_deadline.apply_async(
        args=[
            debate.id,
            round_obj.id,
            round_obj.turn_started_at.isoformat(),
            speaker.id,
            f"debate_{debate.id}",
        ],
        countdown=max(0, remaining),
    )


def _is_user_turn(*, debate: Debate, current_round: Round, user: User) -> bool:
    return current_round.current_speaker_id == user.id


def _speaker_order(*, debate: Debate, round_type: RoundType) -> list[User]:
    return [debate.user_pro, debate.user_con]


def _next_speaker_in_round(*, debate: Debate, current_round: Round) -> Optional[User]:
    order = _speaker_order(debate=debate, round_type=current_round.round_type)
    try:
        idx = next(
            i for i, u in enumerate(order) if u.id == current_round.current_speaker_id
        )
    except StopIteration:
        return None
    return order[idx + 1] if idx + 1 < len(order) else None


def _advance_after_turn(
    *, debate: Debate, current_round: Round, ender: User
) -> Optional[Round]:
    now = timezone.now()
    next_speaker = _next_speaker_in_round(debate=debate, current_round=current_round)
    if next_speaker:
        selectors.set_round_current_speaker(
            round_obj=current_round, speaker=next_speaker, turn_started_at=now
        )
        return None

    selectors.set_round_current_speaker(
        round_obj=current_round, speaker=None, turn_started_at=None
    )
    selectors.mark_round_ended(round_obj=current_round, ended_at=now)

    next_blocks = [
        (rt, order) for rt, order in ROUND_SEQUENCE if order > current_round.order
    ]
    if not next_blocks:
        group_name = f"debate_{debate.id}"
        start_judgement_of_debate_and_share_result.apply_async(
            args=[debate.id, group_name], countdown=20
        )
        return None
    next_type, next_order = next_blocks[0]
    first_speaker = _speaker_order(debate=debate, round_type=next_type)[0]
    return selectors.create_next_round(
        debate=debate,
        round_type=next_type,
        order=next_order,
        started_at=now,
        current_speaker=first_speaker,
    )


# ── AI Judging ───────────────────────────────────────────────────────────────


def _build_transcript(debate: Debate) -> str:
    lines = [
        f"Topic: {debate.topic.title}",
        f"Pro side: {debate.user_pro.username}",
        f"Con side: {debate.user_con.username}",
        "",
    ]
    for r in selectors.get_rounds_for_debate_ordered(debate=debate):
        lines.append(f"[Round {r.order} — {r.round_type}]")
        for msg in selectors.get_messages_for_debate_round_ordered(round_obj=r):
            side = "Pro" if msg.user == debate.user_pro else "Con"
            lines.append(f"{side}: {msg.content}")
        lines.append("")
    if debate.timed_out_side:
        side_name = "Pro" if debate.timed_out_side == ProOrCon.PRO else "Con"
        lines.append(
            f"[Note: {side_name}'s rebuttal clock ran out before they could respond further.]"
        )
    return "\n".join(lines)


def _call_judge(*, debate: Debate) -> dict:
    from debate.utils.claude_client import judge_client

    transcript = _build_transcript(debate=debate)
    config = get_debate_judge_config()
    return judge_client.judge(transcript=transcript, judge_config=config)


def dispute_judgement(*, user: User, debate_id: int) -> Judgement:
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate:
        raise ServiceException(message="Debate not found")

    if user not in (debate.user_pro, debate.user_con):
        raise ServiceException(message="You are not a participant in this debate")

    if debate.status != DebateStatus.COMPLETED:
        raise ServiceException(message="Can only dispute a completed debate")

    selectors.set_debate_status(debate=debate, status=DebateStatus.DISPUTED)
    try:
        data = _call_judge(debate=debate)
        return selectors.apply_judgement_outcome(debate=debate, data=data)
    except Exception as e:
        logger.error(
            "Dispute judging failed for debate %s: %s", debate.id, e, exc_info=True
        )
        selectors.set_debate_status(debate=debate, status=DebateStatus.COMPLETED)
        raise ServiceException(
            message="Dispute judging failed, please try again"
        ) from e


def auto_judge_debate(*, debate_id: int) -> Optional[Judgement]:
    """Called automatically at the end of every debate round sequence, and also to
    judge whatever transcript exists after a debate is abandoned mid-way."""
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate or debate.status not in (
        DebateStatus.ONGOING,
        DebateStatus.ABANDONED,
    ):
        return None
    try:
        data = _call_judge(debate=debate)
        return selectors.apply_judgement_outcome(debate=debate, data=data)
    except Exception as e:
        logger.error(
            "Auto judging failed for debate %s: %s", debate.id, e, exc_info=True
        )
        return None


# ── Disconnect handling ──────────────────────────────────────────────────────


def abandon_debate_and_schedule_judgement(*, debate_id: int, group_name: str) -> None:
    """Ends a MATCHED/ONGOING debate early — a participant left or dropped for good.
    Marks it ABANDONED and judges whatever transcript exists so far, same as a natural
    end of round sequence."""
    if not selectors.abandon_ongoing_debate(debate_id=debate_id):
        return
    if selectors.judgement_exists_for_debate(debate_id=debate_id):
        return
    start_judgement_of_debate_and_share_result.apply_async(
        args=[debate_id, group_name], countdown=5
    )


def handle_ongoing_debate_disconnect(
    *, user: User, debate_id: int, group_name: str, graceful: bool
) -> str:
    """Called when a participant's WebSocket for an ONGOING debate closes.

    ``graceful`` (a clean close, e.g. the client deliberately left) abandons the debate
    immediately. Anything else (lost internet, app killed, tab crash) only starts a
    grace-period countdown — the debate is abandoned only if the user hasn't rejoined by
    the time ``abandon_debate_if_still_disconnected`` fires.

    Returns "abandoned", "grace_period", or "noop" so the consumer knows what, if
    anything, to tell the opponent.
    """
    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate or debate.status not in (DebateStatus.MATCHED, DebateStatus.ONGOING):
        return "noop"
    if user.id not in (debate.user_pro_id, debate.user_con_id):
        return "noop"

    if graceful:
        abandon_debate_and_schedule_judgement(
            debate_id=debate_id, group_name=group_name
        )
        return "abandoned"

    disconnected_at = timezone.now()
    selectors.set_participant_disconnected_at(
        debate=debate, user_id=user.id, disconnected_at=disconnected_at
    )
    from debate.tasks import abandon_debate_if_still_disconnected

    abandon_debate_if_still_disconnected.apply_async(
        args=[debate_id, user.id, disconnected_at.isoformat(), group_name],
        countdown=DISCONNECT_GRACE_SECONDS,
    )
    return "grace_period"


def rejoin_active_debate(*, user: User, debate_id: int) -> dict:
    """Called when a client reconnects mid-debate and supplies the debate_id it was in
    (persisted locally before the drop). Clears the user's pending disconnect marker and
    returns the same shape ``join_queue_outcome`` returns for a fresh match, so the
    consumer can reuse ``process_join_queue_outcome`` to rejoin the debate group."""
    debate = selectors.get_active_debate_for_user_and_id(
        user_id=user.id, debate_id=debate_id
    )
    logger.info(f"{debate=}, {user.id=}")
    if not debate:
        raise ServiceException(message="Debate not found or no longer active")
    selectors.clear_participant_disconnected_at(debate_id=debate.id, user_id=user.id)
    opponent_id = (
        debate.user_con_id if user.id == debate.user_pro_id else debate.user_pro_id
    )
    messages = selectors.get_messages_by_debate_id(debate_id=debate.id)
    current_round = selectors.get_current_round(debate=debate)
    return {
        "outcome": "matched",
        "opponent_id": opponent_id,
        "debate": DebateListSerializer(debate).data,
        "reconnected": True,
        "rounds": serialize_messages_of_debate(messages=messages),
        "round_time": serialize_round_time(debate=debate, round_obj=current_round),
    }


def join_queue_outcome(
    *,
    user: User,
    pro_or_con: ProOrCon,
    topic_id: Optional[int],
    category_id: Optional[int],
) -> dict:
    entry = join_queue(
        user=user,
        topic_id=topic_id,
        pro_or_con=pro_or_con,
        category_id=category_id,
    )
    if entry.status == MatchQueueStatus.MATCHED and entry.debate_id:
        debate = selectors.get_debate_by_id(debate_id=entry.debate_id)
        if not debate:
            raise ServiceException(message="Debate not found")
        opponent_id = (
            debate.user_con_id if user.id == debate.user_pro_id else debate.user_pro_id
        )
        return {
            "outcome": "matched",
            "opponent_id": opponent_id,
            "debate": DebateListSerializer(debate).data,
        }
    return {
        "outcome": "waiting",
        "queue_id": entry.id,
        "topic": TopicSerializer(entry.topic).data,
    }


def get_pro_or_con(
    *, pro_or_con: Optional[str], topic_id: Optional[int], category_id: Optional[int]
) -> ProOrCon:
    if pro_or_con:
        return ProOrCon(pro_or_con.upper())

    counts = selectors.get_pending_queue_counts_by_side(
        topic_id=topic_id, category_id=category_id
    )
    pending_pro = counts.get(ProOrCon.PRO.value, 0)
    pending_con = counts.get(ProOrCon.CON.value, 0)
    if pending_pro > pending_con:
        return ProOrCon.CON
    if pending_con > pending_pro:
        return ProOrCon.PRO
    return ProOrCon.PRO if random.random() < 0.5 else ProOrCon.CON


def group_topics_by_category(*, topics: list[Topic]) -> Dict:
    data = TopicSerializer(topics, many=True).data
    result: Dict = {}
    for topic_data in data:
        category = topic_data["category"]
        category_name = category["name"]
        if category_name not in result:
            result[category_name] = {
                "description": category["description"],
                "background_image": category["background_image"],
                "topics": [],
            }
        result[category_name]["topics"].append(topic_data)
    return result


def create_debate_viewer(*, user: User, debate_id: int) -> DebateViewer:
    debate_viewer, is_created = selectors.get_or_create_debate_viewer(
        user=user, debate_id=debate_id, status=DebateViewerStatus.JOINED
    )
    if not is_created:
        raise ServiceException(message="You are already a viewer of this debate")
    return debate_viewer


def check_and_add_user_reaction(
    *, user: User, reaction: str, message_id: int, debate_id
):
    if not selectors.is_user_debate_viewer(user_id=user.id, debate_id=debate_id):
        raise ServiceException("You are not the viewer for this debate")

    return selectors.add_viewer_reaction(
        user_id=user.id, reaction=reaction, message_id=message_id
    )


def get_debate_judge_config():
    config = get_application_config_by_name(
        name=ApplicationConfigName.DEBATE_JUDGE.value
    )
    return config.properties if config else {}


# ── Bot matchmaking & AI responses ──────────────────────────────────────────


def get_or_create_bot_user() -> User:
    user, created = User.objects.get_or_create(
        username=BOT_USERNAME,
        defaults={
            "first_name": "Alex",
            "email": f"{BOT_USERNAME}@vaadvivaad.internal",
            "is_active": True,
        },
    )
    logger.info(f"{user.username=} {user.id=}, {created=}")
    if created:
        from users.models import UserProfile

        UserProfile.objects.create(user=user, is_bot=True)
    return user


def get_bot_user_in_debate(*, debate: Debate) -> Optional[User]:
    from users.models import UserProfile

    profile = (
        UserProfile.objects.filter(
            user__in=[debate.user_pro_id, debate.user_con_id], is_bot=True
        )
        .select_related("user")
        .first()
    )
    return profile.user if profile else None


def _build_bot_history(*, debate: Debate, bot_user: User) -> list[str]:
    lines = []
    for r in selectors.get_rounds_for_debate_ordered(debate=debate):
        for msg in selectors.get_messages_for_debate_round_ordered(round_obj=r):
            speaker = "You" if msg.user == bot_user else "Opponent"
            lines.append(f"[{r.round_type}] {speaker}: {msg.content}")
    return lines


@transaction.atomic
def _atomic_bot_submit(
    *, debate: Debate, bot_user: User, argument: str
) -> Optional[tuple[Message, Optional[Round]]]:
    """Lock the current round before writing to prevent concurrent bot submissions."""
    current_round = (
        Round.objects.select_for_update()
        .filter(debate=debate, ended_at__isnull=True)
        .order_by("order")
        .first()
    )
    if not current_round:
        return None

    is_bot_formal_turn = _is_user_turn(
        debate=debate, current_round=current_round, user=bot_user
    )

    if current_round.round_type == RoundType.OPENING:
        # OPENING: simultaneous — bot sends once, whenever it's ready
        if Message.objects.filter(round=current_round, user=bot_user).exists():
            return None
    else:
        # REBUTTAL: strict turn order — only send when it's the bot's formal turn
        if not is_bot_formal_turn:
            return None

    message = selectors.create_message_in_round(
        debate=debate, round_obj=current_round, user=bot_user, content=argument
    )

    if current_round.round_type == RoundType.OPENING:
        # After bot sends its opener, check if the human has already sent theirs.
        # If both have now sent, advance to REBUTTAL (same logic as _try_advance_from_opening
        # but inlined here since we already hold the select_for_update lock on this round).
        pro_sent = Message.objects.filter(
            round=current_round, user=debate.user_pro
        ).exists()
        con_sent = Message.objects.filter(
            round=current_round, user=debate.user_con
        ).exists()
        if pro_sent and con_sent:
            now = timezone.now()
            selectors.mark_round_ended(round_obj=current_round, ended_at=now)
            first_msg = (
                Message.objects.filter(round=current_round)
                .order_by("created_at")
                .first()
            )
            first_speaker = first_msg.user if first_msg else debate.user_pro
            next_round = selectors.create_next_round(
                debate=debate,
                round_type=RoundType.REBUTTAL,
                order=2,
                started_at=now,
                current_speaker=first_speaker,
            )
            selectors.start_rebuttal_clock(
                debate=debate, seconds=debate.debate_time_seconds
            )
            _schedule_rebuttal_deadline(
                debate=debate, round_obj=next_round, speaker=first_speaker
            )
            return message, next_round
        return message, None

    # REBUTTAL: hand the floor back to the human after bot sends
    human_user = (
        debate.user_con if bot_user.id == debate.user_pro.id else debate.user_pro
    )
    now = timezone.now()
    elapsed = (
        (now - current_round.turn_started_at).total_seconds()
        if current_round.turn_started_at
        else 0.0
    )
    selectors.deduct_time_remaining(
        debate=debate, user=bot_user, elapsed_seconds=elapsed
    )
    selectors.set_round_current_speaker(
        round_obj=current_round, speaker=human_user, turn_started_at=now
    )
    _schedule_rebuttal_deadline(
        debate=debate, round_obj=current_round, speaker=human_user
    )
    return message, None


def generate_and_submit_bot_message(
    *, debate_id: int
) -> Optional[tuple[Message, Optional[Round]]]:
    from debate.utils.bot_client import bot_client

    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate or debate.status != DebateStatus.ONGOING:
        return None

    bot_user = get_bot_user_in_debate(debate=debate)
    if not bot_user:
        return None

    current_round = selectors.get_current_round(debate=debate)
    if not current_round:
        return None

    # OPENING: bot sends once whenever ready (simultaneous, no turn order)
    # REBUTTAL: always attempt (the "no double-send" guard is inside _atomic_bot_submit)
    if current_round.round_type == RoundType.OPENING:
        if selectors.user_has_message_in_round(round_obj=current_round, user=bot_user):
            return None

    side = "PRO" if bot_user == debate.user_pro else "CON"
    history = _build_bot_history(debate=debate, bot_user=bot_user)

    argument = bot_client.generate(
        topic=debate.topic.title,
        description=debate.topic.description,
        side=side,
        round_type=current_round.round_type,
        history=history,
    )

    return _atomic_bot_submit(debate=debate, bot_user=bot_user, argument=argument)


def schedule_bot_response_if_needed(*, debate_id: int) -> None:
    from debate.tasks import bot_respond

    debate = selectors.get_debate_by_id(debate_id=debate_id)
    if not debate or debate.status != DebateStatus.ONGOING:
        return

    bot_user = get_bot_user_in_debate(debate=debate)
    if not bot_user:
        return

    current_round = selectors.get_current_round(debate=debate)
    if not current_round:
        return

    if current_round.round_type == RoundType.REBUTTAL:
        # Only schedule when it's genuinely the bot's turn (current_speaker was just toggled to bot)
        if _is_user_turn(debate=debate, current_round=current_round, user=bot_user):
            bot_respond.apply_async(args=[debate_id], countdown=random.randint(10, 20))
    elif current_round.round_type == RoundType.OPENING:
        # OPENING is simultaneous: schedule bot if it hasn't sent its opener yet.
        if not selectors.user_has_message_in_round(
            round_obj=current_round, user=bot_user
        ):
            bot_respond.apply_async(args=[debate_id], countdown=random.randint(10, 20))


@transaction.atomic
def match_with_bot(*, queue_id: int) -> None:
    entry = (
        MatchQueue.objects.select_for_update()
        .filter(id=queue_id, status=MatchQueueStatus.PENDING)
        .first()
    )
    if not entry:
        return  # Already matched or cancelled by the time the task fires

    bot_user = get_or_create_bot_user()
    # Respect the user's requested side; bot takes the other side
    if entry.pro_or_con == ProOrCon.PRO:
        user_pro = entry.user
        user_con = bot_user
    else:
        user_pro = bot_user
        user_con = entry.user
    now = timezone.now()
    _, debate_time_seconds = get_debate_ground_rules()

    debate = Debate.objects.create(
        topic=entry.topic,
        user_pro=user_pro,
        user_con=user_con,
        status=DebateStatus.ONGOING,
        debate_time_seconds=debate_time_seconds,
    )
    Round.objects.create(
        debate=debate,
        round_type=RoundType.OPENING,
        order=1,
        started_at=now,
        current_speaker=user_pro,
        turn_started_at=now,
    )
    entry.status = MatchQueueStatus.MATCHED
    entry.matched = True
    entry.matched_at = now
    entry.debate = debate
    entry.save(update_fields=["status", "matched", "matched_at", "debate"])

    bot_side = ProOrCon.CON if entry.pro_or_con == ProOrCon.PRO else ProOrCon.PRO
    MatchQueue.objects.create(
        user=bot_user,
        topic=entry.topic,
        pro_or_con=bot_side,
        status=MatchQueueStatus.MATCHED,
        matched=True,
        matched_at=now,
        debate=debate,
    )
    transaction.on_commit(partial(send_queue_matched_event, entry, debate))
    # Bot sends its OPENING argument first so the user has something to respond to immediately
    from debate.tasks import bot_respond

    transaction.on_commit(
        lambda: bot_respond.apply_async(
            args=[debate.id], countdown=random.randint(10, 20)
        )
    )


def get_debate_ground_rules():
    config = get_application_config_by_name(
        name=ApplicationConfigName.DEBATE_GROUND_RULES.value
    )
    properties = config.properties if config else {}
    rules = properties.get("rules", [])
    debate_time = properties.get("debate_time", 180)
    return rules, debate_time


def serialize_category_and_debate_rules(*, categories: List[Category]):
    categories_data = CategorySerializer(categories, many=True).data
    debate_rules, debate_time = get_debate_ground_rules()
    return categories_data, debate_rules, debate_time


def get_user_debate_and_message(*, user: User, debate_id: int):
    debate = selectors.get_debate_by_user_and_id(user=user, debate_id=debate_id)
    if not debate:
        raise ServiceException("This debate does not exist")
    messages = selectors.get_messages_by_debate_id(debate_id=debate_id)
    messages_data = serialize_messages_of_debate(messages=messages)
    return messages_data


def get_bot_config() -> Dict:
    config = get_application_config_by_name(name=ApplicationConfigName.BOT_USER.value)
    return config.properties if config else {}
