"""Tests des types domain (Intent, ClarificationRequest, Torrent, WorkflowResult)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mavod.domain import (
    ClarificationRequest,
    Intent,
    QbSubmitResult,
    RankingDecision,
    Torrent,
    TorrentFile,
    WorkflowResult,
)
from mavod.domain.workflow_result import SCHEMA_VERSION
from mavod.exceptions import IntentValidationError

pytestmark = pytest.mark.unit


# ─── Intent ──────────────────────────────────────────────────────────────────

class TestIntent:
    def test_movie_minimal(self):
        """Crée un Intent film minimal."""
        i = Intent(title="Dune", type="movie", year=2021)
        assert i.title == "Dune"
        assert i.episode is None

    def test_serie_with_episode(self):
        """Crée un Intent série avec épisode."""
        i = Intent(title="The Bear", type="serie", season=3, episode=4, year=2024)
        assert i.episode == 4

    def test_empty_title_raises(self):
        """Lève une erreur si le titre est vide."""
        with pytest.raises(IntentValidationError):
            Intent(title="", type="movie")
        with pytest.raises(IntentValidationError):
            Intent(title="   ", type="movie")

    def test_invalid_type_raises(self):
        """Lève une erreur sur un type invalide."""
        with pytest.raises(IntentValidationError):
            Intent(title="X", type="podcast")  # type: ignore[arg-type]

    def test_movie_with_episode_raises(self):
        """Un film ne peut pas avoir d'épisode."""
        with pytest.raises(IntentValidationError):
            Intent(title="Dune", type="movie", episode=1)

    def test_invalid_year(self):
        """Lève une erreur pour une année invalide."""
        with pytest.raises(IntentValidationError):
            Intent(title="X", type="movie", year="2021")  # type: ignore[arg-type]

    def test_imdb_pattern(self):
        """Valide le format d'un identifiant IMDb."""
        Intent(title="X", type="movie", imdb_id="tt1234567")
        Intent(title="X", type="movie", imdb_id="tt12345678")
        with pytest.raises(IntentValidationError):
            Intent(title="X", type="movie", imdb_id="1234567")
        with pytest.raises(IntentValidationError):
            Intent(title="X", type="movie", imdb_id="tt12")

    def test_from_dict(self):
        """Reconstruit un Intent depuis un dict."""
        i = Intent.from_dict({"title": " Dune ", "type": "movie", "year": 2021})
        assert i.title == "Dune"
        assert i.year == 2021

    def test_from_dict_string_null_sentinels_become_none(self):
        """Les sentinelles LLM ('null'/'none'/'') en string → None (pas de crash)."""
        i = Intent.from_dict({
            "title": "Widows Bay", "type": "serie",
            "season": 1, "episode": 1, "year": 2026,
            "imdb_id": "null",  # LLM a rendu la chaîne au lieu du null JSON
        })
        assert i.imdb_id is None
        assert i.season == 1 and i.episode == 1 and i.year == 2026

    def test_from_dict_drops_malformed_imdb_id(self):
        """Un imdb_id mal formé (hallucination LLM) est droppé, pas bloquant."""
        i = Intent.from_dict({"title": "X", "type": "movie", "imdb_id": "tt12"})
        assert i.imdb_id is None
        j = Intent.from_dict({"title": "X", "type": "movie", "imdb_id": "  tt1234567 "})
        assert j.imdb_id == "tt1234567"

    def test_from_dict_coerces_int_strings(self):
        """Les entiers rendus en string sont coercés (year/season/episode)."""
        i = Intent.from_dict({
            "title": "X", "type": "serie",
            "year": "2026", "season": "1", "episode": "none",
        })
        assert i.year == 2026 and i.season == 1 and i.episode is None

    def test_frozen(self):
        """L'Intent est immuable."""
        i = Intent(title="X", type="movie")
        with pytest.raises((AttributeError, Exception)):
            i.title = "Y"  # type: ignore[misc]


# ─── ClarificationRequest ────────────────────────────────────────────────────

class TestClarificationRequest:
    def test_basic(self):
        """Crée une ClarificationRequest basique."""
        c = ClarificationRequest(
            question="Capra ou Benigni ?",
            options=("Capra 1946", "Benigni 1997"),
            missing_field="disambiguation",
            tool_call_id="call_1",
        )
        assert c.options == ("Capra 1946", "Benigni 1997")

    def test_empty_question_raises(self):
        """Lève une erreur si la question est vide."""
        with pytest.raises(IntentValidationError):
            ClarificationRequest(question="")


# ─── Torrent ─────────────────────────────────────────────────────────────────

class TestTorrent:
    def test_basic(self):
        """Crée un Torrent basique."""
        t = Torrent(
            title="Dune.2021.1080p.MULTi.BluRay.x265-FOO",
            indexer="Prowlarr:YTS",
            size_bytes=10 * 1024 ** 3,
            seeders=42,
        )
        assert t.size_gb == pytest.approx(10.0)
        assert t.num_files == 1  # no files → assumed single

    def test_size_gb(self):
        """Calcule la taille en GB."""
        t = Torrent(title="x", indexer="y", size_bytes=5 * 1024 ** 3, seeders=1)
        assert t.size_gb == pytest.approx(5.0)

    def test_with_files(self):
        """Construit un Torrent avec liste de fichiers."""
        files = [
            TorrentFile(name="ep1.mkv", size_bytes=2 * 1024 ** 3),
            TorrentFile(name="ep2.mkv", size_bytes=3 * 1024 ** 3),
        ]
        t = Torrent(title="x", indexer="y", size_bytes=5 * 1024 ** 3, seeders=1).with_files(files)
        assert t.num_files == 2
        assert t.files[0].size_gb == pytest.approx(2.0)
        assert t.has_metadata

    def test_with_score(self):
        """Annote un Torrent avec un score."""
        t = Torrent(title="x", indexer="y", size_bytes=1, seeders=1)
        assert t.score is None
        scored = t.with_score(12.5)
        assert scored.score == 12.5

    def test_with_episode_match(self):
        """Annote un Torrent avec un flag episode_match."""
        t = Torrent(title="x", indexer="y", size_bytes=1, seeders=1)
        assert t.episode_match is None
        matched = t.with_episode_match(True)
        assert matched.episode_match is True

    def test_frozen(self):
        """Le Torrent est immuable."""
        t = Torrent(title="x", indexer="y", size_bytes=1, seeders=1)
        with pytest.raises((AttributeError, Exception)):
            t.title = "y"  # type: ignore[misc]


# ─── RankingDecision ─────────────────────────────────────────────────────────

class TestRankingDecision:
    def test_has_choice(self):
        """RankingDecision avec un choix valide."""
        t = Torrent(title="x", indexer="y", size_bytes=1, seeders=1)
        d = RankingDecision(ranked=[t], best=t)
        assert d.has_choice

    def test_no_choice(self):
        """RankingDecision sans choix retenu."""
        d = RankingDecision(ranked=[], best=None)
        assert not d.has_choice


# ─── WorkflowResult ──────────────────────────────────────────────────────────

class TestWorkflowResult:
    def test_serialize(self, tmp_path: Path):
        """Sérialise un WorkflowResult vers JSON."""
        r = WorkflowResult(
            schema_version=SCHEMA_VERSION,
            search_id="20260519_120000_dune",
            title="Dune",
            media_type="movie",
            year=2021,
            candidates=({"title": "candidate_1"},),
            best_choice={"title": "best"},
            qb_submit=QbSubmitResult(
                infohash="abcdef" * 7, name="Dune.2021", submitted_at=123.0
            ),
        )
        out = tmp_path / "result.json"
        r.write(out)
        loaded = json.loads(out.read_text())
        assert loaded["schema_version"] == 2
        assert loaded["title"] == "Dune"
        assert loaded["qb_submit"]["infohash"].startswith("abcdef")
