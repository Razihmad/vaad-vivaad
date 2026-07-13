from django.db import models
from django.contrib.auth.models import User

from debate.constants import (
    MatchQueueStatus,
    ProOrCon,
    RoundType,
    DebateStatus,
    DebateViewerStatus,
    ViewerReactionType,
)


class Category(models.Model):
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    background_image = models.ImageField(
        upload_to="category_backgrounds/", null=True, blank=True
    )
    icon = models.ImageField(upload_to="category_icon/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name


class Topic(models.Model):
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    category = models.ForeignKey(Category, on_delete=models.CASCADE)
    priority = models.IntegerField(default=0)
    background_image = models.ImageField(
        upload_to="topic_backgrounds/", null=True, blank=True
    )
    icon = models.ImageField(upload_to="topic_icon/", null=True, blank=True)
    is_active = models.BooleanField(default=True)
    is_trending = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Debate(models.Model):
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    user_pro = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="pro_debates"
    )
    user_con = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="con_debates"
    )
    winner = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="won_debates",
    )
    status = models.CharField(max_length=20, choices=DebateStatus.choices)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    user_pro_disconnected_at = models.DateTimeField(null=True, blank=True)
    user_con_disconnected_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Debate #{self.id}: {self.topic.title}"


class Round(models.Model):
    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name="rounds")
    round_type = models.CharField(max_length=20, choices=RoundType.choices)
    order = models.IntegerField()
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    current_speaker = models.ForeignKey(
        User, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    turn_started_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Round {self.order} ({self.round_type}) — Debate #{self.debate_id}"


class Message(models.Model):
    debate = models.ForeignKey(
        Debate, on_delete=models.CASCADE, related_name="messages"
    )
    round = models.ForeignKey(Round, on_delete=models.CASCADE, related_name="messages")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(max_length=400)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message by {self.user.username} in Round {self.round.order}"


class Judgement(models.Model):
    debate = models.OneToOneField(Debate, on_delete=models.CASCADE)
    winner = models.ForeignKey(
        User, on_delete=models.CASCADE, related_name="judged_wins"
    )

    argument_score_pro = models.FloatField()
    rebuttal_score_pro = models.FloatField()
    clarity_score_pro = models.FloatField()
    persuasion_score_pro = models.FloatField()

    argument_score_con = models.FloatField()
    rebuttal_score_con = models.FloatField()
    clarity_score_con = models.FloatField()
    persuasion_score_con = models.FloatField()

    overall_score_pro = models.FloatField(default=0)
    overall_score_con = models.FloatField(default=0)

    xp_delta_pro = models.IntegerField(default=0)
    xp_delta_con = models.IntegerField(default=0)

    reasoning = models.TextField()
    strongest_moment = models.TextField()
    coaching_tip_pro = models.TextField()
    coaching_tip_con = models.TextField()

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Judgement for Debate #{self.debate_id}"


class MatchQueue(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    pro_or_con = models.CharField(max_length=20, choices=ProOrCon.choices)
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE)
    debate = models.ForeignKey(Debate, null=True, blank=True, on_delete=models.SET_NULL)
    matched = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=MatchQueueStatus.choices)
    matched_at = models.DateTimeField(null=True, blank=True)
    joined_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"MatchQueue for {self.user.username}"


class DebateViewer(models.Model):
    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name="viewers")
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    joined_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, choices=DebateViewerStatus.choices)
    left_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"DebateViewer for {self.user.username}"


class ViewerReaction(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    message = models.ForeignKey(Message, on_delete=models.CASCADE)
    reaction = models.CharField(max_length=20, choices=ViewerReactionType.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"ViewerReaction for {self.debate_viewer.user.username}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message"], name="unique_user_message_reaction"
            )
        ]
