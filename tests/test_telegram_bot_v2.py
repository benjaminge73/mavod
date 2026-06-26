"""Smoke tests du bot Telegram V2 (mavod.telegram.bot).

Tests strictement structurels — pas d'I/O réseau (PTB et LLM mockés).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from mavod.config import load_settings
from mavod.domain import (
    Intent,
    QbSubmitResult,
    RankingDecision,
    Torrent,
    WorkflowResult,
)
from mavod.domain.workflow_result import SCHEMA_VERSION
from mavod.services.intent_service import IntentTurnResult
from mavod.telegram.jobs import DownloadOutcome

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "LLM_API_KEY": "sk",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
    "TELEGRAM_ALLOWED_USERS": "42",
}


@pytest.fixture
def settings(tmp_path):
    env = dict(_ENV)
    env["MAVOD_STATE_PATH"] = str(tmp_path / "state.pkl")
    env["MAVOD_TORRENTS_DIR"] = str(tmp_path / "torrents")
    env["MAVOD_LOG_PATH"] = str(tmp_path / "bot.log")
    return load_settings(env=env)


# ─── Format helpers ──────────────────────────────────────────────────────────


def test_format_intent_desc_movie():
    """Formate la description d'un intent film."""
    from mavod.telegram.bot import _format_intent_desc
    intent = Intent(title="Dune", type="movie", year=2021)
    assert _format_intent_desc(intent) == "Dune (2021)"


def test_format_intent_desc_serie_episode():
    """Formate la description série avec épisode."""
    from mavod.telegram.bot import _format_intent_desc
    intent = Intent(title="The Bear", type="serie", season=3, episode=4, year=2024)
    assert _format_intent_desc(intent) == "The Bear (2024) — S03E04"


def test_format_intent_desc_serie_no_episode():
    """Formate la description série sans épisode."""
    from mavod.telegram.bot import _format_intent_desc
    intent = Intent(title="Lost", type="serie", season=1, year=2004)
    assert _format_intent_desc(intent) == "Lost (2004) — S01"


# ─── User allowlist ──────────────────────────────────────────────────────────


def test_user_allowed():
    """Vérifie l'ACL utilisateur via la liste autorisée."""
    from mavod.telegram.bot import _user_allowed
    allowed = frozenset({1, 2, 42})
    assert _user_allowed(42, allowed) is True
    assert _user_allowed(99, allowed) is False
    assert _user_allowed(42, frozenset()) is False


def test_user_allowed_multi_user(tmp_path):
    """Une allowlist multi-user accepte chaque id listé et refuse tout autre."""
    from mavod.telegram.bot import _user_allowed

    env = dict(_ENV)
    env["TELEGRAM_ALLOWED_USERS"] = "111111,222222"
    env["MAVOD_STATE_PATH"] = str(tmp_path / "s.pkl")
    env["MAVOD_TORRENTS_DIR"] = str(tmp_path / "t")
    env["MAVOD_LOG_PATH"] = str(tmp_path / "b.log")
    s = load_settings(env=env)

    assert _user_allowed(111111, s.telegram_allowed_users) is True
    assert _user_allowed(222222, s.telegram_allowed_users) is True
    assert _user_allowed(1, s.telegram_allowed_users) is False  # intrus


# ─── Notification messages ───────────────────────────────────────────────────


def test_notify_download_complete():
    """Notifie l'utilisateur quand le téléchargement est complet."""
    async def run():
        from mavod.telegram.bot import _notify_download_outcome
        bot = AsyncMock()
        outcome = DownloadOutcome(
            kind="complete", name="Dune.2021", progress=1.0, state="uploading",
            elapsed_seconds=120.0,
        )
        await _notify_download_outcome(bot, chat_id=10, outcome=outcome)
        bot.send_message.assert_called_once()
        call = bot.send_message.call_args
        assert "📥" in call.kwargs["text"]
        assert "Dune.2021" in call.kwargs["text"]

    asyncio.run(run())


def test_notify_download_timeout():
    """Notifie l'utilisateur en cas de timeout du téléchargement."""
    async def run():
        from mavod.telegram.bot import _notify_download_outcome
        bot = AsyncMock()
        outcome = DownloadOutcome(
            kind="timeout", name="X", progress=0.42, state="downloading",
            elapsed_seconds=3600.0,
        )
        await _notify_download_outcome(bot, chat_id=10, outcome=outcome)
        text = bot.send_message.call_args.kwargs["text"]
        assert "Timeout" in text
        assert "42%" in text


    asyncio.run(run())


def test_notify_download_cancelled_silent():
    """Une annulation ne déclenche aucune notification."""
    async def run():
        from mavod.telegram.bot import _notify_download_outcome
        bot = AsyncMock()
        outcome = DownloadOutcome(
            kind="cancelled", name="X", progress=0.0, state="?",
            elapsed_seconds=0.0,
        )
        await _notify_download_outcome(bot, chat_id=10, outcome=outcome)
        bot.send_message.assert_not_called()

    asyncio.run(run())


# ─── Typing indicator (best-effort) ──────────────────────────────────────────


def test_send_typing_swallows_timeout():
    """Un TimedOut sur l'indicateur « typing… » ne doit pas se propager."""
    async def run():
        from telegram.error import TimedOut
        from mavod.telegram.bot import _send_typing
        bot = AsyncMock()
        bot.send_chat_action.side_effect = TimedOut()
        await _send_typing(bot, chat_id=10)  # ne lève pas
        bot.send_chat_action.assert_called_once()

    asyncio.run(run())


def test_send_typing_nominal():
    """En nominal, l'indicateur « typing… » est bien envoyé."""
    async def run():
        from telegram.constants import ChatAction
        from mavod.telegram.bot import _send_typing
        bot = AsyncMock()
        await _send_typing(bot, chat_id=10)
        bot.send_chat_action.assert_called_once_with(
            chat_id=10, action=ChatAction.TYPING
        )

    asyncio.run(run())


# ─── Bot context wiring ──────────────────────────────────────────────────────


def test_bot_context_wires_services(settings):
    """BotContext câble correctement les services et adapters."""
    from mavod.telegram.bot import BotContext
    ctx = BotContext(settings)
    assert ctx.settings is settings
    assert "maVOD" in ctx.system_prompt
    assert ctx.workflow_service is not None
    assert ctx.intent_service is not None
    assert ctx.download_watcher is not None
    assert ctx.sessions is not None
    assert ctx.workflow_semaphore._value == settings.max_concurrent_workflows


def test_main_module_imports():
    """`python -m mavod` doit pouvoir importer le bot V2 sans erreur."""
    import mavod.__main__  # noqa: F401
    from mavod.telegram.bot import run  # noqa: F401
