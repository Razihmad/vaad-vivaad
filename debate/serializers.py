from datetime import timedelta
from typing import Dict, List, Optional
from rest_framework import serializers
from django.contrib.auth.models import User
from django.utils import timezone

from debate.constants import RoundType
from debate.models import Debate, Round, Message, Judgement, MatchQueue, Topic, Category
from debate.selectors import get_time_remaining
from users.serializers import UserSerializer


class UserMinimalSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username"]


class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ["id", "name", "description", "background_image"]


class TopicSerializer(serializers.ModelSerializer):
    category = CategorySerializer(read_only=True)

    class Meta:
        model = Topic
        fields = ["id", "title", "description", "category", "background_image", "is_trending" , "pro_context", "con_context"]


class MessageSerializer(serializers.ModelSerializer):
    user = UserMinimalSerializer(read_only=True)

    class Meta:
        model = Message
        fields = ["id", "user", "content", "created_at", "round_id"]


class RoundSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    current_speaker_id = serializers.IntegerField(read_only=True, allow_null=True)

    class Meta:
        model = Round
        fields = [
            "id",
            "round_type",
            "order",
            "started_at",
            "ended_at",
            "current_speaker_id",
            "messages",
        ]


class DebateListSerializer(serializers.ModelSerializer):
    topic = TopicSerializer(read_only=True)
    user_pro = UserMinimalSerializer(read_only=True)
    user_con = UserMinimalSerializer(read_only=True)
    winner = UserMinimalSerializer(read_only=True)

    class Meta:
        model = Debate
        fields = [
            "id",
            "topic",
            "user_pro",
            "user_con",
            "winner",
            "status",
            "started_at",
            "completed_at",
        ]


class DebateDetailSerializer(serializers.ModelSerializer):
    topic = TopicSerializer(read_only=True)
    user_pro = UserMinimalSerializer(read_only=True)
    user_con = UserMinimalSerializer(read_only=True)
    winner = UserMinimalSerializer(read_only=True)
    rounds = RoundSerializer(many=True, read_only=True)

    class Meta:
        model = Debate
        fields = [
            "id",
            "topic",
            "user_pro",
            "user_con",
            "winner",
            "status",
            "started_at",
            "completed_at",
            "rounds",
        ]


class JudgementSerializer(serializers.ModelSerializer):
    winner = UserMinimalSerializer(read_only=True)

    class Meta:
        model = Judgement
        fields = [
            "id",
            "winner",
            "argument_score_pro",
            "rebuttal_score_pro",
            "clarity_score_pro",
            "persuasion_score_pro",
            "argument_score_con",
            "rebuttal_score_con",
            "clarity_score_con",
            "persuasion_score_con",
            "overall_score_pro",
            "overall_score_con",
            "xp_delta_pro",
            "xp_delta_con",
            "reasoning",
            "strongest_moment",
            "coaching_tip_pro",
            "coaching_tip_con",
            "created_at",
        ]


class QueueStatusSerializer(serializers.ModelSerializer):
    topic = TopicSerializer(read_only=True)
    debate_id = serializers.SerializerMethodField()

    class Meta:
        model = MatchQueue
        fields = ["id", "topic", "status", "joined_at", "matched_at", "debate_id"]

    def get_debate_id(self, obj):
        return obj.debate_id


class JoinQueueSerializer(serializers.Serializer):
    topic_id = serializers.IntegerField()


class SubmitMessageSerializer(serializers.Serializer):
    content = serializers.CharField(max_length=400, min_length=1)


class DebateViewerSerializer(serializers.Serializer):
    user = UserSerializer(read_only=True)
    debate_id = serializers.SerializerMethodField()

    class Meta:
        fields = ["id", "debate_id", "user", "status", "joined_at", "left_at"]

    def get_debate_id(self, obj):
        return obj.debate_id


def serialize_round_time(*, debate: Debate, round_obj: Optional[Round]) -> Optional[Dict]:
    """Server-authoritative rebuttal chess clock snapshot. None outside REBUTTAL (or once
    the round has ended) — the client's clock only runs during REBUTTAL."""
    if not round_obj or round_obj.round_type != RoundType.REBUTTAL or round_obj.ended_at:
        return None

    speaker_id = round_obj.current_speaker_id
    turn_deadline = None
    if speaker_id and round_obj.turn_started_at:
        speaker = debate.user_pro if speaker_id == debate.user_pro_id else debate.user_con
        remaining = get_time_remaining(debate=debate, user=speaker)
        turn_deadline = (
            round_obj.turn_started_at + timedelta(seconds=remaining)
        ).isoformat()

    return {
        "current_speaker_id": speaker_id,
        "turn_started_at": round_obj.turn_started_at.isoformat()
        if round_obj.turn_started_at
        else None,
        "turn_deadline": turn_deadline,
        "pro_time_remaining_seconds": debate.pro_time_remaining_seconds,
        "con_time_remaining_seconds": debate.con_time_remaining_seconds,
        "server_now": timezone.now().isoformat(),
    }


def serialize_messages_of_debate(*, messages: List[Message]) -> List[Dict]:
    rounds_by_id: Dict[int, Dict] = {}
    for message in messages:
        round_obj = message.round
        bucket = rounds_by_id.get(round_obj.id)
        if bucket is None:
            bucket = {
                "round_id": round_obj.id,
                "round_type": round_obj.round_type,
                "order": round_obj.order,
                "started_at": round_obj.started_at.isoformat()
                if round_obj.started_at
                else None,
                "ended_at": round_obj.ended_at.isoformat()
                if round_obj.ended_at
                else None,
                "messages": [],
            }
            rounds_by_id[round_obj.id] = bucket
        bucket["messages"].append(MessageSerializer(message).data)

    return sorted(rounds_by_id.values(), key=lambda r: r["order"])
