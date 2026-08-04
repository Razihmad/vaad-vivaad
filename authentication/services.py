# Standard Library
from typing import Dict, Optional, Tuple


# Local
import secrets

from authentication.selectors import (
    create_user,
    create_user_profile,
    get_taken_usernames,
    get_user_by_email,
)
from authentication.utils.google_authentication import google_oauth
from authentication.utils.username_generator import generate_username

# Third Party
from django.contrib.auth.models import User
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken

from base.exception import ServiceException
from users.constants import ApplicationConfigName
from users.selectors import get_application_config_by_name


MAX_USERNAME_ATTEMPTS = 10


def generate_google_login_url():
    return google_oauth.create_google_login_url()


def _generate_unique_username(*, fallback_email: str) -> str:
    candidates = list({generate_username() for _ in range(MAX_USERNAME_ATTEMPTS)})
    base = fallback_email.split("@")[0]
    candidates.append(base)
    candidates.extend(f"{base}{i}" for i in range(1, MAX_USERNAME_ATTEMPTS + 1))

    taken = get_taken_usernames(usernames=candidates)
    for candidate in candidates:
        if candidate not in taken:
            return candidate

    return f"{base}{secrets.token_hex(4)}"


def create_user_by_google_data(*, data: Dict) -> Tuple[User, bool]:
    email = data.pop("email", None)
    if not email:
        raise ServiceException("No email exists")

    existing_user = get_user_by_email(email=email)
    if existing_user:
        if not existing_user.is_active:
            raise ServiceException("User is blocked or deleted")
        return existing_user, False

    user_data = {
        "first_name": data.pop("given_name", ""),
        "last_name": data.pop("family_name", ""),
    }
    username = _generate_unique_username(fallback_email=email)
    user = create_user(email=email, username=username, extra_data=user_data)
    create_user_profile(user=user)
    return user, True


def get_jwt_access_token(*, user: User) -> Tuple[str, str]:
    refresh = RefreshToken.for_user(user)
    return str(refresh.access_token), str(refresh)


def refresh_access_token(*, refresh_token: str) -> str:
    try:
        token = RefreshToken(refresh_token)
        return str(token.access_token)
    except TokenError as e:
        raise ServiceException(str(e))


def get_user_data_from_google_code(*, code: Optional[str]) -> Dict:
    if not code:
        raise ServiceException("No code provided.")

    access_token = google_oauth.google_get_access_token(code=code)
    if not access_token:
        raise ServiceException("Failed to obtain access token from Google.")

    user_data = google_oauth.google_get_user_info(access_token=access_token)

    return user_data


def get_user_data_from_google_id_token(*, id_token: Optional[str]) -> Dict:
    if not id_token:
        raise ServiceException("No id_token provided.")

    return google_oauth.google_get_user_info_from_id_token(id_token=id_token)


def get_username_base_to_generate_usernames():
    config = get_application_config_by_name(
        name=ApplicationConfigName.USERNAME_BASE.value
    )
    return config.properties if config else {}
