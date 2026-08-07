"""Smoke test infrastructure : présence du fichier .env.example pour onboarding.

Réduit volontairement : la présence du `.env` racine n'est plus testée car son
absence fait échouer tous les autres tests (vérification redondante). De même,
les vars critiques sont validées par `mavod.config.load_settings()` au démarrage.
"""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


def test_env_example_exists():
    """`.env.example` doit exister pour l'onboarding (template des secrets)."""
    project_root = Path(__file__).parent.parent
    assert (project_root / ".env.example").exists(), \
        ".env.example manquant — bloque l'onboarding d'un nouveau dev"


def test_ci_workflow_is_named_ci_yml():
    """Le workflow CI s'appelle `.github/workflows/ci.yml` — nom aligné sur les
    6 autres repos du parc Dependabot (hermes-custom, job-agent,
    kanban_mAIster_CRM, bolossabalos, carnet-de-voyage-crete,
    jira-kanban-dashboard), condition pour que la garde nocturne Dependabot
    puisse exiger un workflow nommé (`build_proof.workflow`) plutôt qu'un
    régime générique. Un workflow renommé ou disparu ne remonte aucune
    erreur GitHub Actions — cette assertion est la seule garde."""
    workflows = Path(__file__).parent.parent / ".github" / "workflows"
    assert (workflows / "ci.yml").exists(), "ci.yml manquant — la garde nocturne Dependabot exige ce nom"
    assert not (workflows / "test.yml").exists(), "test.yml n'aurait pas dû survivre au renommage vers ci.yml"
