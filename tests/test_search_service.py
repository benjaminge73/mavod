"""Tests de mavod.services.search_service — adapter Prowlarr mocké."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mavod.config import load_settings
from mavod.domain import Intent, Torrent
from mavod.exceptions import ProwlarrError
from mavod.services.search_service import SearchService

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "LLM_API_KEY": "sk",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
}


@pytest.fixture
def settings():
    return load_settings(env=_ENV)


def _t(title: str, infohash: str = None, size_gb: float = 5.0) -> Torrent:
    return Torrent(
        title=title,
        indexer="Prowlarr:Test",
        size_bytes=int(size_gb * 1024 ** 3),
        seeders=10,
        infohash=infohash,
    )


class TestSearchService:
    def test_movie_via_prowlarr(self, settings):
        """Recherche film via Prowlarr."""
        prow = MagicMock()
        prow.search_movies.return_value = [_t("A", "aa"), _t("B", "bb")]

        svc = SearchService(settings, prowlarr=prow)
        intent = Intent(title="Dune", type="movie", year=2021)
        outcome = svc.search(intent)

        prow.search_movies.assert_called_once_with("Dune", year=2021, imdb_id=None)
        assert len(outcome.raw_pool) == 2
        assert outcome.sources_used == ("prowlarr",)

    def test_empty_prowlarr_yields_empty_pool(self, settings):
        """Prowlarr vide → pool vide, aucune source (plus de fallback C411)."""
        prow = MagicMock()
        prow.search_movies.return_value = []

        svc = SearchService(settings, prowlarr=prow)
        outcome = svc.search(Intent(title="Foo", type="movie"))
        assert outcome.raw_pool == ()
        assert outcome.sources_used == ()

    def test_serie_with_season_episode_imdb(self, settings):
        """Recherche série avec saison, épisode et IMDb id."""
        prow = MagicMock()
        prow.search_series.return_value = [_t("S")]

        svc = SearchService(settings, prowlarr=prow)
        intent = Intent(
            title="The Bear",
            type="serie",
            season=3,
            episode=4,
            year=2024,
            imdb_id="tt1234567",
        )
        svc.search(intent)
        prow.search_series.assert_called_once_with(
            "The Bear", season=3, episode=4, imdb_id="tt1234567"
        )

    def test_prowlarr_error_yields_empty(self, settings):
        """Une erreur Prowlarr est avalée → pool vide (dégradation gracieuse)."""
        prow = MagicMock()
        prow.search_movies.side_effect = ProwlarrError("down")

        svc = SearchService(settings, prowlarr=prow)
        outcome = svc.search(Intent(title="X", type="movie"))
        assert outcome.raw_pool == ()
        assert outcome.sources_used == ()

    def test_deduplication_by_infohash(self, settings):
        """Dédoublonne les résultats par infohash."""
        prow = MagicMock()
        # Deux résultats avec le même infohash
        prow.search_movies.return_value = [_t("A", "deadbeef"), _t("A bis", "deadbeef")]
        svc = SearchService(settings, prowlarr=prow)
        outcome = svc.search(Intent(title="A", type="movie"))
        assert len(outcome.raw_pool) == 1

    def test_no_infohash_no_dedup(self, settings):
        """Ne dédoublonne pas en l'absence d'infohash."""
        prow = MagicMock()
        # Sans infohash, on ne dédup pas (sécurité : préserver des candidats valides)
        prow.search_movies.return_value = [_t("A"), _t("B")]
        svc = SearchService(settings, prowlarr=prow)
        outcome = svc.search(Intent(title="A", type="movie"))
        assert len(outcome.raw_pool) == 2
