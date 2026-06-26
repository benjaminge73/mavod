"""Adapter Prowlarr typé.

Wrappe `torrents_search_download.prowlarr_client.ProwlarrClient` mais expose
des `Torrent` du domain (au lieu de dict) et consomme `Settings` (au lieu
d'os.environ).
"""

from __future__ import annotations

from typing import List, Optional

import requests

from mavod.config import Settings
from mavod.domain import Torrent
from mavod.exceptions import ProwlarrError
from mavod.logging_setup import get_logger


log = get_logger(__name__)


def _normalize_dict_to_torrent(raw: dict) -> Torrent:
    """Convertit un résultat Prowlarr normalisé (dict) en Torrent typé."""
    url = raw.get("downloadUrl", "")
    is_magnet = bool(raw.get("is_magnet")) or url.startswith("magnet:")
    return Torrent(
        title=raw.get("title", ""),
        indexer=raw.get("indexer", "Prowlarr:Unknown"),
        size_bytes=int(raw.get("size") or 0),
        seeders=int(raw.get("seeders") or 0),
        leechers=int(raw.get("leechers") or 0),
        infohash=(raw.get("infoHash") or None) or None,
        magnet=url if is_magnet else None,
        torrent_url=url if (url and not is_magnet) else None,
        extra={
            "guid":        raw.get("guid", ""),
            "categories":  raw.get("categories", []),
            "publishDate": raw.get("publishDate", ""),
            "downloads":   raw.get("downloads", 0),
            "_prowlarr_indexer": raw.get("_prowlarr_indexer", ""),
        },
    )


class ProwlarrAdapter:
    """Adapter typé pour Prowlarr. Délègue au client existant pour le HTTP."""

    def __init__(self, settings: Settings):
        from torrents_search_download.prowlarr_client import ProwlarrClient
        self._client = ProwlarrClient(
            api_url=settings.prowlarr_url,
            api_key=settings.prowlarr_api_key,
        )
        self._settings = settings

    def search_movies(
        self,
        title: str,
        *,
        year: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Torrent]:
        """Cherche des films sur Prowlarr et retourne les Torrent normalisés."""
        try:
            raw = self._client.search_movies(title, year=year, imdb_id=imdb_id)
        except requests.exceptions.RequestException as e:
            raise ProwlarrError(f"requête films KO: {e}") from e
        log.info(
            "prowlarr.search.done",
            extra={"type": "movie", "title": title, "year": year, "imdb_id": imdb_id, "count": len(raw)},
        )
        return [_normalize_dict_to_torrent(r) for r in raw]

    def search_series(
        self,
        title: str,
        *,
        season: Optional[int] = None,
        episode: Optional[int] = None,
        imdb_id: Optional[str] = None,
    ) -> List[Torrent]:
        """Cherche des séries sur Prowlarr et retourne les Torrent normalisés."""
        try:
            raw = self._client.search_series(
                title, season=season, episode=episode, imdb_id=imdb_id
            )
        except requests.exceptions.RequestException as e:
            raise ProwlarrError(f"requête séries KO: {e}") from e
        log.info(
            "prowlarr.search.done",
            extra={
                "type": "serie", "title": title, "season": season,
                "episode": episode, "imdb_id": imdb_id, "count": len(raw),
            },
        )
        return [_normalize_dict_to_torrent(r) for r in raw]
