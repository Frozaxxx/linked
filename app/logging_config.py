from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.settings import get_settings


LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s %(message)s"
QUIET_LOGGERS = ("httpx", "httpcore", "urllib3", "playwright")


def configure_logging() -> None:
    settings = get_settings()
    log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(_resolve_level(settings.log_level))

    app_log_path = log_dir / "internal-linking.log"
    error_log_path = log_dir / "internal-linking-errors.log"

    if not _has_file_handler(root_logger, app_log_path):
        app_handler = RotatingFileHandler(
            app_log_path,
            encoding="utf-8",
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
        )
        app_handler.setLevel(_resolve_level(settings.log_level))
        app_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(app_handler)

    if not _has_file_handler(root_logger, error_log_path):
        error_handler = RotatingFileHandler(
            error_log_path,
            encoding="utf-8",
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backup_count,
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(logging.Formatter(LOG_FORMAT))
        root_logger.addHandler(error_handler)

    for logger_name in QUIET_LOGGERS:
        logging.getLogger(logger_name).setLevel(logging.WARNING)


def _resolve_level(level_name: str) -> int:
    return getattr(logging, level_name.upper(), logging.INFO)


def _has_file_handler(logger: logging.Logger, expected_path: Path) -> bool:
    expected = str(expected_path.resolve())
    return any(
        isinstance(handler, logging.FileHandler) and getattr(handler, "baseFilename", "") == expected
        for handler in logger.handlers
    )
