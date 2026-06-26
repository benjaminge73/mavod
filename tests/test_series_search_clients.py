"""Tests des clients de recherche série (Prowlarr/C411) — `search` mockée."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from torrents_search_download.c411_api_client import C411APIClient
from torrents_search_download.prowlarr_client import ProwlarrClient, _merge_dedup

pytestmark = pytest.mark.unit


def _r(title: str, infohash: str = "") -> dict:
    return {"title": title, "infoHash": infohash, "downloadUrl": f"magnet:{title}"}


class TestMergeDedup:
    def test_dedups_by_infohash(self):
        """Même infoHash → un seul résultat, ordre primary préservé."""
        out = _merge_dedup([_r("A", "h1")], [_r("A bis", "h1"), _r("B", "h2")])
        assert [r["title"] for r in out] == ["A", "B"]

    def test_dedups_by_url_when_no_infohash(self):
        """Sans infoHash, dédup sur downloadUrl."""
        out = _merge_dedup([_r("A")], [_r("A"), _r("B")])
        assert len(out) == 2


class TestProwlarrSeries:
    def _client(self) -> ProwlarrClient:
        c = ProwlarrClient(api_url="http://prowlarr", api_key="k")
        c.search = MagicMock(return_value=[])
        return c

    def test_episode_query_added_and_merged(self):
        """Épisode demandé → requête S{ss} + requête S{ss}E{ee}, fusionnées/dédupliquées."""
        c = self._client()
        c.search.side_effect = [
            [_r("Widows.Bay.S01.1080p", "pack")],          # requête saison
            [_r("Widows.Bay.S01E01.1080p", "ep"),
             _r("Widows.Bay.S01.1080p", "pack")],          # requête épisode (doublon pack)
        ]
        out = c.search_series("Widows Bay", season=1, episode=1)
        queries = [call.args[0] for call in c.search.call_args_list]
        assert queries == ["Widows Bay S01", "Widows Bay S01E01"]
        assert {r["title"] for r in out} == {"Widows.Bay.S01.1080p", "Widows.Bay.S01E01.1080p"}

    def test_no_episode_single_season_query(self):
        """Sans épisode → une seule requête saison."""
        c = self._client()
        c.search.return_value = [_r("Widows.Bay.S01", "x")]
        c.search_series("Widows Bay", season=1)
        assert [call.args[0] for call in c.search.call_args_list] == ["Widows Bay S01"]

    def test_imdb_id_strips_tokens(self):
        """Avec imdb_id → titre seul + propagation imdbid, pas de token saison/épisode."""
        c = self._client()
        c.search_series("Widows Bay", season=1, episode=1, imdb_id="tt1234567")
        c.search.assert_called_once_with("Widows Bay", categories=[5000], imdb_id="tt1234567")


class TestC411Series:
    def _client(self) -> C411APIClient:
        c = C411APIClient(api_key="k", api_url="http://c411/api", passkey="p")
        c.search = MagicMock(return_value=[])
        return c

    def test_season_aware_query_first(self):
        """Saison fournie → requête 'Title S{NN}' d'abord."""
        c = self._client()
        c.search.return_value = [_r("Widows.Bay.S01")]
        c.search_series("Widows Bay", season=1)
        assert c.search.call_args.args[0] == "Widows Bay S01"

    def test_falls_back_to_title_only_when_empty(self):
        """Requête saison vide → fallback titre seul."""
        c = self._client()
        c.search.side_effect = [[], [_r("Widows.Bay")]]
        out = c.search_series("Widows Bay", season=1)
        queries = [call.args[0] for call in c.search.call_args_list]
        assert queries == ["Widows Bay S01", "Widows Bay"]
        assert len(out) == 1
