"""Tests unitaires de mavod.adapters.c411."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from mavod.adapters.c411 import C411Adapter, _normalize_dict_to_torrent
from mavod.config import load_settings
from mavod.domain import Torrent
from mavod.exceptions import C411Error

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
    a = C411Adapter(settings)
    a._client = MagicMock()
    return a


class TestNormalize:
    def test_magnet_url_detected(self):
        """Détecte une URL magnet et marque is_magnet."""
        raw = {"title": "X", "downloadUrl": "magnet:?xt=urn:btih:abc"}
        t = _normalize_dict_to_torrent(raw)
        assert t.magnet == "magnet:?xt=urn:btih:abc"

    def test_non_magnet_url_no_magnet(self):
        """Une URL non-magnet n'est pas marquée comme magnet."""
        raw = {"title": "X", "downloadUrl": "https://c411.org/d/123"}
        t = _normalize_dict_to_torrent(raw)
        assert t.magnet is None

    def test_default_indexer_is_c411(self):
        """L'indexer par défaut est C411."""
        t = _normalize_dict_to_torrent({"title": "X"})
        assert t.indexer == "C411"

    def test_missing_fields_use_defaults(self):
        """Les champs manquants utilisent des valeurs par défaut."""
        t = _normalize_dict_to_torrent({})
        assert t.title == ""
        assert t.size_bytes == 0
        assert t.seeders == 0
        assert t.infohash is None

    def test_c411_id_preserved_in_extra(self):
        """L'identifiant C411 est conservé dans extra."""
        raw = {"title": "X", "_c411_id": 12345, "guid": "g", "downloads": 7}
        t = _normalize_dict_to_torrent(raw)
        assert t.extra["_c411_id"] == 12345
        assert t.extra["guid"] == "g"
        assert t.extra["downloads"] == 7


class TestSearchMovies:
    def test_returns_typed_torrents(self, adapter):
        """Retourne une liste de Torrent typés pour un film."""
        adapter._client.search_movies.return_value = [
            {"title": "Avatar 2022", "size": 3_000_000_000, "seeders": 10},
        ]
        out = adapter.search_movies("Avatar", year=2022)
        assert len(out) == 1
        assert isinstance(out[0], Torrent)

    def test_passes_year(self, adapter):
        """Transmet l'année à la requête C411."""
        adapter._client.search_movies.return_value = []
        adapter.search_movies("Avatar", year=2022)
        adapter._client.search_movies.assert_called_once_with("Avatar", year=2022)

    def test_request_exception_wrapped(self, adapter):
        """Les erreurs réseau sont enveloppées proprement."""
        adapter._client.search_movies.side_effect = requests.exceptions.ConnectionError("net")
        with pytest.raises(C411Error, match="films KO"):
            adapter.search_movies("Avatar")


class TestSearchSeries:
    def test_returns_typed_torrents(self, adapter):
        """Retourne une liste de Torrent typés pour une série."""
        adapter._client.search_series.return_value = [{"title": "The Bear S03"}]
        out = adapter.search_series("The Bear", season=3)
        assert len(out) == 1

    def test_passes_season(self, adapter):
        """Transmet la saison à la requête C411."""
        adapter._client.search_series.return_value = []
        adapter.search_series("The Bear", season=3)
        adapter._client.search_series.assert_called_once_with("The Bear", season=3)

    def test_request_exception_wrapped(self, adapter):
        """Les erreurs réseau série sont enveloppées proprement."""
        adapter._client.search_series.side_effect = requests.exceptions.Timeout("slow")
        with pytest.raises(C411Error, match="séries KO"):
            adapter.search_series("The Bear", season=3)
