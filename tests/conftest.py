"""Fixtures partagées par toutes les suites de tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

_ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_env_file() -> dict:
    env_vars: dict = {}
    if _ENV_PATH.exists():
        with open(_ENV_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env_vars[key.strip()] = value.strip().strip('"').strip("'")
    return env_vars


@pytest.fixture(scope="session", autouse=True)
def load_env():
    """Charge .env dans os.environ (sans écraser les vars déjà définies)."""
    for key, value in _load_env_file().items():
        if not os.environ.get(key):
            os.environ[key] = value


# ─── Settings fixture standard (réutilisable) ────────────────────────────────


_TEST_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "DEEPSEEK_API_KEY": "sk-test",
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
def test_env() -> dict:
    """Dictionnaire env minimal pour load_settings() — vars critiques uniquement."""
    return dict(_TEST_ENV)


@pytest.fixture
def test_settings(test_env):
    """Settings construits à partir de _TEST_ENV — usage générique en unit tests."""
    from mavod.config import load_settings
    return load_settings(env=test_env)


# ─── Mock DeepSeek (legacy, compatibilité) ───────────────────────────────────


@pytest.fixture
def mock_deepseek():
    """MagicMock d'un DeepSeekClient. Retourne un intent JSON valide par défaut.

    DEPRECATED — préférer `deepseek_intent_response` / `deepseek_ranking_response`
    qui retournent des objets `httpx.Response` directement utilisables avec respx.
    """
    client = MagicMock()
    client.chat.return_value = '{"title": "Test", "year": 2021, "type": "movie", "season": null}'
    return client


# ─── Helpers httpx.Response pour respx ───────────────────────────────────────


@pytest.fixture
def deepseek_intent_response():
    """Fabrique de réponses DeepSeek pour les tool_calls intent.

    Usage :
        respx.post(...).mock(return_value=deepseek_intent_response(
            tool="submit_intent",
            args='{"title": "Dune", "type": "movie", "year": 2021}'
        ))
    """
    def _make(tool: str, args: str, *, call_id: str = "call_1", usage: dict = None):
        usage = usage or {"prompt_tokens": 50, "prompt_cache_hit_tokens": 30}
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {
                        "tool_calls": [{
                            "id": call_id,
                            "function": {"name": tool, "arguments": args},
                        }]
                    }
                }],
                "usage": usage,
            },
        )
    return _make


@pytest.fixture
def deepseek_ranking_response():
    """Fabrique de réponses DeepSeek pour le ranker.

    Usage :
        respx.post(...).mock(return_value=deepseek_ranking_response(best=2))
        respx.post(...).mock(return_value=deepseek_ranking_response(content="custom text"))
    """
    def _make(*, best: int = None, ranking: str = None, content: str = None,
              reasoning: str = "thinking", usage: dict = None):
        if content is None:
            ranking_str = ranking or (f"Torrent {best}, Torrent 1" if best else "")
            best_str = f"\n**Best choice:** Torrent {best}" if best else ""
            content = f"**Final ranking:** {ranking_str}{best_str}"
        usage = usage or {"prompt_tokens": 200, "prompt_cache_hit_tokens": 150}
        return httpx.Response(
            200,
            json={
                "choices": [{
                    "message": {"content": content, "reasoning_content": reasoning}
                }],
                "usage": usage,
            },
        )
    return _make


@pytest.fixture
def prowlarr_search_payload():
    """Échantillon minimal de réponse normalisée Prowlarr (liste de dicts)."""
    return [
        {
            "title": "Dune.2021.1080p.BluRay.x264-FOO",
            "indexer": "Prowlarr:YGG",
            "size": 8 * 1024 ** 3,
            "seeders": 50,
            "leechers": 2,
            "downloadUrl": "magnet:?xt=urn:btih:" + "a" * 40,
            "infoHash": "a" * 40,
            "guid": "g1",
            "categories": [2000],
            "is_magnet": True,
        },
    ]


@pytest.fixture
def c411_search_payload():
    """Échantillon minimal de réponse normalisée C411 (liste de dicts)."""
    return [
        {
            "title": "The.Bear.S03.MULTI.1080p.WEB-DL.x265-BAR",
            "indexer": "C411",
            "size": 5 * 1024 ** 3,
            "seeders": 10,
            "downloadUrl": "magnet:?xt=urn:btih:" + "b" * 40,
            "infoHash": "b" * 40,
            "_c411_id": 12345,
        },
    ]
