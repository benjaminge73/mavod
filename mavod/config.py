"""Configuration centralisée maVOD.

Une seule source de vérité : charger via `load_settings()` au bootstrap,
injecter la `Settings` dans les services. Plus de `os.environ.get(...)` dispersés.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import FrozenSet, Optional

from mavod.exceptions import ConfigError


# Registry providers LLM (OpenAI-compatible). `LLM_PROVIDER` choisit un preset ;
# `LLM_BASE_URL` / `LLM_MODEL` peuvent l'overrider. base_url = sans `/v1` final
# (le client poste sur `{base_url}/v1/chat/completions`).
LLM_PROVIDERS: dict[str, dict[str, str]] = {
    "deepseek":   {"base_url": "https://api.deepseek.com",    "model": "deepseek-v4-flash"},
    "openai":     {"base_url": "https://api.openai.com",      "model": "gpt-4o-mini"},
    "mistral":    {"base_url": "https://api.mistral.ai",      "model": "mistral-small-latest"},
    "groq":       {"base_url": "https://api.groq.com/openai", "model": "llama-3.3-70b-versatile"},
    "openrouter": {"base_url": "https://openrouter.ai/api",   "model": "deepseek/deepseek-chat"},
    "xai":        {"base_url": "https://api.x.ai",            "model": "grok-2-latest"},
    "together":   {"base_url": "https://api.together.xyz",    "model": "meta-llama/Llama-3.3-70B-Instruct-Turbo"},
    "local":      {"base_url": "http://localhost:11434",      "model": "llama3.1"},
}
DEFAULT_LLM_PROVIDER = "deepseek"
DEFAULT_ALLOWED_USER_IDS: FrozenSet[int] = frozenset()
DEFAULT_MAVOD_UI_URL = "http://localhost:8501"


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuration runtime maVOD. Immutable après `load_settings()`."""

    # ─── Secrets requis ────────────────────────────────────────────────────
    telegram_bot_token: str
    llm_api_key: str
    qb_url: str
    qb_user: str
    qb_pass: str
    prowlarr_url: str
    prowlarr_api_key: str

    # ─── Bot Telegram ──────────────────────────────────────────────────────
    telegram_allowed_users: FrozenSet[int] = DEFAULT_ALLOWED_USER_IDS
    max_concurrent_workflows: int = 2
    max_tool_turns: int = 3
    max_history_messages: int = 20
    session_ttl_seconds: int = 30 * 60
    download_poll_interval: int = 30
    download_poll_timeout: int = 3600

    # ─── LLM (OpenAI-compatible, provider-agnostic) ────────────────────────
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = LLM_PROVIDERS[DEFAULT_LLM_PROVIDER]["model"]
    llm_base_url: str = LLM_PROVIDERS[DEFAULT_LLM_PROVIDER]["base_url"]
    llm_timeout: float = 60.0
    llm_max_retries: int = 3
    llm_intent_max_tokens: int = 2048
    llm_ranker_max_tokens: int = 8192

    # ─── Ranking ───────────────────────────────────────────────────────────
    candidates_threshold: int = 30
    max_torrents_for_llm: int = 10
    max_files_per_torrent: int = 10

    # ─── Paths ─────────────────────────────────────────────────────────────
    mavod_ui_url: str = DEFAULT_MAVOD_UI_URL
    state_path: Path = field(default_factory=lambda: Path("/app/state/persistence.pkl"))
    log_path: Path = field(default_factory=lambda: Path("/app/logs/bot.log"))
    torrents_dir: Path = field(default_factory=lambda: Path("/app/torrents"))


# ─── Loader ──────────────────────────────────────────────────────────────────

_REQUIRED_ENV_VARS = (
    "TELEGRAM_BOT_TOKEN",
    "LLM_API_KEY",
    "QB_URL", "QB_USER", "QB_PASS",
    "PROWLARR_URL", "PROWLARR_API_KEY",
)


def _resolve_llm(provider: str, base_url: Optional[str], model: Optional[str]) -> tuple[str, str, str]:
    """Résout (provider, base_url, model) depuis le registry + overrides explicites.

    `LLM_BASE_URL` / `LLM_MODEL` priment sur le preset du provider. Un provider
    inconnu est accepté seulement si `LLM_BASE_URL` ET `LLM_MODEL` sont fournis.
    """
    provider = (provider or DEFAULT_LLM_PROVIDER).lower()
    preset = LLM_PROVIDERS.get(provider)
    resolved_base = base_url or (preset["base_url"] if preset else None)
    resolved_model = model or (preset["model"] if preset else None)
    if not resolved_base:
        raise ConfigError(
            f"LLM_PROVIDER '{provider}' inconnu — fournis LLM_BASE_URL "
            f"ou choisis parmi: {', '.join(sorted(LLM_PROVIDERS))}"
        )
    if not resolved_model:
        raise ConfigError(
            f"LLM_MODEL requis pour le provider '{provider}' (aucun défaut connu)"
        )
    return provider, resolved_base.rstrip("/"), resolved_model


def _parse_allowed_users(raw: Optional[str]) -> FrozenSet[int]:
    """CSV user_ids → frozenset[int]. Tokens vides ou invalides ignorés.

    Si la variable est absente OU ne contient que des tokens invalides,
    renvoie DEFAULT_ALLOWED_USER_IDS (= vide → personne autorisé, défaut sûr).
    """
    if not raw:
        return DEFAULT_ALLOWED_USER_IDS
    parsed: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            parsed.add(int(token))
        except ValueError:
            continue
    return frozenset(parsed) if parsed else DEFAULT_ALLOWED_USER_IDS


def _env(key: str, default: Optional[str] = None) -> Optional[str]:
    """Lit une variable d'env ; renvoie `default` si absente OU chaîne vide."""
    val = os.environ.get(key)
    if val is None or val == "":
        return default
    return val


def load_settings(*, env: Optional[dict] = None) -> Settings:
    """Construit Settings depuis `os.environ` (ou un dict explicite pour les tests).

    Raises:
        ConfigError si une variable requise est absente.
    """
    if env is not None:
        # Pour tests : patcher temporairement os.environ
        original = dict(os.environ)
        os.environ.clear()
        os.environ.update({k: str(v) for k, v in env.items()})
        try:
            return _load_from_environ()
        finally:
            os.environ.clear()
            os.environ.update(original)
    return _load_from_environ()


def _load_from_environ() -> Settings:
    missing = [k for k in _REQUIRED_ENV_VARS if not os.environ.get(k)]
    if missing:
        raise ConfigError(f"Variables d'env requises absentes: {', '.join(missing)}")

    provider, base_url, model = _resolve_llm(
        _env("LLM_PROVIDER", DEFAULT_LLM_PROVIDER),
        _env("LLM_BASE_URL"),
        _env("LLM_MODEL"),
    )

    return Settings(
        telegram_bot_token=os.environ["TELEGRAM_BOT_TOKEN"],
        llm_api_key=os.environ["LLM_API_KEY"],
        qb_url=os.environ["QB_URL"],
        qb_user=os.environ["QB_USER"],
        qb_pass=os.environ["QB_PASS"],
        prowlarr_url=os.environ["PROWLARR_URL"],
        prowlarr_api_key=os.environ["PROWLARR_API_KEY"],
        telegram_allowed_users=_parse_allowed_users(_env("TELEGRAM_ALLOWED_USERS")),
        llm_provider=provider,
        llm_model=model,
        llm_base_url=base_url,
        mavod_ui_url=_env("MAVOD_UI_URL", DEFAULT_MAVOD_UI_URL),
        state_path=Path(_env("MAVOD_STATE_PATH", "/app/state/persistence.pkl")),
        log_path=Path(_env("MAVOD_LOG_PATH", "/app/logs/bot.log")),
        torrents_dir=Path(_env("MAVOD_TORRENTS_DIR", "/app/torrents")),
    )


def field_names() -> tuple[str, ...]:
    """Liste des champs Settings (utile pour debug / introspection)."""
    return tuple(f.name for f in fields(Settings))
