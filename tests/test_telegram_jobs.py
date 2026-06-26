"""Tests de mavod.telegram.jobs.DownloadWatcher."""

from __future__ import annotations

import asyncio
from typing import List
from unittest.mock import MagicMock

import pytest

from mavod.config import load_settings
from mavod.telegram.jobs import DownloadOutcome, DownloadWatcher

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "DEEPSEEK_API_KEY": "sk",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
    "C411_URL_API": "http://c411",
    "C411_API_KEY": "ck",
    "C411_PASSKEY": "pk",
}


def _build_settings(poll_interval=0.01, poll_timeout=1):
    env = dict(_ENV)
    return load_settings(env=env)


def _mk_settings_short(monkeypatch):
    s = load_settings(env=_ENV)
    # Le polling est rapide via reassignment frozen dataclass : on patche via object.__setattr__
    object.__setattr__(s, "download_poll_interval", 0)
    object.__setattr__(s, "download_poll_timeout", 1)
    return s


def test_watcher_completes_on_progress_1():
    """Le watcher se termine quand progress atteint 1."""
    async def run():
        settings = _mk_settings_short(None)
        qb = MagicMock()
        qb.get_info.return_value = {"progress": 1.0, "state": "uploading", "name": "Dune"}

        collected: List[DownloadOutcome] = []

        async def cb(user_id, outcome):
            collected.append(outcome)

        watcher = DownloadWatcher(settings, qb=qb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="Dune", on_done=cb)
        await asyncio.sleep(0.1)
        assert collected[0].kind == "complete"
        assert collected[0].name == "Dune"
        assert watcher.active_count() == 0

    asyncio.run(run())


def test_watcher_completes_on_state():
    """Le watcher se termine sur un état final qBittorrent."""
    async def run():
        settings = _mk_settings_short(None)
        qb = MagicMock()
        qb.get_info.return_value = {"progress": 0.3, "state": "stalledUP", "name": "X"}
        collected = []

        async def cb(user_id, outcome):
            collected.append(outcome)

        watcher = DownloadWatcher(settings, qb=qb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="X", on_done=cb)
        await asyncio.sleep(0.1)
        assert collected[0].kind == "complete"

    asyncio.run(run())


def test_watcher_timeout():
    """Le watcher abandonne après le timeout configuré."""
    async def run():
        settings = _mk_settings_short(None)
        object.__setattr__(settings, "download_poll_timeout", 0)  # immediate timeout
        qb = MagicMock()
        qb.get_info.return_value = {"progress": 0.1, "state": "downloading", "name": "x"}
        collected = []

        async def cb(user_id, outcome):
            collected.append(outcome)

        watcher = DownloadWatcher(settings, qb=qb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="x", on_done=cb)
        await asyncio.sleep(0.1)
        assert collected[0].kind == "timeout"

    asyncio.run(run())


def test_watcher_qb_unavailable():
    """Le watcher gère un qBittorrent injoignable."""
    async def run():
        settings = _mk_settings_short(None)
        qb = MagicMock()
        qb.get_info.return_value = None
        collected = []

        async def cb(user_id, outcome):
            collected.append(outcome)

        watcher = DownloadWatcher(settings, qb=qb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="x", on_done=cb)
        await asyncio.sleep(0.1)
        assert collected[0].kind == "qb_unavailable"

    asyncio.run(run())


def test_cancel_user():
    """Annule tous les watchers d'un utilisateur donné."""
    async def run():
        settings = _mk_settings_short(None)
        # Polling éternel : progress reste à 0.1
        qb = MagicMock()
        qb.get_info.return_value = {"progress": 0.1, "state": "downloading", "name": "x"}
        # Bumper le timeout pour ne pas finir par lui-même
        object.__setattr__(settings, "download_poll_timeout", 999)
        collected = []

        async def cb(user_id, outcome):
            collected.append(outcome)

        watcher = DownloadWatcher(settings, qb=qb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="x", on_done=cb)
        await asyncio.sleep(0.05)  # task running
        cancelled = await watcher.cancel_user(1)
        await asyncio.sleep(0.05)
        assert cancelled == 1
        # Le callback a été appelé en mode cancelled
        assert collected and collected[0].kind == "cancelled"

    asyncio.run(run())


def test_idempotent_watch():
    """Lancer watch deux fois sur la même cible est idempotent."""
    async def run():
        settings = _mk_settings_short(None)
        object.__setattr__(settings, "download_poll_timeout", 999)
        qb = MagicMock()
        qb.get_info.return_value = {"progress": 0.1, "state": "downloading", "name": "x"}
        watcher = DownloadWatcher(settings, qb=qb)

        async def cb(user_id, outcome):
            pass

        await watcher.watch(user_id=1, infohash="abc", torrent_name="x", on_done=cb)
        await watcher.watch(user_id=1, infohash="abc", torrent_name="x", on_done=cb)  # noop
        assert watcher.active_count() == 1
        await watcher.cancel_all()

    asyncio.run(run())
