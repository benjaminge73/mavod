"""Service unifié de recherche torrent (films + séries).

Remplace `torrents_search_download/movie_search_cli.py` et
`serie_search_cli.py` (200 lignes dupliquées). Une seule fonction
`search()` paramétrée par `Intent.type`.

Couche fine : délègue le HTTP à `ProwlarrAdapter` (seule source) et le
filtrage (jusqu'à ce que `ranking_service` le porte) au module legacy
`torrents_search_download.torrent_filter`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Tuple

from mavod.adapters.prowlarr import ProwlarrAdapter
from mavod.config import Settings
from mavod.domain import Intent, Torrent
from mavod.exceptions import ProwlarrError
from mavod.logging_setup import get_logger
from mavod.services.dedup import dedup_torrents


log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """Résultat brut du sourcing (avant ranking/filter top-N)."""

    raw_pool: Tuple[Torrent, ...]
    sources_used: Tuple[str, ...]


class SearchService:
    """Orchestre la recherche torrent via Prowlarr (source unique)."""

    def __init__(
        self,
        settings: Settings,
        *,
        prowlarr: Optional[ProwlarrAdapter] = None,
    ):
        self._settings = settings
        self._prowlarr = prowlarr or ProwlarrAdapter(settings)

    def search(self, intent: Intent) -> SearchOutcome:
        """Recherche pour un `Intent`, dispatch films / séries automatiquement.

        Pas de filtrage qualité ici — l'objectif est de remplir le pool brut.
        Le filter+ranker downstream s'en charge.
        """
        if intent.type == "movie":
            prow = self._safe_prowlarr_movies(intent)
        else:
            prow = self._safe_prowlarr_series(intent)

        # Déduplication cross-tracker : infohash, sinon nom de release
        # normalisé + taille (cf. mavod.services.dedup).
        pool: List[Torrent] = []
        sources_used: List[str] = []
        duplicates_removed = 0

        if prow:
            sources_used.append("prowlarr")
            pool = dedup_torrents(prow)
            duplicates_removed = len(prow) - len(pool)

        log.info(
            "search.done",
            extra={
                "title": intent.title,
                "type": intent.type,
                "year": intent.year,
                "season": intent.season,
                "episode": intent.episode,
                "imdb_id": intent.imdb_id,
                "raw_count": len(pool),
                "duplicates_removed": duplicates_removed,
                "sources": ",".join(sources_used) or "none",
            },
        )
        return SearchOutcome(raw_pool=tuple(pool), sources_used=tuple(sources_used))

    # ── Wrappers défensifs : convertissent les erreurs en empty list ──────

    def _safe_prowlarr_movies(self, intent: Intent) -> List[Torrent]:
        try:
            return self._prowlarr.search_movies(
                intent.title, year=intent.year, imdb_id=intent.imdb_id
            )
        except ProwlarrError as e:
            log.warning("search.prowlarr_unavailable", extra={"err": str(e)})
            return []

    def _safe_prowlarr_series(self, intent: Intent) -> List[Torrent]:
        try:
            return self._prowlarr.search_series(
                intent.title,
                season=intent.season,
                episode=intent.episode,
                imdb_id=intent.imdb_id,
            )
        except ProwlarrError as e:
            log.warning("search.prowlarr_unavailable", extra={"err": str(e)})
            return []
