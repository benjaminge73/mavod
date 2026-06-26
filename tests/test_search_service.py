"""Tests de mavod.services.search_service — adapters mockés."""

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
    def test_movie_via_prowlarr_only(self, settings):
        """Recherche film uniquement via Prowlarr."""
        prow = MagicMock()
        prow.search_movies.return_value = [_t("A", "aa"), _t("B", "bb")]
        c411 = MagicMock()

        svc = SearchService(settings, prowlarr=prow, c411=c411)
        intent = Intent(title="Dune", type="movie", year=2021)
        outcome = svc.search(intent)

        prow.search_movies.assert_called_once_with("Dune", year=2021, imdb_id=None)
        # C411 NE doit PAS être appelé si Prowlarr renvoie des résultats
        c411.search_movies.assert_not_called()
        assert len(outcome.raw_pool) == 2
        assert outcome.sources_used == ("prowlarr",)

    def test_fallback_c411_when_prowlarr_empty(self, settings):
        """Bascule sur C411 quand Prowlarr ne renvoie rien."""
        prow = MagicMock()
        prow.search_movies.return_value = []
        c411 = MagicMock()
        c411.search_movies.return_value = [_t("C", "cc")]

        svc = SearchService(settings, prowlarr=prow, c411=c411)
        outcome = svc.search(Intent(title="Foo", type="movie"))
        c411.search_movies.assert_called_once_with("Foo", year=None)
        assert outcome.sources_used == ("c411",)
        assert len(outcome.raw_pool) == 1

    def test_serie_with_season_episode_imdb(self, settings):
        """Recherche série avec saison, épisode et IMDb id."""
        prow = MagicMock()
        prow.search_series.return_value = [_t("S")]
        c411 = MagicMock()

        svc = SearchService(settings, prowlarr=prow, c411=c411)
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

    def test_prowlarr_error_falls_back_to_c411(self, settings):
        """Une erreur Prowlarr déclenche le fallback C411."""
        prow = MagicMock()
        prow.search_movies.side_effect = ProwlarrError("down")
        c411 = MagicMock()
        c411.search_movies.return_value = [_t("X")]

        svc = SearchService(settings, prowlarr=prow, c411=c411)
        outcome = svc.search(Intent(title="X", type="movie"))
        assert outcome.sources_used == ("c411",)

    def test_deduplication_by_infohash(self, settings):
        """Dédoublonne les résultats par infohash."""
        prow = MagicMock()
        # Deux résultats avec le même infohash
        prow.search_movies.return_value = [_t("A", "deadbeef"), _t("A bis", "deadbeef")]
        c411 = MagicMock()
        svc = SearchService(settings, prowlarr=prow, c411=c411)
        outcome = svc.search(Intent(title="A", type="movie"))
        assert len(outcome.raw_pool) == 1

    def test_no_infohash_no_dedup(self, settings):
        """Ne dédoublonne pas en l'absence d'infohash."""
        prow = MagicMock()
        # Sans infohash, on ne dédup pas (sécurité : préserver des candidats valides)
        prow.search_movies.return_value = [_t("A"), _t("B")]
        c411 = MagicMock()
        svc = SearchService(settings, prowlarr=prow, c411=c411)
        outcome = svc.search(Intent(title="A", type="movie"))
        assert len(outcome.raw_pool) == 2
