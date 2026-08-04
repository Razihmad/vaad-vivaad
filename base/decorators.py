import logging
import functools
from collections.abc import Awaitable, Callable

from base.exception import ServiceException
from base.response import status_400, status_500

logger = logging.getLogger(__name__)


def handle_exception(func: callable) -> callable:
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        request = args[1] if len(args) > 1 else None
        user_id = getattr(getattr(request, "user", None), "id", None)
        view_name = type(args[0]).__name__ if args else func.__qualname__
        method = getattr(request, "method", None)
        path = getattr(request, "path", None)
        log_prefix = f"[{view_name}] user_id={user_id} method={method} path={path}"

        logger.info(f"{log_prefix} request started")
        try:
            response = func(*args, **kwargs)
            logger.info(
                f"{log_prefix} status={getattr(response, 'status_code', None)} "
                f"request completed"
            )
            return response
        except ServiceException as e:
            logger.warning(
                f"{log_prefix} service_exception message={e.message!r} "
                f"error_code={e.error_code}"
            )
            return status_400(message=e.message)
        except Exception as e:
            logger.error(f"{log_prefix} unexpected error: {e=}", exc_info=True)
            return status_500(message="Something went wrong")

    return wrapper


def websocket_catch_service_exception(default_message: str = "Request failed"):
    """
    Async consumer decorator: on ServiceException, call ``self._send_error(...)`` and
    return. For async methods of ``AsyncWebsocketConsumer`` subclasses.
    """

    def decorator(
        method: Callable[..., Awaitable],
    ) -> Callable[..., Awaitable]:
        @functools.wraps(method)
        async def wrapper(self, *args, **kwargs):
            user_id = getattr(getattr(self, "user", None), "id", None)
            try:
                return await method(self, *args, **kwargs)
            except ServiceException as e:
                logger.warning(
                    f"[{type(self).__name__}.{method.__name__}] user_id={user_id} "
                    f"service_exception message={e.message!r} error_code={e.error_code}"
                )
                await self._send_error(e.message or default_message)

        return wrapper

    return decorator
