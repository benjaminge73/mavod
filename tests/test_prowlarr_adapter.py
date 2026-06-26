"""Tests unitaires de mavod.adapters.prowlarr."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from mavod.adapters.prowlarr import ProwlarrAdapter, _normalize_dict_to_torrent
from mavod.config import load_settings
from mavod.domain import Torrent
from mavod.exceptions import ProwlarrError

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


@pytest.fixture
def adapter(settings):
    a = ProwlarrAdapter(settings)
    a._client = MagicMock()
    return a


class TestNormalize:
    def test_magnet_url_detected(self):
        """Détecte une URL magnet et marque is_magnet."""
        raw = {"title": "X", "downloadUrl": "magnet:?xt=urn:btih:abc", "indexer": "Prowlarr:Y"}
        t = _normalize_dict_to_torrent(raw)
        assert t.magnet == "magnet:?xt=urn:btih:abc"
        assert t.torrent_url is None

    def test_http_url_kept_as_torrent_url(self):
        """Une URL HTTP est conservée comme torrent_url."""
        raw = {"title": "X", "downloadUrl": "https://x/torrent.torrent", "indexer": "Prowlarr:Y"}
        t = _normalize_dict_to_torrent(raw)
        assert t.torrent_url == "https://x/torrent.torrent"
        assert t.magnet is None

    def test_is_magnet_flag_respected_even_without_magnet_prefix(self):
        """Le flag is_magnet est respecté même sans préfixe magnet."""
        raw = {"title": "X", "downloadUrl": "https://weird", "is_magnet": True, "indexer": "P"}
        t = _normalize_dict_to_torrent(raw)
        assert t.magnet == "https://weird"

    def test_infohash_propagated(self):
        """L'infohash est propagé depuis la réponse Prowlarr."""
        raw = {"title": "X", "infoHash": "deadbeef", "indexer": "P"}
        t = _normalize_dict_to_torrent(raw)
        assert t.infohash == "deadbeef"

    def test_missing_fields_use_defaults(self):
        """Les champs manquants utilisent des valeurs par défaut."""
        t = _normalize_dict_to_torrent({})
        assert t.title == ""
        assert t.indexer == "Prowlarr:Unknown"
        assert t.size_bytes == 0
        assert t.seeders == 0
        assert t.leechers == 0
        assert t.infohash is None

    def test_extra_fields_preserved(self):
        """Les champs supplémentaires sont préservés dans extra."""
        raw = {
            "title": "X", "indexer": "P", "guid": "g1",
            "categories": [2000], "publishDate": "2024-01-01",
            "downloads": 42, "_prowlarr_indexer": "Indexer-Name",
        }
        t = _normalize_dict_to_torrent(raw)
        assert t.extra["guid"] == "g1"
        assert t.extra["categories"] == [2000]
        assert t.extra["downloads"] == 42
        assert t.extra["_prowlarr_indexer"] == "Indexer-Name"

    def test_size_and_seeders_coerced_to_int(self):
        """Coerce size et seeders en entiers."""
        raw = {"title": "X", "indexer": "P", "size": "1024", "seeders": "5", "leechers": "2"}
        t = _normalize_dict_to_torrent(raw)
        assert t.size_bytes == 1024
        assert t.seeders == 5
        assert t.leechers == 2


class TestSearchMovies:
    def test_returns_typed_torrents(self, adapter):
        """Retourne des Torrent typés pour un film."""
        adapter._client.search_movies.return_value = [
            {"title": "Dune", "indexer": "Prowlarr:X", "size": 5_000_000_000, "seeders": 30},
        ]
        out = adapter.search_movies("Dune", year=2021)
        assert len(out) == 1
        assert isinstance(out[0], Torrent)
        assert out[0].title == "Dune"

    def test_passes_year_and_imdb_id(self, adapter):
        """Transmet l'année et l'IMDb id à Prowlarr."""
        adapter._client.search_movies.return_value = []
        adapter.search_movies("Dune", year=2021, imdb_id="tt1160419")
        adapter._client.search_movies.assert_called_once_with(
            "Dune", year=2021, imdb_id="tt1160419"
        )

    def test_request_exception_wrapped(self, adapter):
        """Les erreurs réseau film sont enveloppées proprement."""
        adapter._client.search_movies.side_effect = requests.exceptions.ConnectionError("boom")
        with pytest.raises(ProwlarrError, match="films KO"):
            adapter.search_movies("Dune")


class TestSearchSeries:
    def test_returns_typed_torrents(self, adapter):
        """Retourne des Torrent typés pour une série."""
        adapter._client.search_series.return_value = [
            {"title": "The Bear S03", "indexer": "Prowlarr:Y"},
        ]
        out = adapter.search_series("The Bear", season=3)
        assert len(out) == 1
        assert out[0].title == "The Bear S03"

    def test_passes_season_and_imdb_id(self, adapter):
        """Transmet la saison et l'IMDb id à Prowlarr."""
        adapter._client.search_series.return_value = []
        adapter.search_series("The Bear", season=3, imdb_id="tt14452776")
        adapter._client.search_series.assert_called_once_with(
            "The Bear", season=3, episode=None, imdb_id="tt14452776"
        )

    def test_request_exception_wrapped(self, adapter):
        """Les erreurs réseau série sont enveloppées proprement."""
        adapter._client.search_series.side_effect = requests.exceptions.Timeout("slow")
        with pytest.raises(ProwlarrError, match="séries KO"):
            adapter.search_series("The Bear", season=3)
