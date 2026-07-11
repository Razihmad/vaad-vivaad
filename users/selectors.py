from typing import List

from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
from datetime import timedelta

from debate.constants import DebateStatus
from debate.models import Debate
from users.models import (
    ApplicationConfig,
    TopicComment,
    UserDevice,
    UserFeedback,
    UserProfile,
)

WEEKLY_WINDOW = timedelta(days=7)


def get_user_profile(*, user_id: int) -> UserProfile:
    return UserProfile.objects.get(user_id=user_id)


def get_active_user_devices(*, user_id: int) -> list[UserDevice]:
    return list(UserDevice.objects.filter(user_id=user_id, is_active=True))


def update_user_device_status(*, pk_ids: List[int], is_active: bool) -> None:
    UserDevice.objects.filter(id__in=pk_ids).update(is_active=is_active)


def get_user_feedbacks(*, user_id: int) -> List[UserFeedback]:
    return list(UserFeedback.objects.filter(user_id=user_id))


def create_user_feedback(
    *, user: User, feedback_type: str, title: str, message: str
) -> UserFeedback:
    return UserFeedback.objects.create(
        user=user,
        feedback_type=feedback_type,
        title=title,
        message=message,
    )


def create_topic_comment(
    *, user: User, topic_id: int, comment: str, side: str
) -> TopicComment:
    return TopicComment.objects.create(
        user=user,
        topic_id=topic_id,
        comment=comment,
        side=side,
    )


def get_application_config_by_name(*, name: str) -> ApplicationConfig:
    return ApplicationConfig.objects.filter(name=name, is_active=True).first()


def get_user_by_id(*, user_id: int) -> User:
    return User.objects.filter(id=user_id).first()


def get_weekly_active_user_ids() -> set[int]:
    since = timezone.now() - WEEKLY_WINDOW
    debates = Debate.objects.filter(status=DebateStatus.COMPLETED, completed_at__gte=since)
    pro_ids = debates.values_list("user_pro_id", flat=True)
    con_ids = debates.values_list("user_con_id", flat=True)
    return set(pro_ids) | set(con_ids)


def get_leaderboard(*, timeframe: str) -> List[UserProfile]:
    qs = UserProfile.objects.select_related("user")
    if timeframe == "weekly":
        qs = qs.filter(user_id__in=get_weekly_active_user_ids())
    return list(qs.order_by("-elo_rating"))


def update_user_profile_after_debate(
    *, winner_id: int, loser_id: int, winner_elo_delta: int, loser_elo_delta: int
) -> None:
    UserProfile.objects.filter(user_id=winner_id).update(
        elo_rating=models.F("elo_rating") + winner_elo_delta,
        wins=models.F("wins") + 1,
        total_debates=models.F("total_debates") + 1,
        streak=models.F("streak") + 1,
    )
    UserProfile.objects.filter(user_id=loser_id).update(
        elo_rating=models.F("elo_rating") - loser_elo_delta,
        losses=models.F("losses") + 1,
        total_debates=models.F("total_debates") + 1,
        streak=0,
    )
