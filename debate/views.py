import logging

from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_simplejwt.authentication import JWTAuthentication

from base.decorators import handle_exception
from base.response import status_200, status_400

from debate.constants import DebateStatus
from debate.models import Judgement
from debate.selectors import (
    get_active_categories,
    get_active_topics,
    get_debates_by_status,
    get_messages_for_debate_and_user,
    get_user_debates,
    get_debate,
)
from debate.services import (
    dispute_judgement,
    get_user_debate_and_message,
    group_topics_by_category,
    serialize_category_and_debate_rules,
)
from debate.serializers import (
    DebateListSerializer,
    DebateDetailSerializer,
    JudgementSerializer,
    MessageSerializer,
)

logger = logging.getLogger(__name__)


class TopicListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        category_id = request.GET.get("category_id")
        topics = get_active_topics(category_id=category_id)
        data = group_topics_by_category(topics=topics)
        return status_200(message="Topics fetched", data={"topics": data})


class DebateListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        debates = get_user_debates(user=request.user)
        return status_200(
            message="Debates fetched",
            data={"debates": DebateListSerializer(debates, many=True).data},
        )


class OngoingDebateListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        debates = get_debates_by_status(status=DebateStatus.ONGOING)
        return status_200(
            message="Ongoing debates fetched",
            data={
                "debates": DebateListSerializer(debates, many=True).data,
            },
        )


class DebateDetailView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request, debate_id):
        debate = get_debate(debate_id=debate_id)
        if request.user not in (debate.user_pro, debate.user_con):
            logger.info(
                f"user_id={request.user.id} debate_id={debate_id} "
                f"denied debate detail: not a participant"
            )
            return status_400(message="You are not a participant in this debate")
        return status_200(
            message="Debate fetched", data=DebateDetailSerializer(debate).data
        )


class MyDebatesListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        debate_id = request.GET.get("debate_id")
        if debate_id:
            data = get_user_debate_and_message(user=request.user, debate_id=debate_id)
            return status_200(message="Fetch Debate Messages", data={"messages": data})

        debates = get_user_debates(user=request.user)
        return status_200(
            message="My debates fetched",
            data={"debates": DebateListSerializer(debates, many=True).data},
        )


class MessageListView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request, debate_id):
        if not debate_id:
            return status_400(message="Debate ID is required")

        messages = get_messages_for_debate_and_user(
            debate_id=debate_id, user_id=request.user.id
        )
        return status_200(
            message="Messages fetched",
            data={"messages": MessageSerializer(messages, many=True).data},
        )


class JudgementView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request, debate_id):
        debate = get_debate(debate_id=debate_id)
        if request.user not in (debate.user_pro, debate.user_con):
            logger.info(
                f"user_id={request.user.id} debate_id={debate_id} "
                f"denied judgement: not a participant"
            )
            return status_400(message="You are not a participant in this debate")
        try:
            judgement = Judgement.objects.select_related("winner").get(debate=debate)
        except Judgement.DoesNotExist:
            return status_400(message="Judgement not available yet")
        return status_200(
            message="Judgement fetched", data=JudgementSerializer(judgement).data
        )


class DisputeView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def post(self, request, debate_id):
        logger.info(
            f"user_id={request.user.id} debate_id={debate_id} dispute requested"
        )
        judgement = dispute_judgement(user=request.user, debate_id=debate_id)
        logger.info(
            f"user_id={request.user.id} debate_id={debate_id} "
            f"dispute processed judgement_id={judgement.id} winner_id={judgement.winner_id}"
        )
        return status_200(
            message="Dispute processed", data=JudgementSerializer(judgement).data
        )


class CategoryAndGroundRule(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAuthenticated]

    @handle_exception
    def get(self, request):
        categories = get_active_categories()
        categories, rules, debate_time = serialize_category_and_debate_rules(
            categories=categories
        )
        return status_200(
            message="Fetch Categories",
            data={"categories": categories, "rules": rules, "debate_time": debate_time},
        )
