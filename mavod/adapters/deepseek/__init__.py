"""Adapter DeepSeek : client HTTP + prompts externalisés."""

from mavod.adapters.deepseek.client import DeepSeekAdapter
from mavod.adapters.deepseek.prompts import (
    load_intent_prompt,
    load_ranker_prompt,
)

__all__ = [
    "DeepSeekAdapter",
    "load_intent_prompt",
    "load_ranker_prompt",
]
