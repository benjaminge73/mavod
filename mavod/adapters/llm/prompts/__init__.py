"""Prompts LLM externalisés (versionnés, traçables).

Charger via `load_intent_prompt()` / `load_ranker_prompt()`. Le hash SHA1 du
prompt est exposé via `prompt_hash()` pour permettre au logging de tracker
les régressions de cache hit (le prompt-caching dépend du prefix exact).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Tuple


_PROMPTS_DIR = Path(__file__).parent


def _load(filename: str) -> str:
    path = _PROMPTS_DIR / filename
    return path.read_text(encoding="utf-8")


def load_intent_prompt() -> str:
    """Prompt système pour le parsing d'intent (multi-turn function calling)."""
    return _load("intent_v1.md")


def load_ranker_prompt() -> str:
    """Prompt système pour le ranker (v2)."""
    return _load("ranker_v2.md")


def prompt_hash(prompt: str) -> str:
    """SHA1 court (12 chars) d'un prompt — pour logger sa version."""
    return hashlib.sha1(prompt.encode("utf-8")).hexdigest()[:12]
