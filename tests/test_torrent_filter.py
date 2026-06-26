"""Tests du filtre multi-saisons de torrents_search_download.torrent_filter."""

from __future__ import annotations

import pytest

from torrents_search_download.torrent_filter import filter_candidates

pytestmark = pytest.mark.unit


def _raw(title: str) -> dict:
    """Résultat brut TV minimal qui passe type/langue/qualité."""
    return {
        "title": title,
        "categories": [5000],
        "downloadUrl": "magnet:?xt=urn:btih:" + "a" * 40,
        "size": 5 * 1024 ** 3,
        "seeders": 10,
    }


def _titles(results) -> set:
    return {r["title"] for r in results}


class TestMultiSeasonFilter:
    def test_single_season_pack_spelled_out_survives(self):
        """Un pack mono-saison nommé 'Season 1 1080p' ne doit PAS être pris pour du multi-saisons."""
        raw = [
            _raw("Widows Bay Season 1 1080p WEB-DL MULTi x265"),
            _raw("Widows Bay Saison 1 1080p MULTi"),
            _raw("Widows.Bay.S01.1080p.WEB-DL.MULTi.x265"),
            _raw("Widows.Bay.S01E01-08.1080p.MULTi"),
        ]
        kept, _ = filter_candidates(raw, "serie", season=1, search_title="Widows Bay", verbose=False)
        assert _titles(kept) == {r["title"] for r in raw}

    def test_multi_season_pack_excluded(self):
        """Les vrais packs multi-saisons / intégrales sont écartés quand on cible une saison."""
        raw = [
            _raw("Widows Bay S01-S05 COMPLETE 1080p MULTi"),
            _raw("Widows Bay Season 1-5 Complete 1080p"),
            _raw("Widows Bay Intégrale 1080p MULTi"),
            _raw("Widows Bay Complete Series 1080p"),
        ]
        kept, _ = filter_candidates(raw, "serie", season=1, search_title="Widows Bay", verbose=False)
        assert kept == []

    def test_single_episode_survives(self):
        """Un épisode isolé de la saison ciblée reste candidat."""
        raw = [_raw("Widows.Bay.S01E05.1080p.MULTi.x265")]
        kept, _ = filter_candidates(raw, "serie", season=1, search_title="Widows Bay", verbose=False)
        assert len(kept) == 1


class TestEpisodeHardFilter:
    """Quand un épisode précis est demandé, un mauvais épisode unique est exclu."""

    def test_wrong_single_episode_excluded(self):
        """On veut S01E05 : S01E04 et S01E09 sont écartés."""
        raw = [
            _raw("Widows.Bay.S01E04.MULTI.VFF.2160p.WEB.DV.H265-TFA"),
            _raw("Widows.Bay.S01E09.MULTI.VFF.2160p.WEB.DV.H265-TFA"),
        ]
        kept, _ = filter_candidates(
            raw, "serie", season=1, episode=5, search_title="Widows Bay", verbose=False
        )
        assert kept == []

    def test_exact_episode_kept(self):
        """L'épisode exact demandé reste candidat."""
        raw = [_raw("Widows.Bay.S01E05.MULTI.VFF.2160p.WEB.DV.H265-TFA")]
        kept, _ = filter_candidates(
            raw, "serie", season=1, episode=5, search_title="Widows Bay", verbose=False
        )
        assert len(kept) == 1

    def test_season_pack_kept(self):
        """Un pack de saison (sans marqueur d'épisode) peut contenir l'épisode → gardé."""
        raw = [_raw("Widows.Bay.S01.MULTI.VFF.2160p.WEB.DV.H265-TFA")]
        kept, _ = filter_candidates(
            raw, "serie", season=1, episode=5, search_title="Widows Bay", verbose=False
        )
        assert len(kept) == 1

    def test_range_covering_episode_kept(self):
        """Une plage S01E01-08 couvre E05 → gardée ; une plage E06-09 ne le couvre pas → exclue."""
        raw = [
            _raw("Widows.Bay.S01E01-08.1080p.MULTi"),
            _raw("Widows.Bay.S01E06-E09.1080p.MULTi"),
        ]
        kept, _ = filter_candidates(
            raw, "serie", season=1, episode=5, search_title="Widows Bay", verbose=False
        )
        assert _titles(kept) == {"Widows.Bay.S01E01-08.1080p.MULTi"}

    def test_mixed_pool_only_episode_and_packs_survive(self):
        """Pool mixte : seuls E05 + pack survivent, les autres épisodes tombent."""
        raw = [
            _raw("Widows.Bay.S01E03.2160p.WEB.H265-TFA"),
            _raw("Widows.Bay.S01E05.2160p.WEB.H265-TFA"),
            _raw("Widows.Bay.S01E09.2160p.WEB.H265-TFA"),
            _raw("Widows.Bay.S01.COMPLETE.2160p.WEB.H265-TFA"),
        ]
        kept, _ = filter_candidates(
            raw, "serie", season=1, episode=5, search_title="Widows Bay", verbose=False
        )
        assert _titles(kept) == {
            "Widows.Bay.S01E05.2160p.WEB.H265-TFA",
            "Widows.Bay.S01.COMPLETE.2160p.WEB.H265-TFA",
        }

    def test_no_episode_requested_is_noop(self):
        """Sans épisode demandé, aucun épisode n'est exclu (rétro-compat)."""
        raw = [
            _raw("Widows.Bay.S01E04.1080p.MULTi"),
            _raw("Widows.Bay.S01E05.1080p.MULTi"),
        ]
        kept, _ = filter_candidates(
            raw, "serie", season=1, search_title="Widows Bay", verbose=False
        )
        assert len(kept) == 2
