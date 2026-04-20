from __future__ import annotations

import logging
from typing import Any

from app.settings import get_settings

try:
    from langchain_community.chat_models import ChatYandexGPT
except ImportError:  # pragma: no cover - optional runtime dependency until installed
    ChatYandexGPT = None


settings = get_settings()
logger = logging.getLogger(__name__)


def create_yandex_gpt_client(*, temperature: float | None = None) -> tuple[Any, str | None]:
    if not settings.yandex_gpt_enabled or ChatYandexGPT is None:
        if not settings.yandex_gpt_enabled:
            return None, "YandexGPT отключен в конфигурации."
        return None, "Зависимость langchain-community для YandexGPT не установлена."

    api_key = _none_if_blank(settings.yandex_cloud_api_key)
    folder_id = _none_if_blank(settings.yandex_cloud_folder_id)
    model_uri = _none_if_blank(settings.yandex_gpt_model_uri)

    if not api_key:
        return None, "API key для YandexGPT не настроен."
    if not folder_id and not model_uri:
        return None, "Folder ID или model URI для YandexGPT не настроены."

    client_kwargs = {
        "api_key": api_key,
        "folder_id": folder_id,
        "model_uri": model_uri,
        "temperature": settings.yandex_gpt_temperature if temperature is None else temperature,
        "max_tokens": settings.yandex_gpt_max_tokens,
        "max_retries": settings.yandex_gpt_max_retries,
    }
    try:
        client = ChatYandexGPT(**{key: value for key, value in client_kwargs.items() if value is not None})
    except Exception as exc:  # pragma: no cover - depends on external API/runtime setup
        logger.exception("Failed to initialize YandexGPT client.")
        return None, f"Не удалось инициализировать YandexGPT client: {exc}"
    return client, None


def _none_if_blank(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None
