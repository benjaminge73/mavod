"""Jobs Telegram avec lifecycle managé.

Remplace `_poll_download_complete` orphelin de `telegram_bot.py:320-366` :
les tasks asyncio sont indexées par user_id + infohash, et le
`DownloadWatcher` permet de les annuler explicitement (sur `/reset`,
expiry de session, etc.).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Dict, Optional, Set, Tuple

from mavod.adapters.qbittorrent import QBittorrentAdapter
from mavod.config import Settings
from mavod.logging_setup import get_logger


log = get_logger(__name__)


_QB_COMPLETE_STATES: Set[str] = {
    "uploading", "stalledUP", "pausedUP", "queuedUP",
    "checkingUP", "forcedUP",
}


@dataclass(frozen=True, slots=True)
class DownloadOutcome:
    """Résultat d'un cycle de polling."""

    kind: str       # "complete" | "timeout" | "qb_unavailable" | "cancelled"
    name: str
    progress: float
    state: str
    elapsed_seconds: float


# Callback signature : appelé quand un torrent termine ou timeout.
DownloadCallback = Callable[[int, DownloadOutcome], Awaitable[None]]


class DownloadWatcher:
    """Surveille la complétion des téléchargements qBittorrent par user.

    Usage :
        watcher = DownloadWatcher(settings, qb_adapter)
        await watcher.watch(user_id="12345", infohash="abc", torrent_name="Dune", on_done=cb)

        # Plus tard, pour tout annuler (ex. /reset) :
        await watcher.cancel_user(12345)
    """

    def __init__(
        self,
        settings: Settings,
        qb: Optional[QBittorrentAdapter] = None,
    ):
        self._settings = settings
        self._qb = qb
        # (user_id, infohash) → asyncio.Task
        self._tasks: Dict[Tuple[int, str], asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def watch(
        self,
        *,
        user_id: int,
        infohash: str,
        torrent_name: str,
        on_done: DownloadCallback,
    ) -> None:
        """Lance le polling pour ce torrent. Idempotent (un seul task par hash)."""
        async with self._lock:
            key = (user_id, infohash.lower())
            existing = self._tasks.get(key)
            if existing and not existing.done():
                log.info("download_watcher.skip_duplicate", extra={"user_id": user_id, "hash": infohash[:8]})
                return
            task = asyncio.create_task(
                self._poll(user_id, infohash, torrent_name, on_done),
                name=f"download_watcher_{user_id}_{infohash[:8]}",
            )
            self._tasks[key] = task

    async def cancel_user(self, user_id: int) -> int:
        """Annule tous les tasks d'un user. Retourne le nombre de tasks annulés."""
        async with self._lock:
            keys = [k for k in self._tasks if k[0] == user_id]
            for k in keys:
                task = self._tasks.pop(k)
                if not task.done():
                    task.cancel()
        if keys:
            log.info("download_watcher.cancel_user", extra={"user_id": user_id, "count": len(keys)})
        return len(keys)

    async def cancel_all(self) -> int:
        """Annule tous les tasks (utilisé au shutdown du bot)."""
        async with self._lock:
            tasks = list(self._tasks.values())
            self._tasks.clear()
        for task in tasks:
            if not task.done():
                task.cancel()
        return len(tasks)

    def active_count(self) -> int:
        """Nombre de tasks de polling en cours (debug / introspection)."""
        return sum(1 for t in self._tasks.values() if not t.done())

    # ─── Interne ───────────────────────────────────────────────────────────

    async def _poll(
        self,
        user_id: int,
        infohash: str,
        torrent_name: str,
        on_done: DownloadCallback,
    ) -> None:
        key = (user_id, infohash.lower())
        start = time.monotonic()
        interval = self._settings.download_poll_interval
        timeout = self._settings.download_poll_timeout

        try:
            qb = self._qb or QBittorrentAdapter(self._settings)
            while True:
                elapsed = time.monotonic() - start
                if elapsed > timeout:
                    outcome = DownloadOutcome(
                        kind="timeout", name=torrent_name,
                        progress=0.0, state="?", elapsed_seconds=elapsed,
                    )
                    await self._fire(user_id, outcome, on_done)
                    return

                try:
                    info = qb.get_info(infohash)
                except Exception as e:
                    log.warning("download_watcher.qb_error", extra={"err": str(e), "hash": infohash[:8]})
                    info = None

                if info is None:
                    outcome = DownloadOutcome(
                        kind="qb_unavailable", name=torrent_name,
                        progress=0.0, state="?", elapsed_seconds=elapsed,
                    )
                    await self._fire(user_id, outcome, on_done)
                    return

                progress = float(info.get("progress", 0.0))
                state = str(info.get("state", ""))
                name = info.get("name") or torrent_name

                if progress >= 1.0 or state in _QB_COMPLETE_STATES:
                    outcome = DownloadOutcome(
                        kind="complete", name=name,
                        progress=progress, state=state, elapsed_seconds=elapsed,
                    )
                    await self._fire(user_id, outcome, on_done)
                    return

                await asyncio.sleep(interval)

        except asyncio.CancelledError:
            log.info("download_watcher.cancelled", extra={"user_id": user_id, "hash": infohash[:8]})
            outcome = DownloadOutcome(
                kind="cancelled", name=torrent_name,
                progress=0.0, state="?",
                elapsed_seconds=time.monotonic() - start,
            )
            try:
                await on_done(user_id, outcome)
            except Exception:
                pass
            raise
        finally:
            async with self._lock:
                self._tasks.pop(key, None)

    async def _fire(self, user_id: int, outcome: DownloadOutcome, cb: DownloadCallback) -> None:
        try:
            await cb(user_id, outcome)
        except Exception as e:
            log.warning("download_watcher.callback_error", extra={"err": str(e)})
