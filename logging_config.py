from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOGGER_NAME = "haz_market_data"
DEFAULT_LOG_DIR = Path(__file__).resolve().parent / "logs"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 10


def setup_logging(
    log_dir: Path | None = None,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> Path:
    """Configure persistent rotating file logging and return the active log path."""
    target_dir = log_dir or DEFAULT_LOG_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    log_path = target_dir / "market.log"

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    # main() can be called more than once by tests or an embedded runner. Avoid
    # duplicate handlers and duplicate log lines.
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
        delay=False,
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger.addHandler(file_handler)

    return log_path


def get_logger(component: str) -> logging.Logger:
    return logging.getLogger(f"{LOGGER_NAME}.{component}")
