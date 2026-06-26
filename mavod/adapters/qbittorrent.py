"""Adapter qBittorrent typé.

Wrappe le client existant et expose les opérations clés avec Settings +
exceptions typées (`QBittorrentError`, `DuplicateTorrent`).
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Union

from mavod.config import Settings
from mavod.exceptions import DuplicateTorrent, QBittorrentError
from mavod.logging_setup import get_logger


log = get_logger(__name__)


TorrentSource = Union[str, bytes]


class QBittorrentAdapter:
    """Adapter typé pour qBittorrent. Délègue au client existant."""

    def __init__(self, settings: Settings):
        from mavod.qbittorrent_client import QBittorrentClient
        self._client = QBittorrentClient(
            url=settings.qb_url,
            username=settings.qb_user,
            password=settings.qb_pass,
        )

    def add(
        self,
        source: TorrentSource,
        *,
        download_dir: Optional[str] = None,
        tags: Optional[str] = None,
        category: Optional[str] = None,
    ) -> str:
        """Ajoute un torrent. Retourne l'infohash (lower-case)."""
        try:
            return self._client.add_torrent(
                source, download_dir=download_dir, tags=tags, category=category
            )
        except RuntimeError as e:
            msg = str(e).lower()
            if "déjà présent" in str(e) or "duplicate" in msg:
                raise DuplicateTorrent(str(e)) from e
            raise QBittorrentError(str(e)) from e

    def get_info(self, infohash: str) -> Optional[Dict[str, Any]]:
        """Retourne l'état d'un torrent (state, progress, name…) ou None si introuvable."""
        try:
            return self._client.get_torrent_info(infohash)
        except Exception as e:
            raise QBittorrentError(f"get_info KO: {e}") from e

    def delete(self, infohash: str, *, delete_files: bool = False) -> None:
        """Supprime un torrent ; `delete_files=True` efface aussi les données sur disque."""
        try:
            self._client.delete_torrent(infohash, delete_files=delete_files)
        except Exception as e:
            raise QBittorrentError(f"delete KO: {e}") from e
