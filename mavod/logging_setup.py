"""Logging structuré maVOD.

Pas de dépendance externe : on utilise `logging` stdlib avec un formatter
JSON-like quand `MAVOD_LOG_JSON=1` est défini, sinon human-readable.

Convention d'utilisation :
    from mavod.logging_setup import get_logger
    log = get_logger(__name__)
    log.info("workflow.search.done", extra={"search_id": sid, "candidates": n})
"""

from __future__ import annotations

import json
import logging
import os
import sys
from logging import Formatter, Logger, StreamHandler
from pathlib import Path
from typing import Any, Optional


_CONFIGURED = False


class _JsonFormatter(Formatter):
    """Formatter qui sérialise chaque LogRecord en une ligne JSON."""

    _RESERVED = {
        "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
        "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
        "created", "msecs", "relativeCreated", "thread", "threadName",
        "processName", "process", "message", "asctime", "taskName",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts":     self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":  record.levelname,
            "logger": record.name,
            "event":  record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key in self._RESERVED or key.startswith("_"):
                continue
            payload[key] = value
        return json.dumps(payload, ensure_ascii=False, default=str)


class _HumanFormatter(Formatter):
    """Formatter human-readable avec champs extra entre crochets."""

    _RESERVED = _JsonFormatter._RESERVED

    def format(self, record: logging.LogRecord) -> str:
        base = f"{self.formatTime(record, '%H:%M:%S')} [{record.levelname:5}] {record.name}: {record.getMessage()}"
        extras = {
            k: v for k, v in record.__dict__.items()
            if k not in self._RESERVED and not k.startswith("_")
        }
        if extras:
            base += " " + " ".join(f"{k}={v!r}" for k, v in extras.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(
    *,
    level: str = "INFO",
    json_output: Optional[bool] = None,
    log_file: Optional[Path] = None,
) -> None:
    """Configure le root logger. Idempotent.

    Args:
        level: niveau global (DEBUG, INFO, WARNING, ERROR).
        json_output: True → JSON formatter ; False → human ; None → auto via env
            MAVOD_LOG_JSON.
        log_file: si défini, ajoute un FileHandler en plus du stderr.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return

    if json_output is None:
        json_output = os.environ.get("MAVOD_LOG_JSON", "").lower() in ("1", "true", "yes")

    fmt: Formatter = _JsonFormatter() if json_output else _HumanFormatter()
    root = logging.getLogger()
    root.setLevel(level.upper())

    # Reset handlers existants (utile en tests / reload)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    stderr_handler = StreamHandler(sys.stderr)
    stderr_handler.setFormatter(fmt)
    root.addHandler(stderr_handler)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(fmt)
            root.addHandler(file_handler)
        except OSError as e:
            # Pas fatal — on log juste sur stderr
            root.warning("logging.file_handler.failed", extra={"path": str(log_file), "err": str(e)})

    # Réduire le bruit de bibliothèques bavardes
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("httpcore").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")

    _CONFIGURED = True


def get_logger(name: str) -> Logger:
    """Helper : retourne un logger nommé. Configure le root si besoin."""
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)


def reset_for_tests() -> None:
    """Force la reconfiguration au prochain `configure_logging`. Réservé aux tests."""
    global _CONFIGURED
    _CONFIGURED = False
