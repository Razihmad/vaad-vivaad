import logging
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from base.decorators import handle_exception
from base.response import status_200, status_400

from users.serializers import (
    TopicCommentSerializer,
    TopicVoteSerializer,
    UserDeviceSerializer,
    UserFeedbackSerializer,
    UserProfileSerializer,
)
from users.selectors import (
    get_leaderboard,
    get_topic_comments,
    get_topic_vote_summary,
    get_user_feedbacks,
    get_user_profile,
    get_user_profile_by_id,
)
from users.services import (
    add_topic_comment,
    cast_topic_vote,
    create_feedback,
    register_device,
    update_user_profile,
)


logger = logging.getLogger(__name__)


# Create your views here.
class GetUserProfileView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        user = request.user
        logger.info(f"{user.id=}")
        user_profile = get_user_profile(user_id=user.id)
        return status_200(
            message="User profile fetched",
            data={"user": UserProfileSerializer(user_profile).data},
        )

    @handle_exception
    def post(self, request):
        username = request.data.get("username")
        name = request.data.get("name")
        bio = request.data.get("bio")
        profile_pic = request.FILES.get("profile_pic")
        update_user_profile(
            name=name,
            username=username,
            bio=bio,
            user_id=request.user.id,
            profile_pic=profile_pic,
        )
        return status_200(message="Profile updated successfully")


class UserProfileByIdView(APIView):
    """Read-only: view another user's profile. No edit/logout — that's only
    ever done on your own profile via ``GetUserProfileView``."""

    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request, user_id):
        user_profile = get_user_profile_by_id(user_id=user_id)
        return status_200(
            message="User profile fetched",
            data={"user": UserProfileSerializer(user_profile).data},
        )


class FeedbackView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        feedbacks = get_user_feedbacks(user_id=request.user.id)
        return status_200(
            message="Feedbacks fetched",
            data={"feedbacks": UserFeedbackSerializer(feedbacks, many=True).data},
        )

    @handle_exception
    def post(self, request):
        serializer = UserFeedbackSerializer(data=request.data)
        if not serializer.is_valid():
            return status_400(message="Invalid data", data=serializer.errors)
        feedback = create_feedback(
            user=request.user,
            feedback_type=serializer.validated_data["feedback_type"],
            title=serializer.validated_data["title"],
            message=serializer.validated_data["message"],
        )
        return status_200(
            message="Feedback submitted",
            data={"feedback": UserFeedbackSerializer(feedback).data},
        )


class TopicCommentView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        topic_id = request.GET.get("topic_id")
        if not topic_id:
            return status_400(message="topic_id is required")
        comments = get_topic_comments(topic_id=topic_id)
        return status_200(
            message="Comments fetched",
            data={"comments": TopicCommentSerializer(comments, many=True).data},
        )

    @handle_exception
    def post(self, request):
        serializer = TopicCommentSerializer(data=request.data)
        if not serializer.is_valid():
            return status_400(message="Invalid data", data=serializer.errors)
        comment = add_topic_comment(
            user=request.user,
            topic_id=serializer.validated_data["topic"].id,
            comment=serializer.validated_data["comment"],
            side=serializer.validated_data["side"],
        )
        return status_200(
            message="Comment submitted",
            data={"comment": TopicCommentSerializer(comment).data},
        )


class TopicVoteView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        topic_id = request.GET.get("topic_id")
        if not topic_id:
            return status_400(message="topic_id is required")
        summary = get_topic_vote_summary(topic_id=topic_id, user_id=request.user.id)
        return status_200(message="Votes fetched", data=summary)

    @handle_exception
    def post(self, request):
        serializer = TopicVoteSerializer(data=request.data)
        if not serializer.is_valid():
            return status_400(message="Invalid data", data=serializer.errors)
        cast_topic_vote(
            user=request.user,
            topic_id=serializer.validated_data["topic"].id,
            side=serializer.validated_data["side"],
        )
        summary = get_topic_vote_summary(
            topic_id=serializer.validated_data["topic"].id, user_id=request.user.id
        )
        return status_200(message="Vote recorded", data=summary)


class LeaderboardView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        timeframe = request.query_params.get("timeframe", "all_time")
        if timeframe not in ("weekly", "all_time"):
            return status_400(message="timeframe must be 'weekly' or 'all_time'")
        profiles = get_leaderboard(timeframe=timeframe)
        players = [
            {"rank": i + 1, **UserProfileSerializer(profile).data}
            for i, profile in enumerate(profiles)
        ]
        return status_200(message="Leaderboard fetched", data={"players": players})


class DeviceRegistrationView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def post(self, request):
        serializer = UserDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return status_400(message="Invalid data", data=serializer.errors)
        device = register_device(
            user=request.user,
            device_id=serializer.validated_data["device_id"],
            device_type=serializer.validated_data["device_type"],
            device_token=serializer.validated_data["device_token"],
        )
        return status_200(
            message="Device registered",
            data={"device": UserDeviceSerializer(device).data},
        )
