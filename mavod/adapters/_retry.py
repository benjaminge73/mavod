"""Décorateur retry unifié pour les appels réseau.

Remplace la duplication chat/chat_with_tools du client LLM + la
gestion ad-hoc dans Prowlarr. Honore `Retry-After` (HTTP date OU
secondes), backoff exponentiel avec cap.
"""

from __future__ import annotations

import email.utils
import functools
import logging
import time
from datetime import datetime, timezone
from typing import Callable, Optional, Tuple, Type, TypeVar


T = TypeVar("T")
log = logging.getLogger(__name__)


def parse_retry_after(header_value: Optional[str], fallback: float) -> float:
    """Parse un header HTTP `Retry-After` (secondes OU HTTP date).

    Retourne `fallback` si le header est absent ou non parsable.
    """
    if not header_value:
        return fallback
    header_value = header_value.strip()
    # Format secondes ("12" ou "12.5")
    try:
        return float(header_value)
    except ValueError:
        pass
    # Format HTTP date
    try:
        dt = email.utils.parsedate_to_datetime(header_value)
        if dt is None:
            return fallback
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = (dt - datetime.now(timezone.utc)).total_seconds()
        return max(delta, 0.0)
    except (TypeError, ValueError):
        return fallback


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff: float = 2.0,
    retry_on: Tuple[Type[BaseException], ...] = (Exception,),
    logger: Optional[logging.Logger] = None,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """Décorateur : retry exponentiel d'une fonction synchrone.

    Args:
        max_attempts: nombre total de tentatives (≥ 1).
        base_delay: délai initial (secondes).
        max_delay: cap supérieur du délai.
        backoff: multiplicateur exponentiel.
        retry_on: tuple d'exceptions qui déclenchent un retry.
        logger: logger à utiliser ; défaut = logger du module.

    Le dernier essai relève l'exception sans la swallow.
    """
    _log = logger or log

    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(fn)
        def wrapper(*args, **kwargs) -> T:
            delay = base_delay
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except retry_on as exc:
                    if attempt == max_attempts:
                        raise
                    _log.warning(
                        "retry.attempt_failed",
                        extra={
                            "fn": fn.__name__,
                            "attempt": attempt,
                            "max_attempts": max_attempts,
                            "delay": delay,
                            "exc": type(exc).__name__,
                        },
                    )
                    time.sleep(delay)
                    delay = min(delay * backoff, max_delay)
            # Inatteignable, mais le typage l'exige
            raise RuntimeError("with_retry: max_attempts dépassé sans exception")

        return wrapper

    return decorator
