from __future__ import annotations

import logging
from typing import Any

from app.settings import get_settings

try:
    from langchain_gigachat.chat_models import GigaChat
except ImportError:  # pragma: no cover - optional runtime dependency until installed
    GigaChat = None


settings = get_settings()
logger = logging.getLogger(__name__)


def create_gigachat_client(*, temperature: float | None = None) -> tuple[Any, str | None]:
    if not settings.gigachat_enabled or GigaChat is None:
        if not settings.gigachat_enabled:
            return None, "GigaChat отключен в конфигурации."
        return None, "Зависимость langchain_gigachat не установлена."

    auth_kwargs: dict[str, Any] = {}
    if settings.gigachat_access_token and _looks_like_jwt(settings.gigachat_access_token):
        auth_kwargs["access_token"] = settings.gigachat_access_token
    elif settings.gigachat_credentials:
        auth_kwargs["credentials"] = settings.gigachat_credentials
        auth_kwargs["scope"] = settings.gigachat_scope
    elif settings.gigachat_access_token:
        auth_kwargs["credentials"] = settings.gigachat_access_token
        auth_kwargs["scope"] = settings.gigachat_scope
    else:
        return None, "Учетные данные или access token для GigaChat не настроены."

    client_kwargs = {
        **auth_kwargs,
        "model": settings.gigachat_model,
        "temperature": settings.gigachat_temperature if temperature is None else temperature,
        "timeout": settings.gigachat_timeout_seconds,
        "max_retries": settings.gigachat_max_retries,
        "verify_ssl_certs": settings.gigachat_verify_ssl_certs,
        "ca_bundle_file": settings.gigachat_ca_bundle_file,
    }
    try:
        client = GigaChat(**{key: value for key, value in client_kwargs.items() if value is not None})
    except Exception as exc:  # pragma: no cover - depends on external API/runtime setup
        logger.exception("Failed to initialize GigaChat client.")
        return None, f"Не удалось инициализировать GigaChat client: {exc}"
    return client, None


def _looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2
