from typing import Optional

from django.contrib.auth.models import User

from users.models import TopicComment, TopicVote, UserDevice, UserFeedback, UserProfile
from users.selectors import create_topic_comment, create_user_feedback


def create_feedback(
    *, user: User, feedback_type: str, title: str, message: str
) -> UserFeedback:
    return create_user_feedback(
        user=user, feedback_type=feedback_type, title=title, message=message
    )


def add_topic_comment(
    *, user: User, topic_id: int, comment: str, side: str
) -> TopicComment:
    return create_topic_comment(
        user=user,
        topic_id=topic_id,
        comment=comment,
        side=side,
    )


def cast_topic_vote(*, user: User, topic_id: int, side: str) -> Optional[TopicVote]:
    """Tapping the side you've already voted removes your vote; tapping the
    other side switches it. Mirrors the toggle behaviour the app's vote
    buttons already have."""
    existing = TopicVote.objects.filter(topic_id=topic_id, user=user).first()
    if existing and existing.side == side:
        existing.delete()
        return None
    vote, _ = TopicVote.objects.update_or_create(
        topic_id=topic_id, user=user, defaults={"side": side}
    )
    return vote


def register_device(
    *, user: User, device_id: str, device_type: str, device_token: str
) -> UserDevice:
    device, _ = UserDevice.objects.update_or_create(
        device_id=device_id,
        defaults={
            "user": user,
            "device_type": device_type,
            "device_token": device_token,
            "is_active": True,
        },
    )
    return device


def update_user_profile(
    *, user_id: int, username: str = None, bio: str = None, name: str = None, profile_pic=None
):
    user_updates = {}
    if username is not None:
        user_updates["username"] = username
    if name is not None:
        first, _, last = name.strip().partition(" ")
        user_updates["first_name"] = first
        user_updates["last_name"] = last
    if user_updates:
        User.objects.filter(id=user_id).update(**user_updates)

    profile = UserProfile.objects.get(user_id=user_id)
    profile_fields = []
    if bio is not None:
        profile.bio = bio
        profile_fields.append("bio")
    if profile_pic is not None:
        profile.profile_pic = profile_pic
        profile_fields.append("profile_pic")
    if profile_fields:
        profile.save(update_fields=profile_fields)
