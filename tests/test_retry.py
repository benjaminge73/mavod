"""Tests du décorateur with_retry et parse_retry_after."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from mavod.adapters._retry import parse_retry_after, with_retry

pytestmark = pytest.mark.unit


class TestParseRetryAfter:
    def test_none(self):
        """Retourne None quand l'en-tête Retry-After est absent."""
        assert parse_retry_after(None, 5.0) == 5.0

    def test_seconds_integer(self):
        """Parse Retry-After en secondes entières."""
        assert parse_retry_after("12", 5.0) == 12.0

    def test_seconds_float(self):
        """Parse Retry-After en secondes flottantes."""
        assert parse_retry_after("12.5", 5.0) == 12.5

    def test_http_date_future(self):
        """Parse Retry-After au format HTTP date dans le futur."""
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        header = format_datetime(future, usegmt=True)
        val = parse_retry_after(header, 5.0)
        # Tolérance ±2s pour le test
        assert 28 <= val <= 32

    def test_http_date_past_returns_zero(self):
        """Une HTTP date passée retourne zéro."""
        past = datetime.now(timezone.utc) - timedelta(seconds=60)
        header = format_datetime(past, usegmt=True)
        assert parse_retry_after(header, 5.0) == 0.0

    def test_garbage_returns_fallback(self):
        """Une valeur invalide retourne le fallback."""
        assert parse_retry_after("not a date", 5.0) == 5.0


class TestWithRetry:
    def test_succeeds_first_try(self):
        """Réussit dès le premier essai sans retry."""
        calls = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01)
        def fn():
            calls["n"] += 1
            return "ok"

        assert fn() == "ok"
        assert calls["n"] == 1

    def test_retries_then_succeeds(self):
        """Réessaie puis finit par réussir."""
        calls = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01, retry_on=(ValueError,))
        def fn():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ValueError("transient")
            return "ok"

        assert fn() == "ok"
        assert calls["n"] == 3

    def test_raises_after_max_attempts(self):
        """Lève après le nombre max de tentatives."""
        calls = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01, retry_on=(ValueError,))
        def fn():
            calls["n"] += 1
            raise ValueError("always")

        with pytest.raises(ValueError):
            fn()
        assert calls["n"] == 3

    def test_does_not_retry_unrelated_exception(self):
        """Ne réessaie pas pour une exception non concernée."""
        calls = {"n": 0}

        @with_retry(max_attempts=3, base_delay=0.01, retry_on=(ValueError,))
        def fn():
            calls["n"] += 1
            raise RuntimeError("not in retry_on")

        with pytest.raises(RuntimeError):
            fn()
        assert calls["n"] == 1
