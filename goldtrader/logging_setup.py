"""Structured JSON logging shared across the package."""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

import structlog

from .config import get_settings

_configured = False


def setup_logging(console: bool = True) -> None:
    """Configure structlog + stdlib logging once. Idempotent."""
    global _configured
    if _configured:
        return
    settings = get_settings()

    timestamper = structlog.processors.TimeStamper(fmt="iso")
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        timestamper,
        structlog.processors.StackInfoRenderer(),
    ]

    structlog.configure(
        processors=shared_processors
        + [structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    json_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    console_formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(colors=True),
        foreign_pre_chain=shared_processors,
    )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    file_handler = RotatingFileHandler(
        settings.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(json_formatter)
    root.addHandler(file_handler)

    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(console_formatter)
        root.addHandler(stream)

    _configured = True


def get_logger(name: str = "goldtrader") -> structlog.stdlib.BoundLogger:
    setup_logging()
    return structlog.get_logger(name)
