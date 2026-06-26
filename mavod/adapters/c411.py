"""Adapter C411 typé."""

from __future__ import annotations

from typing import List, Optional

import requests

from mavod.config import Settings
from mavod.domain import Torrent
from mavod.exceptions import C411Error
from mavod.logging_setup import get_logger


log = get_logger(__name__)


def _normalize_dict_to_torrent(raw: dict) -> Torrent:
    """Convertit un résultat C411 normalisé (dict) en Torrent typé."""
    url = raw.get("downloadUrl", "")
    return Torrent(
        title=raw.get("title", ""),
        indexer=raw.get("indexer", "C411"),
        size_bytes=int(raw.get("size") or 0),
        seeders=int(raw.get("seeders") or 0),
        leechers=int(raw.get("leechers") or 0),
        infohash=(raw.get("infoHash") or None),
        magnet=url if url.startswith("magnet:") else None,
        torrent_url=None,
        extra={
            "guid":        raw.get("guid", ""),
            "categories":  raw.get("categories", []),
            "publishDate": raw.get("publishDate", ""),
            "downloads":   raw.get("downloads", 0),
            "_c411_id":    raw.get("_c411_id"),
        },
    )


class C411Adapter:
    """Adapter typé pour C411. Délègue au client existant pour le HTTP."""

    def __init__(self, settings: Settings):
        from torrents_search_download.c411_api_client import C411APIClient
        self._client = C411APIClient(
            api_key=settings.c411_api_key,
            api_url=settings.c411_url_api,
            passkey=settings.c411_passkey,
        )
        self._settings = settings

    def search_movies(self, title: str, *, year: Optional[int] = None) -> List[Torrent]:
        """Cherche des films sur C411 et retourne les Torrent normalisés."""
        try:
            raw = self._client.search_movies(title, year=year)
        except requests.exceptions.RequestException as e:
            raise C411Error(f"requête films KO: {e}") from e
        log.info("c411.search.done",
                 extra={"type": "movie", "title": title, "year": year, "count": len(raw)})
        return [_normalize_dict_to_torrent(r) for r in raw]

    def search_series(self, title: str, *, season: Optional[int] = None) -> List[Torrent]:
        """Cherche des séries sur C411 et retourne les Torrent normalisés."""
        try:
            raw = self._client.search_series(title, season=season)
        except requests.exceptions.RequestException as e:
            raise C411Error(f"requête séries KO: {e}") from e
        log.info("c411.search.done",
                 extra={"type": "serie", "title": title, "season": season, "count": len(raw)})
        return [_normalize_dict_to_torrent(r) for r in raw]
