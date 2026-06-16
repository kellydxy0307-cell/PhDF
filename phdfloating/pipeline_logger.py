"""Shared logger for summary pipeline runs."""

from __future__ import annotations

import logging
from pathlib import Path

from .settings_store import get_config_dir


LOGGER_NAME = "phdf.summary_pipeline"


def get_pipeline_log_path() -> Path:
    return get_config_dir() / "logs" / "summary_pipeline.log"


def get_pipeline_logger() -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    if getattr(logger, "_phdf_configured", False):
        return logger

    logger.setLevel(logging.INFO)
    logger.propagate = False

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            pass

    try:
        log_path = get_pipeline_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
    except OSError:
        logger.addHandler(logging.NullHandler())

    setattr(logger, "_phdf_configured", True)
    return logger
