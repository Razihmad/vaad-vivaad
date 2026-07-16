from django.urls import path
from users.views import (
    DeviceRegistrationView,
    FeedbackView,
    GetUserProfileView,
    LeaderboardView,
    TopicCommentDetailView,
    TopicCommentView,
    TopicVoteView,
    UserProfileByIdView,
)

urlpatterns = [
    path("getProfile/", GetUserProfileView.as_view(), name="get-user-profile"),
    path("getProfile/<int:user_id>/", UserProfileByIdView.as_view(), name="get-user-profile-by-id"),
    path("leaderboard/", LeaderboardView.as_view(), name="leaderboard"),
    path("feedback/", FeedbackView.as_view(), name="feedback"),
    path("devices/register/", DeviceRegistrationView.as_view(), name="device-register"),
    path("topics/comments/", TopicCommentView.as_view(), name="topic-comment"),
    path("topics/comments/<int:comment_id>/", TopicCommentDetailView.as_view(), name="topic-comment-detail"),
    path("topics/votes/", TopicVoteView.as_view(), name="topic-vote"),
]
