from django.urls import path
from users.views import (
    DeviceRegistrationView,
    FeedbackView,
    GetUserProfileView,
    LeaderboardView,
    TopicCommentView,
)

urlpatterns = [
    path("getProfile/", GetUserProfileView.as_view(), name="get-user-profile"),
    path("leaderboard/", LeaderboardView.as_view(), name="leaderboard"),
    path("feedback/", FeedbackView.as_view(), name="feedback"),
    path("devices/register/", DeviceRegistrationView.as_view(), name="device-register"),
    path("topics/comments/", TopicCommentView.as_view(), name="topic-comment"),
]
