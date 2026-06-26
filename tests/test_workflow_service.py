"""Tests de mavod.services.workflow_service."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mavod.config import load_settings
from mavod.domain import (
    Intent,
    QbSubmitResult,
    RankingDecision,
    Torrent,
)
from mavod.services.search_service import SearchOutcome
from mavod.services.workflow_service import (
    WorkflowService,
    build_search_id,
    sanitize_filename,
)

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
def settings(tmp_path):
    env = dict(_ENV)
    env["MAVOD_TORRENTS_DIR"] = str(tmp_path / "torrents")
    return load_settings(env=env)


def _t(name: str, infohash: str = None) -> Torrent:
    return Torrent(
        title=name,
        indexer="P:T",
        size_bytes=5 * 1024 ** 3,
        seeders=10,
        infohash=infohash or "deadbeef" * 5,
        magnet=f"magnet:?xt=urn:btih:{infohash or 'deadbeef'*5}&dn={name}",
    )


class TestHelpers:
    def test_sanitize_removes_specials(self):
        """Supprime les caractères spéciaux du titre."""
        assert sanitize_filename("foo/bar:baz") == "foo_bar_baz"

    def test_sanitize_truncates(self):
        """Tronque les titres trop longs."""
        assert len(sanitize_filename("x" * 500)) == 100

    def test_build_search_id_movie(self):
        """Construit le search_id pour un film."""
        intent = Intent(title="Dune", type="movie", year=2021)
        sid = build_search_id(intent, now=datetime(2026, 5, 19, 12, 30, 0))
        assert sid == "20260519_123000_Dune"

    def test_build_search_id_serie_with_episode(self):
        """Construit le search_id pour une série avec épisode."""
        intent = Intent(title="The Bear", type="serie", season=3, episode=4, year=2024)
        sid = build_search_id(intent, now=datetime(2026, 5, 19, 12, 30, 0))
        assert sid == "20260519_123000_The Bear_S03E04"

    def test_build_search_id_serie_no_episode(self):
        """Construit le search_id pour une série sans épisode."""
        intent = Intent(title="Lost", type="serie", season=1, year=2004)
        sid = build_search_id(intent, now=datetime(2026, 5, 19, 12, 30, 0))
        assert sid == "20260519_123000_Lost_S01"


class TestWorkflowService:
    def test_no_results_path(self, settings):
        """Gère le cas où aucun résultat n'est trouvé."""
        search = MagicMock()
        search.search.return_value = SearchOutcome(raw_pool=(), sources_used=())
        ranking = MagicMock()
        qb = MagicMock()

        svc = WorkflowService(settings, search=search, ranking=ranking, qb=qb)
        result = svc.run(Intent(title="X", type="movie"))

        assert result.error == "Aucun candidat trouvé"
        assert result.best_choice is None
        ranking.filter_and_score.assert_not_called()
        qb.add.assert_not_called()

    def test_no_candidates_after_filter(self, settings):
        """Gère le cas où le filtre ne laisse aucun candidat."""
        search = MagicMock()
        search.search.return_value = SearchOutcome(raw_pool=(_t("a"),), sources_used=("prowlarr",))
        ranking = MagicMock()
        ranking.filter_and_score.return_value = []
        qb = MagicMock()

        svc = WorkflowService(settings, search=search, ranking=ranking, qb=qb)
        result = svc.run(Intent(title="X", type="movie"))

        assert result.best_choice is None
        assert "filtres" in (result.error or "")

    def test_success_with_qb_submit(self, settings):
        """Workflow complet avec soumission qBittorrent réussie."""
        torrent = _t("Best Choice")
        search = MagicMock()
        search.search.return_value = SearchOutcome(raw_pool=(torrent,), sources_used=("prowlarr",))
        ranking = MagicMock()
        ranking.filter_and_score.return_value = [torrent]
        ranking.rank.return_value = RankingDecision(
            ranked=(torrent,), best=torrent,
            reasoning="thinking", raw_response="…**Best choice:** Torrent 1",
        )
        qb = MagicMock()
        qb.add.return_value = "abc123"

        svc = WorkflowService(settings, search=search, ranking=ranking, qb=qb)
        result = svc.run(Intent(title="X", type="movie"), skip_qb=False)

        assert result.qb_submit is not None
        assert result.qb_submit.infohash == "abc123"
        assert result.best_choice["title"] == "Best Choice"
        assert result.error is None

    def test_skip_qb(self, settings):
        """Le flag skip_qb saute l'étape de soumission."""
        torrent = _t("X")
        search = MagicMock()
        search.search.return_value = SearchOutcome(raw_pool=(torrent,), sources_used=("prowlarr",))
        ranking = MagicMock()
        ranking.filter_and_score.return_value = [torrent]
        ranking.rank.return_value = RankingDecision(ranked=(torrent,), best=torrent)
        qb = MagicMock()

        svc = WorkflowService(settings, search=search, ranking=ranking, qb=qb)
        result = svc.run(Intent(title="X", type="movie"), skip_qb=True)

        assert result.qb_submit is None
        qb.add.assert_not_called()

    def test_persists_result_json(self, settings):
        """Persiste un result.json sur disque."""
        torrent = _t("X")
        search = MagicMock()
        search.search.return_value = SearchOutcome(raw_pool=(torrent,), sources_used=("prowlarr",))
        ranking = MagicMock()
        ranking.filter_and_score.return_value = [torrent]
        ranking.rank.return_value = RankingDecision(ranked=(torrent,), best=torrent)
        qb = MagicMock()
        qb.add.return_value = "h"

        svc = WorkflowService(settings, search=search, ranking=ranking, qb=qb)
        result = svc.run(Intent(title="Dune", type="movie", year=2021))

        out = settings.torrents_dir / result.search_id / "result.json"
        assert out.exists()
        loaded = json.loads(out.read_text())
        assert loaded["schema_version"] == 2
        assert loaded["title"] == "Dune"

    def test_submit_to_qb_routes_magnet_when_only_in_torrent_url(self, settings, monkeypatch):
        """Magnet égaré dans torrent_url : route vers qB sans HTTP get."""
        magnet = "magnet:?xt=urn:btih:C1AFBD67ED2746AFAF8223542B1A9E34CA64D069&dn=x"
        torrent = Torrent(
            title="Perfect Sense 2011",
            indexer="P:T",
            size_bytes=5 * 1024 ** 3,
            seeders=10,
            infohash="c1afbd67ed2746afaf8223542b1a9e34ca64d069",
            magnet=None,
            torrent_url=magnet,
        )
        qb = MagicMock()
        qb.add.return_value = "hash"

        import mavod.services.workflow_service as wf_mod
        called = {}

        def _fail_get(*a, **kw):
            called["get"] = True
            raise AssertionError("requests.get must not be called for magnet")

        monkeypatch.setattr("requests.get", _fail_get, raising=True)

        svc = WorkflowService(settings, search=MagicMock(), ranking=MagicMock(), qb=qb)
        result = svc._submit_to_qb(torrent, "sid-test")

        assert result is not None
        assert result.infohash == "hash"
        qb.add.assert_called_once()
        args, kwargs = qb.add.call_args
        assert args[0] == magnet
        assert "get" not in called

    def test_submit_to_qb_follows_http_to_magnet_redirect(self, settings, monkeypatch):
        """YTS via Prowlarr : HTTP 302 → magnet:, on extrait le magnet et on le passe à qB."""
        http_url = "https://prowlarr.example.com/1/download?apikey=k&hash=ABC"
        magnet_url = (
            "magnet:?xt=urn:btih:C1AFBD67ED2746AFAF8223542B1A9E34CA64D069"
            "&dn=Perfect+Sense+(2011)+1080p+BRRip+5.1+x264+-YTS"
            "&tr=udp%3a%2f%2ftracker.opentrackr.org%3a1337%2fannounce"
        )
        torrent = Torrent(
            title="Perfect Sense (2011) 1080p BRRip -YTS",
            indexer="Prowlarr:YTS",
            size_bytes=2 * 1024 ** 3,
            seeders=42,
            infohash="c1afbd67ed2746afaf8223542b1a9e34ca64d069",
            magnet=None,
            torrent_url=http_url,
        )
        qb = MagicMock()
        qb.add.return_value = "hash"

        captured = []

        def _fake_get(url, **kw):
            captured.append((url, kw.get("allow_redirects")))
            resp = MagicMock()
            resp.is_redirect = True
            resp.status_code = 302
            resp.headers = {"Location": magnet_url}
            return resp

        monkeypatch.setattr("requests.get", _fake_get, raising=True)

        svc = WorkflowService(settings, search=MagicMock(), ranking=MagicMock(), qb=qb)
        result = svc._submit_to_qb(torrent, "sid-test")

        assert result is not None
        assert result.infohash == "hash"
        assert captured == [(http_url, False)]
        qb.add.assert_called_once()
        args, _ = qb.add.call_args
        assert args[0] == magnet_url

    def test_submit_to_qb_http_returns_torrent_bytes(self, settings, monkeypatch):
        """URL HTTP qui répond 200 avec un .torrent : on passe les bytes à qB."""
        http_url = "https://indexer.example.com/dl/abc.torrent"
        torrent_bytes = b"d8:announce..."
        torrent = Torrent(
            title="Some Release",
            indexer="Prowlarr:X",
            size_bytes=1024,
            seeders=1,
            infohash="b" * 40,
            magnet=None,
            torrent_url=http_url,
        )
        qb = MagicMock()
        qb.add.return_value = "hash"

        def _fake_get(url, **kw):
            resp = MagicMock()
            resp.is_redirect = False
            resp.status_code = 200
            resp.headers = {}
            resp.content = torrent_bytes
            resp.raise_for_status = lambda: None
            return resp

        monkeypatch.setattr("requests.get", _fake_get, raising=True)

        svc = WorkflowService(settings, search=MagicMock(), ranking=MagicMock(), qb=qb)
        result = svc._submit_to_qb(torrent, "sid-test")

        assert result is not None
        qb.add.assert_called_once()
        args, _ = qb.add.call_args
        assert args[0] == torrent_bytes

    def test_submit_to_qb_follows_http_chain_then_magnet(self, settings, monkeypatch):
        """Chaîne HTTP→HTTP→magnet : on suit jusqu'au magnet final."""
        url1 = "https://a.example/dl"
        url2 = "https://b.example/dl"
        magnet_url = "magnet:?xt=urn:btih:" + "a" * 40
        torrent = Torrent(
            title="X", indexer="P:X", size_bytes=1, seeders=1,
            infohash="a" * 40, magnet=None, torrent_url=url1,
        )
        qb = MagicMock(); qb.add.return_value = "h"

        responses = iter([
            (302, {"Location": url2}),
            (302, {"Location": magnet_url}),
        ])

        def _fake_get(url, **kw):
            status, headers = next(responses)
            resp = MagicMock()
            resp.is_redirect = True
            resp.status_code = status
            resp.headers = headers
            return resp

        monkeypatch.setattr("requests.get", _fake_get, raising=True)

        svc = WorkflowService(settings, search=MagicMock(), ranking=MagicMock(), qb=qb)
        svc._submit_to_qb(torrent, "sid")

        args, _ = qb.add.call_args
        assert args[0] == magnet_url

    def test_submit_to_qb_skips_when_url_is_not_http(self, settings):
        """URL ni magnet ni http : skip propre, pas de crash."""
        torrent = Torrent(
            title="weird",
            indexer="P:T",
            size_bytes=1024,
            seeders=1,
            infohash="a" * 40,
            magnet=None,
            torrent_url="ftp://example.com/x.torrent",
        )
        qb = MagicMock()

        svc = WorkflowService(settings, search=MagicMock(), ranking=MagicMock(), qb=qb)
        result = svc._submit_to_qb(torrent, "sid-test")

        assert result is None
        qb.add.assert_not_called()

    def test_ui_url(self, settings):
        """Construit l'URL mavod-ui pour la recherche."""
        svc = WorkflowService(
            settings,
            search=MagicMock(),
            ranking=MagicMock(),
            qb=MagicMock(),
        )
        url = svc.ui_url("20260519_120000_Dune")
        assert url.endswith("/?search_id=20260519_120000_Dune")
