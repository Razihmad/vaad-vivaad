from rest_framework import serializers
from django.contrib.auth.models import User
from users.models import TopicComment, UserDevice, UserFeedback, UserProfile


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name"]


class UserProfileSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = [
            "user",
            "elo_rating",
            "xp",
            "total_debates",
            "wins",
            "losses",
            "streak",
            "bio",
            "profile_pic",
        ]


class UserFeedbackSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserFeedback
        fields = ["id", "feedback_type", "title", "message", "created_at"]
        read_only_fields = ["id", "created_at"]


class UserDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserDevice
        fields = ["device_id", "device_type", "device_token"]


class TopicCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model = TopicComment
        fields = ["id", "topic", "comment", "side", "created_at"]
        read_only_fields = ["id", "created_at"]
