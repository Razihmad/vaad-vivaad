import logging

from django.conf import settings
from django.contrib.auth.models import User
from rest_framework.permissions import AllowAny
from rest_framework.views import APIView

from authentication.services import (
    create_user_by_google_data,
    generate_google_login_url,
    get_jwt_access_token,
    get_user_data_from_google_code,
    get_user_data_from_google_id_token,
    refresh_access_token,
)
from base.decorators import handle_exception
from base.response import status_200, status_400

logger = logging.getLogger(__name__)


class GoogleLogin(APIView):
    @handle_exception
    def get(self, request, *args, **kwargs):
        login_url = generate_google_login_url()
        logger.info(f"google login url generated={login_url}")
        return status_200(message="Login successful", data={"url": login_url})

    @handle_exception
    def post(self, request, *args, **kwargs):
        code = request.GET.get("code", None)
        user_data = get_user_data_from_google_code(code=code)
        user, is_created = create_user_by_google_data(data=user_data)
        access_token, refresh_token = get_jwt_access_token(user=user)
        logger.info(
            f"user_id={user.id} username={user.username} is_new_user={is_created} "
            f"google login (code flow) successful"
        )
        return status_200(
            message="Login successful",
            data={
                "is_new_user": is_created,
                "username": user.username,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )


class GoogleLoginCallback(APIView):
    permission_classes = [AllowAny]

    @handle_exception
    def post(self, request, *args, **kwargs):
        id_token = request.data.get("id_token", None)
        user_data = get_user_data_from_google_id_token(id_token=id_token)
        user, is_created = create_user_by_google_data(data=user_data)
        access_token, refresh_token = get_jwt_access_token(user=user)
        logger.info(
            f"user_id={user.id} username={user.username} is_new_user={is_created} "
            f"google login (id_token flow) successful"
        )
        return status_200(
            message="Login successful",
            data={
                "is_new_user": is_created,
                "username": user.username,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )


class DevLoginView(APIView):
    """Dev-only: returns JWT tokens for any username. Only works when DEBUG=True."""

    permission_classes = [AllowAny]

    @handle_exception
    def post(self, request):
        if not settings.DEBUG:
            return status_400(message="Not available in production")
        username = request.data.get("username")
        if not username:
            return status_400(message="username is required")
        try:
            user = User.objects.get(username=username)
        except User.DoesNotExist:
            logger.warning(f"dev login failed: username={username!r} not found")
            return status_400(message=f"User '{username}' not found")
        access_token, refresh_token = get_jwt_access_token(user=user)
        logger.info(f"user_id={user.id} username={user.username} dev login successful")
        return status_200(
            message="Login successful",
            data={
                "is_new_user": False,
                "username": user.username,
                "access_token": access_token,
                "refresh_token": refresh_token,
            },
        )


class TokenRefreshView(APIView):
    @handle_exception
    def post(self, request):
        refresh_token = request.data.get("refresh_token")
        if not refresh_token:
            return status_400(message="refresh_token is required")
        access_token = refresh_access_token(refresh_token=refresh_token)
        return status_200(
            message="Token refreshed", data={"access_token": access_token}
        )
