"""Tests de mavod.services.dedup — déduplication cross-tracker."""

from __future__ import annotations

import pytest

from mavod.domain import Torrent
from mavod.services.dedup import dedup_torrents, normalize_release_name

pytestmark = pytest.mark.unit


GB = 1024 ** 3


def _t(
    title: str,
    *,
    indexer: str = "Prowlarr:X",
    size_gb: float = 20.0,
    seeders: int = 10,
    infohash: str | None = None,
    magnet: str | None = None,
    torrent_url: str | None = None,
) -> Torrent:
    return Torrent(
        title=title,
        indexer=indexer,
        size_bytes=int(size_gb * GB),
        seeders=seeders,
        infohash=infohash,
        magnet=magnet,
        torrent_url=torrent_url,
    )


class TestNormalizeReleaseName:
    def test_separators_are_equivalent(self):
        """Points, tirets et espaces décrivent la même release."""
        assert normalize_release_name("Show.S01.1080p.WEB-DL.x265-GRP") == (
            normalize_release_name("Show S01 1080p WEB DL x265 GRP")
        )

    def test_tracker_tag_is_stripped(self):
        """Le tag de tracker collé par l'indexer ne doit pas différencier."""
        assert normalize_release_name("Show.S01.1080p [www.torrent9.xx]") == (
            normalize_release_name("Show.S01.1080p")
        )

    def test_release_identity_is_preserved(self):
        """Résolution, saison et groupe restent discriminants."""
        assert normalize_release_name("Show.S01.1080p-GRP") != normalize_release_name(
            "Show.S01.720p-GRP"
        )
        assert normalize_release_name("Show.S01E01") != normalize_release_name("Show.S01E02")
        assert normalize_release_name("Show.S01.1080p-GRPA") != normalize_release_name(
            "Show.S01.1080p-GRPB"
        )

    def test_empty_title(self):
        assert normalize_release_name("") == ""
        assert normalize_release_name(None) == ""


class TestDedupTorrents:
    def test_same_infohash_different_trackers(self):
        """Règle 1 : même infohash → une seule copie."""
        pool = [
            _t("Show S01 1080p", indexer="A", infohash="DEADBEEF"),
            _t("Show.S01.1080p", indexer="B", infohash="deadbeef"),
        ]
        assert len(dedup_torrents(pool)) == 1

    def test_same_release_three_trackers_without_infohash(self):
        """Le bug remonté : 3 copies d'une série, aucun infohash, 3 trackers."""
        pool = [
            _t("Show.S01.MULTI.1080p.WEB-DL.x265-GRP", indexer="YGG", seeders=5),
            _t("Show S01 MULTI 1080p WEB DL x265 GRP", indexer="1337x", seeders=40),
            _t("Show.S01.MULTI.1080p.WEB.DL.x265-GRP [www.torrent9.xx]",
               indexer="Torrent9", seeders=2),
        ]
        out = dedup_torrents(pool)
        assert len(out) == 1
        # On garde la copie la mieux seedée…
        assert out[0].indexer == "1337x"
        assert out[0].seeders == 40
        # …et on trace les trackers écartés.
        assert out[0].extra["duplicate_count"] == 3
        assert set(out[0].extra["duplicate_indexers"]) == {"YGG", "Torrent9"}

    def test_size_rounding_tolerated(self):
        """Même nom, taille à 1 % près (arrondi tracker) → doublon."""
        pool = [
            _t("Show.S01.1080p", indexer="A", size_gb=20.0),
            _t("Show S01 1080p", indexer="B", size_gb=20.15),
        ]
        assert len(dedup_torrents(pool)) == 1

    def test_same_name_very_different_size_kept(self):
        """Même nom mais taille très différente → releases distinctes, on garde tout."""
        pool = [
            _t("Show.S01.1080p", indexer="A", size_gb=20.0),
            _t("Show.S01.1080p", indexer="B", size_gb=6.0),
        ]
        assert len(dedup_torrents(pool)) == 2

    def test_exact_size_and_close_titles(self):
        """Règle 3 : taille identique à l'octet + titres très proches → doublon."""
        pool = [
            _t("The.Show.S01.1080p.WEB-DL.DDP5.1.x264-GRP", indexer="A", size_gb=20.0),
            _t("The Show S01 1080p WEBDL DDP5 1 x264 GRP", indexer="B", size_gb=20.0),
        ]
        assert len(dedup_torrents(pool)) == 1

    def test_marker_conflict_blocks_size_match(self):
        """Taille identique mais épisode différent → jamais fusionné."""
        pool = [
            _t("Show.S01E01.1080p.WEB-DL.x264-GRP", indexer="A", size_gb=2.0),
            _t("Show.S01E02.1080p.WEB-DL.x264-GRP", indexer="B", size_gb=2.0),
        ]
        assert len(dedup_torrents(pool)) == 2

    def test_resolution_conflict_blocks_size_match(self):
        """Taille identique mais résolution différente → deux releases."""
        pool = [
            _t("Show.S01.1080p.WEB-DL.x264-GRP", indexer="A", size_gb=20.0),
            _t("Show.S01.720p.WEBDL.x264-GRP", indexer="B", size_gb=20.0),
        ]
        assert len(dedup_torrents(pool)) == 2

    def test_language_conflict_blocks_size_match(self):
        """MULTi et VOSTFR du même pack restent deux candidats distincts."""
        pool = [
            _t("Severance.S02.MULTi.1080p.WEB.H264-GRP", indexer="A", size_gb=22.4),
            _t("Severance.S02.VOSTFR.1080p.WEB.H264-GRP", indexer="B", size_gb=22.4),
        ]
        assert len(dedup_torrents(pool)) == 2

    def test_different_releases_are_preserved(self):
        """Aucune fusion abusive entre épisodes, résolutions ou groupes."""
        pool = [
            _t("Show.S01E01.1080p-GRP", size_gb=2.0),
            _t("Show.S01E02.1080p-GRP", size_gb=2.0),
            _t("Show.S01.2160p-GRP", size_gb=60.0),
            _t("Show.S01.1080p-OTHER", size_gb=20.0),
        ]
        assert len(dedup_torrents(pool)) == 4

    def test_unknown_size_falls_back_on_name(self):
        """Taille inconnue (0) : le nom normalisé décide seul."""
        pool = [
            _t("Show.S01.1080p", indexer="A", size_gb=0),
            _t("Show S01 1080p", indexer="B", size_gb=20.0),
        ]
        assert len(dedup_torrents(pool)) == 1

    def test_prefers_directly_submittable_copy_at_equal_seeders(self):
        """À seeders égaux, on garde la copie la plus facile à envoyer à qB."""
        pool = [
            _t("Show.S01.1080p", indexer="A", seeders=10, torrent_url="http://a/x"),
            _t("Show.S01.1080p", indexer="B", seeders=10, magnet="magnet:?xt=urn:btih:ab"),
        ]
        out = dedup_torrents(pool)
        assert len(out) == 1
        assert out[0].indexer == "B"

    def test_order_is_preserved(self):
        """L'ordre Prowlarr (ranking server-side) survit à la dédup."""
        pool = [
            _t("Alpha.S01.1080p", indexer="A"),
            _t("Beta.S01.1080p", indexer="B"),
            _t("Alpha S01 1080p", indexer="C"),
            _t("Gamma.S01.1080p", indexer="D"),
        ]
        out = dedup_torrents(pool)
        assert [t.title.split(".")[0].split(" ")[0] for t in out] == ["Alpha", "Beta", "Gamma"]

    def test_unique_torrent_is_untouched(self):
        """Un candidat sans doublon ne gagne pas d'annotation."""
        pool = [_t("Show.S01.1080p")]
        out = dedup_torrents(pool)
        assert out[0] is pool[0]
        assert "duplicate_count" not in out[0].extra

    def test_empty_pool(self):
        assert dedup_torrents([]) == []
