from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(BASE_DIR / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Анализатор внутренней перелинковки"
    app_version: str = "0.1.0"
    app_description: str = (
        "API для анализа внутренней перелинковки. Сервис сразу запускает BFS-обход и одновременно "
        "использует sitemap как сигнал приоритизации во время сканирования."
    )
    analyze_summary: str = "Проверить внутреннюю перелинковку до целевой страницы"

    request_timeout_seconds: float = 10.0
    request_retry_count: int = 1
    crawl_concurrency: int = 12
    good_depth_threshold: int = 4
    crawl_max_depth: int = 4
    analyze_time_budget_seconds: float = 120.0
    max_crawl_level_size: int = 300
    obey_robots_txt: bool = True
    robots_user_agent: str = "*"
    log_level: str = "INFO"
    log_file_max_bytes: int = 2_000_000
    log_file_backup_count: int = 3

    gigachat_enabled: bool = True
    gigachat_credentials: str | None = None
    gigachat_access_token: str | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str | None = None
    gigachat_temperature: float = 0.2
    gigachat_timeout_seconds: float = 20.0
    gigachat_max_retries: int = 1
    gigachat_verify_ssl_certs: bool = True
    gigachat_ca_bundle_file: str | None = None
    gigachat_rerank_enabled: bool = True
    gigachat_rerank_temperature: float = 0.0
    gigachat_rerank_max_candidates: int = 4
    gigachat_good_message_prompt: str = (
        "Ты SEO-аналитик. На основе JSON с результатом анализа внутренней перелинковки сформируй "
        "короткое и понятное сообщение для пользователя на русском языке. Этот режим используется "
        "только когда перелинковка хорошая. Нужно подтвердить, что все в порядке, и кратко упомянуть, "
        "за сколько шагов найдена целевая страница относительно порога. Не давай советы и не расписывай детали. "
        "Верни только готовый текст без markdown, кавычек, списков и служебных пояснений. "
        "Длина ответа: 1-2 предложения, до 220 символов."
    )
    gigachat_bad_message_prompt: str = (
        "Ты SEO-аналитик. На основе JSON с результатом анализа внутренней перелинковки сформируй "
        "понятное и более подробное сообщение для пользователя на русском языке. Этот режим используется, "
        "когда перелинковка плохая или целевая страница не найдена. Обязательно: прямо скажи, в чем проблема; "
        "кратко объясни, как это влияет на SEO и доступность страницы для пользователя и поисковых систем; "
        "предложи, как исправить внутреннюю перелинковку; объясни, что улучшится после исправления. "
        "Если страница найдена слишком глубоко, скажи, что путь до нее длинный. "
        "Если страница не найдена, скажи, что до нее не удалось дойти в пределах глубины поиска. "
        "Опирайся только на переданные данные. Верни только готовый текст без markdown, кавычек, "
        "списков и служебных пояснений. Длина ответа: 3-5 предложений, до 700 символов."
    )

    fetch_user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/133.0.0.0 Safari/537.36"
    )
    fetch_accept_header: str = "text/html,application/xhtml+xml,application/xml,text/xml;q=0.9,*/*;q=0.8"
    fetch_browser_name: str = "chromium"
    fetch_browser_headless: bool = True
    fetch_browser_network_idle_timeout_ms: int = 1500
    fetch_browser_post_load_wait_ms: int = 250


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
