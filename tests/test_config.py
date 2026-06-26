"""Tests de mavod.config — chargement Settings + validation env."""

from __future__ import annotations

from pathlib import Path

import pytest

from mavod.config import (
    DEFAULT_ALLOWED_USER_IDS,
    DEFAULT_LLM_PROVIDER,
    LLM_PROVIDERS,
    Settings,
    _parse_allowed_users,
    load_settings,
)
from mavod.exceptions import ConfigError

pytestmark = pytest.mark.unit


_FULL_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg-token",
    "LLM_API_KEY": "sk-test",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pw",
}


def test_load_settings_minimal():
    """Charge les settings depuis un .env minimal."""
    s = load_settings(env=_FULL_ENV)
    assert isinstance(s, Settings)
    assert s.telegram_bot_token == "tg-token"
    assert s.llm_api_key == "sk-test"
    assert s.llm_provider == DEFAULT_LLM_PROVIDER
    assert s.llm_model == LLM_PROVIDERS[DEFAULT_LLM_PROVIDER]["model"]
    assert s.llm_base_url == LLM_PROVIDERS[DEFAULT_LLM_PROVIDER]["base_url"]
    assert s.telegram_allowed_users == DEFAULT_ALLOWED_USER_IDS


def test_load_settings_missing_required_raises():
    """Lève une erreur si une variable requise manque."""
    env = dict(_FULL_ENV)
    del env["LLM_API_KEY"]
    with pytest.raises(ConfigError) as exc:
        load_settings(env=env)
    assert "LLM_API_KEY" in str(exc.value)


def test_llm_provider_preset_resolves_base_and_model():
    """Choisir un provider connu résout base_url + modèle depuis le registry."""
    env = dict(_FULL_ENV)
    env["LLM_PROVIDER"] = "openai"
    s = load_settings(env=env)
    assert s.llm_provider == "openai"
    assert s.llm_base_url == LLM_PROVIDERS["openai"]["base_url"]
    assert s.llm_model == LLM_PROVIDERS["openai"]["model"]


def test_llm_explicit_overrides_win_over_preset():
    """LLM_MODEL / LLM_BASE_URL priment sur le preset (+ strip trailing slash)."""
    env = dict(_FULL_ENV)
    env["LLM_PROVIDER"] = "deepseek"
    env["LLM_MODEL"] = "deepseek-v4-pro"
    env["LLM_BASE_URL"] = "https://proxy.example.com/"
    s = load_settings(env=env)
    assert s.llm_model == "deepseek-v4-pro"
    assert s.llm_base_url == "https://proxy.example.com"


def test_unknown_provider_without_base_url_raises():
    """Provider inconnu sans LLM_BASE_URL → ConfigError explicite."""
    env = dict(_FULL_ENV)
    env["LLM_PROVIDER"] = "acme"
    with pytest.raises(ConfigError) as exc:
        load_settings(env=env)
    assert "acme" in str(exc.value)


def test_unknown_provider_with_base_url_and_model_ok():
    """Provider inconnu accepté si base_url ET model explicites (BYO endpoint)."""
    env = dict(_FULL_ENV)
    env["LLM_PROVIDER"] = "acme"
    env["LLM_BASE_URL"] = "https://acme.local"
    env["LLM_MODEL"] = "acme-1"
    s = load_settings(env=env)
    assert s.llm_provider == "acme"
    assert s.llm_base_url == "https://acme.local"
    assert s.llm_model == "acme-1"


def test_load_settings_allowed_users_override():
    """TELEGRAM_ALLOWED_USERS override la liste par défaut."""
    env = dict(_FULL_ENV)
    env["TELEGRAM_ALLOWED_USERS"] = "1,2,3"
    s = load_settings(env=env)
    assert s.telegram_allowed_users == frozenset({1, 2, 3})


def test_load_settings_allowed_users_invalid_falls_back_to_default():
    """Une valeur invalide retombe sur la liste par défaut (vide)."""
    env = dict(_FULL_ENV)
    env["TELEGRAM_ALLOWED_USERS"] = "abc,,xyz"
    s = load_settings(env=env)
    assert s.telegram_allowed_users == DEFAULT_ALLOWED_USER_IDS


def test_load_settings_paths_overridable():
    """Les chemins peuvent être surchargés par variables d'env."""
    env = dict(_FULL_ENV)
    env["MAVOD_STATE_PATH"] = "/tmp/state.pkl"
    env["MAVOD_LOG_PATH"] = "/tmp/bot.log"
    env["MAVOD_UI_URL"] = "https://example.com"
    s = load_settings(env=env)
    assert s.state_path == Path("/tmp/state.pkl")
    assert s.log_path == Path("/tmp/bot.log")
    assert s.mavod_ui_url == "https://example.com"


def test_settings_frozen():
    """L'instance Settings est immuable."""
    s = load_settings(env=_FULL_ENV)
    with pytest.raises((AttributeError, Exception)):
        s.telegram_bot_token = "changed"  # type: ignore[misc]


def test_parse_allowed_users_empty():
    """Le parsing d'une chaîne vide retourne la valeur par défaut (vide)."""
    assert _parse_allowed_users(None) == DEFAULT_ALLOWED_USER_IDS
    assert _parse_allowed_users("") == DEFAULT_ALLOWED_USER_IDS
    assert _parse_allowed_users(",,") == DEFAULT_ALLOWED_USER_IDS


def test_parse_allowed_users_mixed():
    """Ignore les tokens invalides dans la liste mixte."""
    assert _parse_allowed_users("1,abc,2") == frozenset({1, 2})


def test_default_acl_is_empty():
    """Défaut sûr : aucune ID hardcodée → personne autorisé sans config explicite."""
    assert DEFAULT_ALLOWED_USER_IDS == frozenset()
