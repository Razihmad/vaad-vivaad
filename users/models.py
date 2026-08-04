from django.db import models
from django.contrib.auth.models import User

from debate.constants import ProOrCon
from debate.models import Topic
from users.constants import DeviceType, FeedbackType


# Create your models here.
class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    elo_rating = models.IntegerField(default=1200)
    xp = models.IntegerField(default=0)
    total_debates = models.IntegerField(default=0)
    wins = models.IntegerField(default=0)
    bio = models.TextField()
    losses = models.IntegerField(default=0)
    streak = models.IntegerField(default=0)
    profile_pic = models.ImageField(upload_to="user_profiles/", null=True, blank=True)
    # optional for later
    is_bot = models.BooleanField(default=False)

    def __str__(self):
        return self.user.username


class UserDevice(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    device_id = models.CharField(max_length=255, unique=True)
    device_type = models.CharField(max_length=255, choices=DeviceType.choices)
    device_token = models.CharField(max_length=255)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)


class UserFeedback(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="feedbacks")
    feedback_type = models.CharField(
        max_length=50, choices=FeedbackType.choices, default=FeedbackType.GENERAL
    )
    title = models.CharField(max_length=255)
    message = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.feedback_type}"


class TopicComment(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="comments")
    user = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="topic_comments"
    )
    comment = models.TextField()
    side = models.CharField(max_length=20, choices=ProOrCon.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username} - {self.topic.title} ({self.side})"


class TopicVote(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="votes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="topic_votes")
    side = models.CharField(max_length=20, choices=ProOrCon.choices)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["topic", "user"], name="unique_topic_user_vote"
            )
        ]

    def __str__(self):
        return f"{self.user.username} - {self.topic.title} ({self.side})"


class ApplicationConfig(models.Model):
    name = models.CharField(max_length=256)
    properties = models.JSONField(default=dict)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name
