"""Unit tests for ui/data.py — the read-only data layer of mavod-ui."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
UI_DIR = REPO_ROOT / "ui"


def _load_data_module(tmp_path: Path):
    """Load ui/data.py as a fresh module pointing at tmp_path/torrents."""
    spec = importlib.util.spec_from_file_location("mavod_ui_data", UI_DIR / "data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.TORRENTS_ROOT = tmp_path / "torrents"
    module.invalidate_cache()
    return module


def _write_result(tmp_path: Path, search_id: str, payload: dict) -> Path:
    folder = tmp_path / "torrents" / search_id
    folder.mkdir(parents=True, exist_ok=True)
    result_path = folder / "result.json"
    result_path.write_text(json.dumps(payload), encoding="utf-8")
    return result_path


def _sample_payload(search_id: str = "20250101_120000_Dune") -> dict:
    return {
        "search_id": search_id,
        "query": "Dune (2021)",
        "type": "movie",
        "season": None,
        "episode": None,
        "torrents": [
            {"id": 1, "name": "Dune.2021.1080p", "size_gb": 8.5, "num_files": 1,
             "seeders": 120, "indexer": "Prowlarr:YGG", "path": "/tmp/a.torrent"},
            {"id": 2, "name": "Dune.2021.2160p.DV", "size_gb": 25.0, "num_files": 1,
             "seeders": 40, "indexer": "Prowlarr:YGG", "path": "/tmp/b.torrent"},
            {"id": 3, "name": "Dune.2021.720p.DTS", "size_gb": 0, "num_files": 1,
             "seeders": 2, "indexer": "C411", "path": None},
        ],
        "llm_choice_id": 2,
        "llm_choice_name": "Dune.2021.2160p.DV",
        "llm_response": "**Final ranking:** Torrent 2, Torrent 1, Torrent 3",
        "llm_reasoning": "Internal chain-of-thought: 2160p DV preferred.",
    }


# ── _extract_year ────────────────────────────────────────────────────────────

@pytest.mark.unit
def test_extract_year_finds_year_in_parens(tmp_path):
    """Extrait l'année entre parenthèses dans le titre."""
    data = _load_data_module(tmp_path)
    assert data._extract_year("Dune (2021)") == 2021


@pytest.mark.unit
def test_extract_year_finds_year_inline(tmp_path):
    """Extrait l'année inline dans le titre."""
    data = _load_data_module(tmp_path)
    assert data._extract_year("The Bear 2024 S03") == 2024


@pytest.mark.unit
def test_extract_year_none_when_absent(tmp_path):
    """Retourne None quand aucune année n'est trouvée."""
    data = _load_data_module(tmp_path)
    assert data._extract_year("No year here") is None


# ── _result_to_ui_dict ───────────────────────────────────────────────────────

@pytest.mark.unit
def test_result_to_ui_dict_marks_only_llm_choice(tmp_path):
    """Seul le choix LLM est marqué comme best."""
    data = _load_data_module(tmp_path)
    ui_dict = data._result_to_ui_dict(_sample_payload())
    llm_flags = {t["rank"]: t["llm"] for t in ui_dict["torrents"]}
    assert llm_flags == {1: False, 2: True, 3: False}


@pytest.mark.unit
def test_result_to_ui_dict_size_formatting(tmp_path):
    """Formate correctement les tailles en GB."""
    data = _load_data_module(tmp_path)
    ui_dict = data._result_to_ui_dict(_sample_payload())
    sizes = {t["rank"]: t["size"] for t in ui_dict["torrents"]}
    assert sizes[1] == "8.50 GB"
    assert sizes[2] == "25.00 GB"
    assert sizes[3] == "—"


@pytest.mark.unit
def test_result_to_ui_dict_carries_reasoning(tmp_path):
    """Transmet le raisonnement du LLM vers l'UI."""
    data = _load_data_module(tmp_path)
    ui_dict = data._result_to_ui_dict(_sample_payload())
    assert ui_dict["llm_choice"]["rank"] == 2
    assert ui_dict["llm_choice"]["name"] == "Dune.2021.2160p.DV"
    assert "Torrent 2" in ui_dict["llm_choice"]["reasoning"]
    assert ui_dict["year"] == 2021


@pytest.mark.unit
def test_result_to_ui_dict_exposes_reasoning_content(tmp_path):
    """Expose le reasoning_content de DeepSeek."""
    data = _load_data_module(tmp_path)
    ui_dict = data._result_to_ui_dict(_sample_payload())
    assert "chain-of-thought" in ui_dict["llm_choice"]["reasoning_content"]


@pytest.mark.unit
def test_result_to_ui_dict_reasoning_content_empty_when_missing(tmp_path):
    """Le reasoning_content vaut vide quand absent."""
    data = _load_data_module(tmp_path)
    payload = _sample_payload()
    payload.pop("llm_reasoning")
    ui_dict = data._result_to_ui_dict(payload)
    assert ui_dict["llm_choice"]["reasoning_content"] == ""


@pytest.mark.unit
def test_load_all_orders_newest_first(tmp_path):
    """Trie les recherches du plus récent au plus ancien."""
    _write_result(tmp_path, "20240101_120000_Old", _sample_payload("20240101_120000_Old"))
    _write_result(tmp_path, "20260101_120000_New", _sample_payload("20260101_120000_New"))
    _write_result(tmp_path, "20250101_120000_Mid", _sample_payload("20250101_120000_Mid"))

    data = _load_data_module(tmp_path)
    keys = list(data.get_all_searches().keys())

    assert keys == [
        "20260101_120000_New",
        "20250101_120000_Mid",
        "20240101_120000_Old",
    ]


# ── _load_all / get_all_searches ─────────────────────────────────────────────

@pytest.mark.unit
def test_get_all_searches_reads_result_json(tmp_path):
    """Lit les result.json depuis le dossier torrents."""
    _write_result(tmp_path, "20250101_120000_Dune", _sample_payload())
    data = _load_data_module(tmp_path)

    searches = data.get_all_searches()

    assert "20250101_120000_Dune" in searches
    assert searches["20250101_120000_Dune"]["title"] == "Dune"
    assert searches["20250101_120000_Dune"]["year"] == 2021


@pytest.mark.unit
def test_get_all_searches_skips_corrupt_json(tmp_path, capsys):
    """Ignore silencieusement les result.json corrompus."""
    _write_result(tmp_path, "good", _sample_payload("good"))
    bad_dir = tmp_path / "torrents" / "bad"
    bad_dir.mkdir(parents=True)
    (bad_dir / "result.json").write_text("{not valid json", encoding="utf-8")

    data = _load_data_module(tmp_path)
    searches = data.get_all_searches()

    assert set(searches.keys()) == {"good"}
    captured = capsys.readouterr()
    assert "Could not read" in captured.out


@pytest.mark.unit
def test_get_all_searches_empty_when_no_torrents_dir(tmp_path):
    """Retourne vide si le dossier torrents n'existe pas."""
    data = _load_data_module(tmp_path)
    assert data.get_all_searches() == {}


@pytest.mark.unit
def test_display_label_with_and_without_year(tmp_path):
    """Construit le label d'affichage avec et sans année."""
    payload_with = _sample_payload("with_year")
    payload_no_year = _sample_payload("no_year")
    payload_no_year["query"] = "Something Untitled"
    _write_result(tmp_path, "with_year", payload_with)
    _write_result(tmp_path, "no_year", payload_no_year)

    data = _load_data_module(tmp_path)
    assert data.display_label("with_year") == "Dune (2021)"
    assert data.display_label("no_year") == "Something Untitled"


@pytest.mark.unit
def test_result_to_ui_dict_strips_year_suffix_from_title(tmp_path):
    """`query` is written as 'Title (YYYY)' by workflow; UI must not double it."""
    data = _load_data_module(tmp_path)
    ui_dict = data._result_to_ui_dict(_sample_payload())
    assert ui_dict["title"] == "Dune"
    assert ui_dict["year"] == 2021


@pytest.mark.unit
def test_result_to_ui_dict_keeps_title_when_no_year(tmp_path):
    """Conserve le titre intact en l'absence d'année."""
    data = _load_data_module(tmp_path)
    payload = _sample_payload()
    payload["query"] = "Something Untitled"
    ui_dict = data._result_to_ui_dict(payload)
    assert ui_dict["title"] == "Something Untitled"
    assert ui_dict["year"] == ""


# ── download_torrent ─────────────────────────────────────────────────────────

@pytest.mark.unit
def test_download_torrent_unknown_search_raises(tmp_path):
    """Lève sur un search_id inconnu."""
    _write_result(tmp_path, "sid", _sample_payload("sid"))
    data = _load_data_module(tmp_path)
    with pytest.raises(ValueError, match="Unknown search_id"):
        data.download_torrent("does_not_exist", 1)


@pytest.mark.unit
def test_download_torrent_unknown_id_raises(tmp_path):
    """Lève sur un torrent_id inconnu."""
    _write_result(tmp_path, "sid", _sample_payload("sid"))
    data = _load_data_module(tmp_path)
    with pytest.raises(ValueError, match="Torrent id 999 not found"):
        data.download_torrent("sid", 999)


@pytest.mark.unit
def test_download_torrent_no_source_raises(tmp_path):
    """Lève quand aucune source (path/magnet/url) n'est disponible."""
    _write_result(tmp_path, "sid", _sample_payload("sid"))
    data = _load_data_module(tmp_path)
    # Torrent id=3 has path=None and no magnet/url in the fixture
    with pytest.raises(FileNotFoundError, match="Aucune source soumissible"):
        data.download_torrent("sid", 3)


@pytest.mark.unit
def test_download_torrent_missing_file_raises(tmp_path):
    """Lève quand le fichier torrent est manquant sur disque."""
    payload = _sample_payload("sid")
    payload["torrents"][0]["path"] = str(tmp_path / "ghost.torrent")
    _write_result(tmp_path, "sid", payload)
    data = _load_data_module(tmp_path)
    with pytest.raises(FileNotFoundError, match="Fichier introuvable"):
        data.download_torrent("sid", 1)


@pytest.mark.unit
def test_download_torrent_pushes_bytes_to_qb(tmp_path, monkeypatch):
    """Pousse les bytes du torrent vers qBittorrent avec le tag override."""
    torrent_bytes = b"d8:announce..."
    torrent_path = tmp_path / "real.torrent"
    torrent_path.write_bytes(torrent_bytes)

    payload = _sample_payload("sid")
    payload["torrents"][0]["path"] = str(torrent_path)
    _write_result(tmp_path, "sid", payload)
    data = _load_data_module(tmp_path)

    mock_client = MagicMock()
    mock_client.add_torrent.return_value = "abc123def456"

    fake_qb_module = MagicMock()
    fake_qb_module.QBittorrentClient.from_env.return_value = mock_client
    monkeypatch.setitem(sys.modules, "mavod.qbittorrent_client", fake_qb_module)

    result = data.download_torrent("sid", 1)

    assert result == "abc123def456"
    mock_client.add_torrent.assert_called_once()
    call_args, call_kwargs = mock_client.add_torrent.call_args
    assert call_args[0] == torrent_bytes
    assert call_kwargs.get("tags") == "ui-override"


# ── Schéma V2 (workflow_result.schema_version=2) ─────────────────────────────


def _v2_payload(search_id: str = "20260519_120000_Dune") -> dict:
    return {
        "schema_version": 2,
        "search_id": search_id,
        "title": "Dune",
        "media_type": "movie",
        "year": 2021,
        "candidates": [
            {"title": "Dune.2021.1080p.BluRay", "size_gb": 8.5, "seeders": 42, "indexer": "P:T"},
            {"title": "Dune.2021.720p", "size_gb": 4.0, "seeders": 5, "indexer": "P:T"},
        ],
        "best_choice": {"title": "Dune.2021.1080p.BluRay"},
        "llm_reasoning": "thinking",
        "llm_response": "**Best choice:** Torrent 1",
    }


@pytest.mark.unit
def test_v2_result_basic_fields(tmp_path):
    """Lit les champs de base d'un result.json schema v2."""
    data = _load_data_module(tmp_path)
    ui = data._result_to_ui_dict(_v2_payload())
    assert ui["title"] == "Dune"
    assert ui["year"] == 2021
    assert ui["type"] == "movie"
    assert len(ui["torrents"]) == 2


@pytest.mark.unit
def test_v2_best_choice_marks_only_first(tmp_path):
    """Seul le premier choix est marqué best en v2."""
    data = _load_data_module(tmp_path)
    ui = data._result_to_ui_dict(_v2_payload())
    assert ui["torrents"][0]["llm"] is True
    assert ui["torrents"][1]["llm"] is False


@pytest.mark.unit
def test_v2_reasoning_propagated(tmp_path):
    """Le raisonnement v2 est propagé vers l'UI."""
    data = _load_data_module(tmp_path)
    ui = data._result_to_ui_dict(_v2_payload())
    assert ui["llm_choice"]["reasoning_content"] == "thinking"


@pytest.mark.unit
def test_v2_serie_with_episode(tmp_path):
    """Lit un result v2 série avec épisode."""
    data = _load_data_module(tmp_path)
    payload = {
        "schema_version": 2,
        "search_id": "20260519_120000_X_S03E04",
        "title": "The Bear",
        "media_type": "serie",
        "year": 2024,
        "season": 3,
        "episode": 4,
        "candidates": [
            {"title": "The.Bear.S03E04", "size_gb": 2.0, "seeders": 10, "indexer": "P:T"},
        ],
        "best_choice": {"title": "The.Bear.S03E04"},
        "llm_response": "",
        "llm_reasoning": "",
    }
    ui = data._result_to_ui_dict(payload)
    assert ui["type"] == "serie"
    assert ui["season"] == 3
    assert ui["episode"] == 4


@pytest.mark.unit
def test_v2_no_best_choice_marks_none(tmp_path):
    """Aucun torrent n'est marqué best si pas de choix v2."""
    data = _load_data_module(tmp_path)
    payload = {
        "schema_version": 2,
        "search_id": "x",
        "title": "X",
        "media_type": "movie",
        "candidates": [{"title": "X.1080p", "size_gb": 5.0, "seeders": 3}],
        "best_choice": None,
        "llm_response": "",
    }
    ui = data._result_to_ui_dict(payload)
    assert ui["torrents"][0]["llm"] is False


@pytest.mark.unit
def test_v1_v2_dispatch_in_get_all_searches(tmp_path):
    """get_all_searches() doit lire v1 ET v2 sans confusion."""
    _write_result(tmp_path, "20260519_120000_v1", _sample_payload("20260519_120000_v1"))
    _write_result(tmp_path, "20260519_130000_v2", {
        "schema_version": 2,
        "search_id": "20260519_130000_v2",
        "title": "Inception",
        "media_type": "movie",
        "year": 2010,
        "candidates": [{"title": "Inception", "size_gb": 5, "seeders": 8, "indexer": "p"}],
        "best_choice": {"title": "Inception"},
        "llm_response": "",
        "llm_reasoning": "",
    })
    data = _load_data_module(tmp_path)
    all_searches = data.get_all_searches()
    assert "20260519_120000_v1" in all_searches
    assert "20260519_130000_v2" in all_searches
    assert all_searches["20260519_130000_v2"]["title"] == "Inception"


# ── download_torrent : sources V2 (magnet / url) ─────────────────────────────


def _v2_payload_with_sources(search_id: str = "20260601_120000_Dune") -> dict:
    """result.json v2 réaliste : candidats avec magnet et torrent_url (pas de path)."""
    return {
        "schema_version": 2,
        "search_id": search_id,
        "title": "Dune",
        "media_type": "movie",
        "year": 2021,
        "candidates": [
            {"title": "Dune.2021.1080p.BluRay", "size_gb": 8.5, "seeders": 42,
             "indexer": "P:T", "magnet": "magnet:?xt=urn:btih:aaaa", "torrent_url": None},
            {"title": "Dune.2021.2160p", "size_gb": 25.0, "seeders": 12,
             "indexer": "P:T", "magnet": None,
             "torrent_url": "https://prowlarr.example/dl/dune.torrent"},
            {"title": "Dune.2021.720p", "size_gb": 4.0, "seeders": 3,
             "indexer": "P:T", "magnet": None, "torrent_url": None},
        ],
        "best_choice": {"title": "Dune.2021.1080p.BluRay"},
        "llm_response": "**Best choice:** Torrent 1",
        "llm_reasoning": "",
    }


def _patch_qb(monkeypatch, return_hash: str = "deadbeef"):
    """Injecte un faux mavod.qbittorrent_client et retourne le mock client."""
    mock_client = MagicMock()
    mock_client.add_torrent.return_value = return_hash
    fake_qb_module = MagicMock()
    fake_qb_module.QBittorrentClient.from_env.return_value = mock_client
    monkeypatch.setitem(sys.modules, "mavod.qbittorrent_client", fake_qb_module)
    return mock_client


@pytest.mark.unit
def test_v2_candidates_expose_magnet_and_url(tmp_path):
    """Les candidats v2 exposent magnet et url vers l'UI."""
    data = _load_data_module(tmp_path)
    ui = data._result_to_ui_dict(_v2_payload_with_sources())
    assert ui["torrents"][0]["magnet"] == "magnet:?xt=urn:btih:aaaa"
    assert ui["torrents"][1]["url"] == "https://prowlarr.example/dl/dune.torrent"
    assert ui["torrents"][2]["magnet"] is None
    assert ui["torrents"][2]["url"] is None


@pytest.mark.unit
def test_download_torrent_pushes_magnet_to_qb(tmp_path, monkeypatch):
    """Pousse le magnet d'un candidat v2 vers qBittorrent avec le tag override."""
    _write_result(tmp_path, "sid", _v2_payload_with_sources("sid"))
    data = _load_data_module(tmp_path)
    mock_client = _patch_qb(monkeypatch, "abc123")

    result = data.download_torrent("sid", 1)

    assert result == "abc123"
    call_args, call_kwargs = mock_client.add_torrent.call_args
    assert call_args[0] == "magnet:?xt=urn:btih:aaaa"
    assert call_kwargs.get("tags") == "ui-override"


@pytest.mark.unit
def test_download_torrent_pushes_http_url_bytes_to_qb(tmp_path, monkeypatch):
    """Un torrent_url HTTP 200 est résolu en bytes puis poussé à qBittorrent."""
    _write_result(tmp_path, "sid", _v2_payload_with_sources("sid"))
    data = _load_data_module(tmp_path)
    mock_client = _patch_qb(monkeypatch)

    fake_resp = MagicMock()
    fake_resp.is_redirect = False
    fake_resp.status_code = 200
    fake_resp.content = b"d8:announce..."
    monkeypatch.setattr("requests.get", lambda *a, **k: fake_resp)

    data.download_torrent("sid", 2)

    call_args, _ = mock_client.add_torrent.call_args
    assert call_args[0] == b"d8:announce..."


@pytest.mark.unit
def test_download_torrent_follows_http_to_magnet_redirect(tmp_path, monkeypatch):
    """Un torrent_url HTTP qui redirige 302 vers magnet est résolu en magnet."""
    _write_result(tmp_path, "sid", _v2_payload_with_sources("sid"))
    data = _load_data_module(tmp_path)
    mock_client = _patch_qb(monkeypatch)

    fake_resp = MagicMock()
    fake_resp.is_redirect = True
    fake_resp.status_code = 302
    fake_resp.headers = {"Location": "magnet:?xt=urn:btih:bbbb"}
    monkeypatch.setattr("requests.get", lambda *a, **k: fake_resp)

    data.download_torrent("sid", 2)

    call_args, _ = mock_client.add_torrent.call_args
    assert call_args[0] == "magnet:?xt=urn:btih:bbbb"


@pytest.mark.unit
def test_download_torrent_prefers_magnet_over_path(tmp_path, monkeypatch):
    """Le magnet prime sur un path disque legacy s'il est présent."""
    real = tmp_path / "real.torrent"
    real.write_bytes(b"bytes")
    payload = _sample_payload("sid")
    payload["torrents"][0]["path"] = str(real)
    payload["torrents"][0]["magnet"] = "magnet:?xt=urn:btih:cccc"
    _write_result(tmp_path, "sid", payload)
    data = _load_data_module(tmp_path)
    mock_client = _patch_qb(monkeypatch)

    data.download_torrent("sid", 1)

    call_args, _ = mock_client.add_torrent.call_args
    assert call_args[0] == "magnet:?xt=urn:btih:cccc"
