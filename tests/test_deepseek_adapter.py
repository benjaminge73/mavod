"""Tests de mavod.adapters.deepseek.client — mocks via respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavod.adapters.deepseek import DeepSeekAdapter
from mavod.adapters.deepseek.prompts import (
    load_intent_prompt,
    load_ranker_prompt,
    prompt_hash,
)
from mavod.config import Settings, load_settings
from mavod.exceptions import DeepSeekError, DeepSeekMalformed

pytestmark = pytest.mark.unit


_ENV = {
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
def settings() -> Settings:
    return load_settings(env=_ENV)


# ─── Prompts externalisés ────────────────────────────────────────────────────

class TestPrompts:
    def test_load_intent_prompt_non_empty(self):
        """Le prompt d'intent est chargé et non vide."""
        p = load_intent_prompt()
        assert "You are the request parser for maVOD" in p
        assert "submit_intent" not in p  # Tool schemas are not in the system prompt

    def test_load_ranker_prompt_non_empty(self):
        """Le prompt du ranker est chargé et non vide."""
        p = load_ranker_prompt()
        assert "VIDEO HDR" in p
        assert "Best choice" in p

    def test_prompt_hash_stable(self):
        """Le hash d'un même prompt est stable."""
        h1 = prompt_hash(load_intent_prompt())
        h2 = prompt_hash(load_intent_prompt())
        assert h1 == h2
        assert len(h1) == 12

    def test_prompt_hash_differs(self):
        """Deux prompts différents produisent des hashs différents."""
        assert prompt_hash("a") != prompt_hash("b")


# ─── Client ──────────────────────────────────────────────────────────────────

class TestDeepSeekAdapter:
    @respx.mock
    def test_chat_success(self, settings):
        """Un appel chat réussi retourne le contenu."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "hello world"}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 2},
                },
            )
        )
        adapter = DeepSeekAdapter(settings)
        assert adapter.chat(system="sys", user="user") == "hello world"

    @respx.mock
    def test_chat_with_usage(self, settings):
        """chat_with_usage renvoie aussi les tokens consommés."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {
                            "content": "answer",
                            "reasoning_content": "thinking…",
                        }
                    }],
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 5,
                        "prompt_cache_hit_tokens": 80,
                    },
                },
            )
        )
        adapter = DeepSeekAdapter(settings)
        content, reasoning, usage = adapter.chat_with_usage(system="s", user="u")
        assert content == "answer"
        assert reasoning == "thinking…"
        assert usage["prompt_cache_hit_tokens"] == 80

    @respx.mock
    def test_chat_with_tools_success(self, settings):
        """chat_with_tools retourne les tool_calls."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={
                    "choices": [{
                        "message": {
                            "tool_calls": [{
                                "id": "call_1",
                                "function": {
                                    "name": "submit_intent",
                                    "arguments": '{"title": "Dune", "type": "movie"}',
                                },
                            }]
                        }
                    }],
                    "usage": {"prompt_tokens": 50},
                },
            )
        )
        adapter = DeepSeekAdapter(settings)
        result = adapter.chat_with_tools(
            messages=[{"role": "user", "content": "Dune"}],
            tools=[{"type": "function", "function": {"name": "submit_intent"}}],
        )
        assert result["tool_name"] == "submit_intent"
        assert result["arguments"] == {"title": "Dune", "type": "movie"}

    @respx.mock
    def test_chat_with_tools_no_tool_calls_raises(self, settings):
        """Lève une erreur si aucun tool_call n'est retourné."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(
                200,
                json={"choices": [{"message": {"content": "no tool"}}]},
            )
        )
        adapter = DeepSeekAdapter(settings)
        with pytest.raises(DeepSeekMalformed):
            adapter.chat_with_tools(messages=[{"role": "user", "content": "x"}], tools=[])

    @respx.mock
    def test_http_error_raises(self, settings):
        """Les erreurs HTTP sont propagées proprement."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=httpx.Response(400, text="bad request")
        )
        adapter = DeepSeekAdapter(settings)
        with pytest.raises(DeepSeekError):
            adapter.chat(system="s", user="u")

    @respx.mock
    def test_429_retries_then_succeeds(self, settings, monkeypatch):
        """Un HTTP 429 déclenche un retry puis réussit."""
        # Avoid real sleeping
        from mavod.adapters.deepseek import client as client_mod
        monkeypatch.setattr(client_mod, "_sleep", lambda s: None)

        route = respx.post("https://api.deepseek.com/v1/chat/completions")
        route.side_effect = [
            httpx.Response(429, headers={"Retry-After": "0"}, text="rate limit"),
            httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]}),
        ]
        adapter = DeepSeekAdapter(settings)
        assert adapter.chat(system="s", user="u") == "ok"

    def test_context_manager_closes(self, settings):
        """Le context manager ferme bien le client."""
        with DeepSeekAdapter(settings) as adapter:
            assert adapter.model == settings.deepseek_model
