"""Tests end-to-end V2 — workflow complet avec APIs externes réelles.

Exécution :
    RUN_E2E=1 pytest -m e2e -v

Ces tests appellent réellement le LLM + Prowlarr (mais skip_qb=True
pour ne pas pousser le torrent vers le qBittorrent distant).

Coût estimé : ~$0.01 / run LLM. Durée : ~30-60s par test.
"""

from __future__ import annotations

import os

import pytest

from mavod.config import load_settings
from mavod.domain import Intent, WorkflowResult
from mavod.services.workflow_service import WorkflowService

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("RUN_E2E") != "1",
        reason="E2E skipped — set RUN_E2E=1 to enable",
    ),
]


@pytest.fixture(scope="module")
def settings(tmp_path_factory):
    """Settings depuis l'env (CI écrit le .env depuis les secrets GitHub).

    `tmp_path_factory` redirige torrents_dir pour ne pas polluer /opt/mavod.
    """
    tmp = tmp_path_factory.mktemp("e2e")
    env = dict(os.environ)
    env.setdefault("MAVOD_TORRENTS_DIR", str(tmp / "torrents"))
    env.setdefault("MAVOD_STATE_PATH", str(tmp / "state.pkl"))
    env.setdefault("MAVOD_LOG_PATH", str(tmp / "bot.log"))
    return load_settings(env=env)


@pytest.fixture(scope="module")
def workflow(settings):
    return WorkflowService(settings)


def _assert_valid_result(result: WorkflowResult, *, expect_candidates: bool = True):
    """Invariants minimaux d'un WorkflowResult retourné par .run()."""
    assert isinstance(result, WorkflowResult)
    assert result.search_id
    assert result.schema_version == 2
    if expect_candidates:
        assert result.candidates, "aucun candidat survivant aux filtres"
        # best_choice peut être None si le ranker LLM échoue, on tolère


class TestE2EMovie:
    def test_avatar_2022(self, workflow):
        """Avatar: The Way of Water — référence stable, IMDb tt1630029."""
        intent = Intent(
            title="Avatar The Way of Water",
            type="movie",
            year=2022,
            imdb_id="tt1630029",
        )
        result = workflow.run(intent, skip_qb=True)
        _assert_valid_result(result)
        # Au moins un candidat doit matcher le titre (tolérant fuzzy/locale)
        titles = " ".join(c.get("title", "").lower() for c in result.candidates)
        assert "avatar" in titles


class TestE2ESerie:
    def test_the_bear_s03(self, workflow):
        """The Bear saison 3 — référence stable post-2024."""
        intent = Intent(
            title="The Bear",
            type="serie",
            year=2024,
            season=3,
            imdb_id="tt14452776",
        )
        result = workflow.run(intent, skip_qb=True)
        # Saisons récentes : tolère 0 candidat (release peut être indispo
        # certains jours sur les indexers configurés).
        _assert_valid_result(result, expect_candidates=False)
        if result.candidates:
            titles = " ".join(c.get("title", "").lower() for c in result.candidates)
            assert "bear" in titles
