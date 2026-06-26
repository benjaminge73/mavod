"""Tests de mavod.config — chargement Settings + validation env."""

from __future__ import annotations

from pathlib import Path

import pytest

from mavod.config import (
    DEFAULT_ALLOWED_USER_IDS,
    DEFAULT_DEEPSEEK_MODEL,
    Settings,
    _parse_allowed_users,
    load_settings,
)
from mavod.exceptions import ConfigError

pytestmark = pytest.mark.unit


_FULL_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg-token",
    "DEEPSEEK_API_KEY": "sk-deepseek",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pw",
    "C411_URL_API": "http://c411",
    "C411_API_KEY": "c411k",
    "C411_PASSKEY": "c411p",
}


def test_load_settings_minimal():
    """Charge les settings depuis un .env minimal."""
    s = load_settings(env=_FULL_ENV)
    assert isinstance(s, Settings)
    assert s.telegram_bot_token == "tg-token"
    assert s.deepseek_api_key == "sk-deepseek"
    assert s.deepseek_model == DEFAULT_DEEPSEEK_MODEL
    assert s.telegram_allowed_users == DEFAULT_ALLOWED_USER_IDS
    assert s.deepseek_base_url == "https://api.deepseek.com"


def test_load_settings_missing_required_raises():
    """Lève une erreur si une variable requise manque."""
    env = dict(_FULL_ENV)
    del env["DEEPSEEK_API_KEY"]
    with pytest.raises(ConfigError) as exc:
        load_settings(env=env)
    assert "DEEPSEEK_API_KEY" in str(exc.value)


def test_load_settings_allowed_users_override():
    """TELEGRAM_ALLOWED_USERS override la liste par défaut."""
    env = dict(_FULL_ENV)
    env["TELEGRAM_ALLOWED_USERS"] = "1,2,3"
    s = load_settings(env=env)
    assert s.telegram_allowed_users == frozenset({1, 2, 3})


def test_load_settings_allowed_users_invalid_falls_back_to_default():
    """Une valeur invalide retombe sur la liste par défaut."""
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
    env["DEEPSEEK_BASE_URL"] = "https://api.example.com/"  # trailing slash strip
    env["DEEPSEEK_MODEL"] = "deepseek-v4-flash"
    s = load_settings(env=env)
    assert s.state_path == Path("/tmp/state.pkl")
    assert s.log_path == Path("/tmp/bot.log")
    assert s.mavod_ui_url == "https://example.com"
    assert s.deepseek_base_url == "https://api.example.com"
    assert s.deepseek_model == "deepseek-v4-flash"


def test_settings_frozen():
    """L'instance Settings est immuable."""
    s = load_settings(env=_FULL_ENV)
    with pytest.raises((AttributeError, Exception)):
        s.telegram_bot_token = "changed"  # type: ignore[misc]


def test_parse_allowed_users_empty():
    """Le parsing d'une chaîne vide retourne la valeur par défaut."""
    assert _parse_allowed_users(None) == DEFAULT_ALLOWED_USER_IDS
    assert _parse_allowed_users("") == DEFAULT_ALLOWED_USER_IDS
    assert _parse_allowed_users(",,") == DEFAULT_ALLOWED_USER_IDS


def test_parse_allowed_users_mixed():
    """Ignore les tokens invalides dans la liste mixte."""
    assert _parse_allowed_users("1,abc,2") == frozenset({1, 2})


def test_parse_allowed_users_multiple():
    """L'ACL multi-user parse plusieurs ids (whitespace + ordre indifférents).

    Garde-fou contre régression silencieuse de l'ACL multi-user.
    """
    a, b = 111111, 222222
    assert _parse_allowed_users(f"{a},{b}") == frozenset({a, b})
    assert _parse_allowed_users(f" {b} , {a} ") == frozenset({a, b})


def test_env_example_documents_allowed_users_key():
    """`.env.example` doit documenter la clé TELEGRAM_ALLOWED_USERS.

    Drift guard : la clé doit rester présente pour l'onboarding, même vide.
    """
    root = Path(__file__).parent.parent
    text = (root / ".env.example").read_text()
    line = next(
        (l for l in text.splitlines() if l.startswith("TELEGRAM_ALLOWED_USERS=")),
        None,
    )
    assert line is not None, "TELEGRAM_ALLOWED_USERS absent de .env.example"
