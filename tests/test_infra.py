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
