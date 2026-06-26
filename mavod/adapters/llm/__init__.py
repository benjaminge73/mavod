"""Adapter LLM agnostique : client HTTP OpenAI-compatible + prompts externalisés."""

from mavod.adapters.llm.client import LLMAdapter
from mavod.adapters.llm.prompts import (
    load_intent_prompt,
    load_ranker_prompt,
)

__all__ = [
    "LLMAdapter",
    "load_intent_prompt",
    "load_ranker_prompt",
]
