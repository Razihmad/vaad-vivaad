from __future__ import annotations

from datetime import datetime
from django.utils import timezone
from typing import List, Optional

from django.db.models import Count, Q, QuerySet
from django.contrib.auth.models import User

from debate.models import (
    Category,
    Debate,
    DebateViewer,
    Judgement,
    MatchQueue,
    Message,
    Round,
    Topic,
    ViewerReaction,
)
from debate.constants import (
    DebateStatus,
    DebateViewerStatus,
    MatchQueueStatus,
    ProOrCon,
    RoundType,
)


# ── Topics & debates (read) ──────────────────────────────────────────────


def get_active_topics(*, category_id: Optional[int]) -> QuerySet[Topic]:
    query_filter = Q(is_active=True)
    if category_id:
        query_filter &= Q(category_id=category_id)

    return (
        Topic.objects.select_related("category")
        .filter(query_filter)
        .order_by("priority")
    )


def get_debate(*, debate_id: int) -> Debate:
    return Debate.objects.select_related("topic", "user_pro", "user_con", "winner").get(
        id=debate_id
    )


def get_user_debates(*, user: User) -> QuerySet[Debate]:
    query_filter = Q(user_pro=user) | Q(user_con=user)
    return (
        Debate.objects.filter(query_filter, status=DebateStatus.COMPLETED)
        .select_related("topic", "user_pro", "user_con", "winner")
        .order_by("-started_at")
    )


def get_debate_by_id(*, debate_id: int) -> Debate | None:
    return (
        Debate.objects.select_related("topic", "user_pro", "user_con", "winner")
        .filter(id=debate_id)
        .first()
    )


def get_debate_for_serializer(*, debate_id: int) -> Debate:
    return Debate.objects.select_related("topic", "user_pro", "user_con", "winner").get(
        id=debate_id
    )


# ── Rounds & messages (read) ─────────────────────────────────────────────


def get_current_round(*, debate: Debate) -> Round | None:
    return (
        Round.objects.filter(debate=debate, ended_at__isnull=True)
        .order_by("order")
        .first()
    )


def get_messages_for_round(*, round_obj: Round) -> QuerySet[Message]:
    return Message.objects.filter(round=round_obj)


def get_rounds_for_debate_ordered(*, debate: Debate) -> QuerySet[Round]:
    return Round.objects.filter(debate=debate).order_by("order")


def get_messages_for_debate_round_ordered(*, round_obj: Round) -> QuerySet[Message]:
    return Message.objects.filter(round=round_obj).order_by("created_at")


# ── Match queue (read) ──────────────────────────────────────────────────


def get_active_queue_entry(*, user: User) -> MatchQueue | None:
    return MatchQueue.objects.filter(user=user, status=MatchQueueStatus.PENDING).first()


def get_pending_queue_counts_by_side(
    *, topic_id: Optional[int], category_id: Optional[int]
) -> dict[str, int]:
    query_filter = Q(status=MatchQueueStatus.PENDING)
    if topic_id:
        query_filter &= Q(topic_id=topic_id)
    elif category_id:
        query_filter &= Q(topic__category_id=category_id)

    rows = (
        MatchQueue.objects.filter(query_filter)
        .values("pro_or_con")
        .annotate(count=Count("id"))
    )
    return {row["pro_or_con"]: row["count"] for row in rows}


def get_pending_match_for_topic(
    *, topic_id: int, exclude_user: User, pro_or_con: ProOrCon
) -> MatchQueue | None:
    opposite = ProOrCon.CON if pro_or_con == ProOrCon.PRO else ProOrCon.PRO
    return (
        MatchQueue.objects.select_for_update()
        .filter(topic_id=topic_id, status=MatchQueueStatus.PENDING, pro_or_con=opposite)
        .exclude(user=exclude_user)
        .first()
    )


def get_latest_queue_entry(*, user: User) -> MatchQueue | None:
    return (
        MatchQueue.objects.filter(
            user=user, status__in=[MatchQueueStatus.PENDING, MatchQueueStatus.MATCHED]
        )
        .select_related("topic", "debate")
        .order_by("-joined_at")
        .first()
    )


# ── Match queue (write) ────────────────────────────────────────────────


def get_topic_by_id(*, topic_id: int) -> Topic | None:
    return Topic.objects.filter(id=topic_id, is_active=True).first()


def get_topic_by_id_or_category_id(
    *,
    topic_id: Optional[int],
    category_id: Optional[int],
    pro_or_con: ProOrCon,
) -> Optional[Topic]:
    if topic_id:
        return get_topic_by_id(topic_id=topic_id)

    opposite_side = ProOrCon.CON if pro_or_con == ProOrCon.PRO else ProOrCon.PRO
    candidates = Topic.objects.filter(
        is_active=True,
        matchqueue__status=MatchQueueStatus.PENDING,
        matchqueue__pro_or_con=opposite_side,
    )
    if category_id:
        candidates = candidates.filter(category_id=category_id)
    topic = candidates.order_by("matchqueue__joined_at").first()
    if topic:
        return topic

    if category_id:
        return (
            Topic.objects.filter(category_id=category_id, is_active=True)
            .order_by("?")
            .first()
        )
    return Topic.objects.filter(is_active=True).order_by("?").first()


def create_match_queue_entry(
    *, user: User, topic: Topic, pro_or_con: ProOrCon, status: MatchQueueStatus
) -> MatchQueue:
    return MatchQueue.objects.create(
        user=user,
        topic=topic,
        pro_or_con=pro_or_con,
        status=status,
    )


def set_match_queue_entry_status(
    *, entry: MatchQueue, status: MatchQueueStatus
) -> None:
    entry.status = status
    entry.save(update_fields=["status"])


def create_debate_for_queue_match(
    *,
    topic: Topic,
    user: User,
    opponent_entry: MatchQueue,
    user_pro: User,
    user_con: User,
    pro_or_con_for_joiner: ProOrCon,
    matched_at: datetime,
) -> MatchQueue:
    """Creates Debate, opening round, updates opponent entry, and joiner's match-queue row."""
    debate = Debate.objects.create(
        topic=topic,
        user_pro=user_pro,
        user_con=user_con,
        status=DebateStatus.ONGOING,
    )
    Round.objects.create(
        debate=debate,
        round_type=RoundType.OPENING,
        order=1,
        started_at=matched_at,
        current_speaker=user_pro,
        turn_started_at=matched_at,
    )
    opponent_entry.status = MatchQueueStatus.MATCHED
    opponent_entry.matched = True
    opponent_entry.matched_at = matched_at
    opponent_entry.debate = debate
    opponent_entry.save(update_fields=["status", "matched", "matched_at", "debate"])
    return MatchQueue.objects.create(
        user=user,
        topic=topic,
        pro_or_con=pro_or_con_for_joiner,
        status=MatchQueueStatus.MATCHED,
        matched=True,
        matched_at=matched_at,
        debate=debate,
    )


# ── Messages & rounds (write) ───────────────────────────────────────────


def create_message_in_round(
    *, debate: Debate, round_obj: Round, user: User, content: str
) -> Message:
    return Message.objects.create(
        debate=debate,
        round=round_obj,
        user=user,
        content=content,
    )


def mark_round_ended(*, round_obj: Round, ended_at: datetime) -> None:
    round_obj.ended_at = ended_at
    round_obj.save(update_fields=["ended_at"])


def create_next_round(
    *,
    debate: Debate,
    round_type: RoundType,
    order: int,
    started_at: datetime,
    current_speaker: User,
) -> Round:
    return Round.objects.create(
        debate=debate,
        round_type=round_type,
        order=order,
        started_at=started_at,
        current_speaker=current_speaker,
        turn_started_at=started_at,
    )


def set_round_current_speaker(
    *, round_obj: Round, speaker: Optional[User], turn_started_at: Optional[datetime]
) -> None:
    round_obj.current_speaker = speaker
    round_obj.turn_started_at = turn_started_at
    round_obj.save(update_fields=["current_speaker", "turn_started_at"])


def user_has_message_in_round(*, round_obj: Round, user: User) -> bool:
    return Message.objects.filter(round=round_obj, user=user).exists()


# ── Judgements (write) ────────────────────────────────────────────────


def judgement_exists_for_debate(*, debate_id: int) -> bool:
    return Judgement.objects.filter(debate_id=debate_id).exists()


def apply_judgement_outcome(*, debate: Debate, data: dict) -> Judgement:
    from users.selectors import update_user_profile_after_debate

    Judgement.objects.filter(debate=debate).delete()
    winner_user = debate.user_pro if data["winner"] == "pro" else debate.user_con
    loser_user = debate.user_con if data["winner"] == "pro" else debate.user_pro

    overall_pro = (
        data["pro"]["argument_score"]
        + data["pro"]["rebuttal_score"]
        + data["pro"]["clarity_score"]
        + data["pro"]["persuasion_score"]
    ) / 4
    overall_con = (
        data["con"]["argument_score"]
        + data["con"]["rebuttal_score"]
        + data["con"]["clarity_score"]
        + data["con"]["persuasion_score"]
    ) / 4

    winner_overall = overall_pro if data["winner"] == "pro" else overall_con
    loser_overall = overall_con if data["winner"] == "pro" else overall_pro

    # ELO: winner gains, loser loses — both scaled by their avg score (1–10).
    # XP: always positive; winner earns full value, loser earns half (participation).
    winner_elo = round(winner_overall)
    loser_elo   = round(loser_overall)
    winner_xp   = max(5, round(winner_overall * 10))
    loser_xp    = max(3, round(loser_overall * 5))

    # Store per-side deltas so the result screen can show accurate numbers.
    pro_is_winner = data["winner"] == "pro"
    rating_delta_pro = winner_elo  if pro_is_winner else -loser_elo
    rating_delta_con = -loser_elo  if pro_is_winner else  winner_elo
    xp_delta_pro     = winner_xp   if pro_is_winner else  loser_xp
    xp_delta_con     = loser_xp    if pro_is_winner else  winner_xp

    judgement = Judgement.objects.create(
        debate=debate,
        winner=winner_user,
        argument_score_pro=data["pro"]["argument_score"],
        rebuttal_score_pro=data["pro"]["rebuttal_score"],
        clarity_score_pro=data["pro"]["clarity_score"],
        persuasion_score_pro=data["pro"]["persuasion_score"],
        argument_score_con=data["con"]["argument_score"],
        rebuttal_score_con=data["con"]["rebuttal_score"],
        clarity_score_con=data["con"]["clarity_score"],
        persuasion_score_con=data["con"]["persuasion_score"],
        overall_score_pro=overall_pro,
        overall_score_con=overall_con,
        rating_delta_pro=rating_delta_pro,
        rating_delta_con=rating_delta_con,
        xp_delta_pro=xp_delta_pro,
        xp_delta_con=xp_delta_con,
        reasoning=data["reasoning"],
        strongest_moment=data["strongest_moment"],
        coaching_tip_pro=data["coaching_tip_pro"],
        coaching_tip_con=data["coaching_tip_con"],
    )
    debate.status = DebateStatus.COMPLETED
    debate.winner = winner_user
    debate.completed_at = timezone.now()
    debate.save(update_fields=["status", "winner", "completed_at"])

    update_user_profile_after_debate(
        winner_id=winner_user.id,
        loser_id=loser_user.id,
        winner_elo_delta=winner_elo,
        loser_elo_delta=loser_elo,
        winner_xp_delta=winner_xp,
        loser_xp_delta=loser_xp,
    )

    return judgement


def set_debate_status(*, debate: Debate, status: DebateStatus) -> None:
    debate.status = status
    debate.save(update_fields=["status"])


def update_debate_status(*, debate_id: int, status: DebateStatus) -> None:
    Debate.objects.filter(id=debate_id).update(status=status)


def update_match_queue_status(
    *, user_id: int, status: MatchQueueStatus, debate_id: int
) -> None:
    MatchQueue.objects.filter(
        user_id=user_id, status=status, debate_id=debate_id
    ).update(status=status)


def get_debates_by_status(*, status: DebateStatus) -> QuerySet[Debate]:
    return (
        Debate.objects.select_related("topic", "user_pro", "user_con", "winner")
        .filter(status=status)
        .order_by("-started_at")
    )


def get_messages_for_debate_and_user(
    *, debate_id: int, user_id: int
) -> QuerySet[Message]:
    return (
        Message.objects.filter(debate_id=debate_id, user_id=user_id)
        .select_related("user", "round")
        .order_by("created_at")
    )


def get_or_create_debate_viewer(
    *, user: User, debate_id: int, status: DebateViewerStatus
) -> DebateViewer:
    return DebateViewer.objects.get_or_create(
        user=user,
        debate_id=debate_id,
        status=status,
    )


def update_debate_viewer_status(*, id: int, status: DebateViewerStatus):
    return DebateViewer.objects.filter(id=id).update(status=status)


def is_user_debate_viewer(*, user_id: int, debate_id: int) -> bool:
    return DebateViewer.objects.filter(
        user_id=user_id, debate_id=debate_id, status=DebateViewerStatus.JOINED
    ).exist()


def add_viewer_reaction(
    *, user_id: int, message_id: int, reaction: str
) -> ViewerReaction:
    return ViewerReaction.objects.create(
        user_id=user_id, message_id=message_id, reaction=reaction
    )


def get_active_categories() -> List[Category]:
    return Category.objects.filter(is_active=True)


def get_debate_by_user_and_id(*, user: User, debate_id: int) -> Debate:
    query_filter = Q(id=debate_id) & (Q(user_pro=user) | Q(user_con=user))
    return Debate.objects.filter(query_filter).first()


def get_messages_by_debate_id(*, debate_id: int) -> List[Message]:
    return Message.objects.select_related("round", "debate", "user").filter(
        debate_id=debate_id
    )
