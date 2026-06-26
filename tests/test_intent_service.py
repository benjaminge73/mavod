"""Tests de mavod.services.intent_service — mocks via respx."""

from __future__ import annotations

import httpx
import pytest
import respx

from mavod.config import load_settings
from mavod.domain import ClarificationRequest, Intent
from mavod.exceptions import IntentParseError, IntentValidationError
from mavod.services.intent_service import IntentService

pytestmark = pytest.mark.unit


_ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "LLM_API_KEY": "sk-test",
    "QB_URL": "http://qb",
    "QB_USER": "u",
    "QB_PASS": "p",
    "PROWLARR_URL": "http://prowlarr",
    "PROWLARR_API_KEY": "pk",
}


@pytest.fixture
def settings():
    return load_settings(env=_ENV)


def _tool_response(name, args_json, call_id="call_1"):
    return httpx.Response(
        200,
        json={
            "choices": [{
                "message": {
                    "role": "assistant",
                    "tool_calls": [{
                        "id": call_id,
                        "function": {"name": name, "arguments": args_json},
                    }]
                }
            }],
            "usage": {"prompt_tokens": 50, "prompt_cache_hit_tokens": 30},
        },
    )


class TestIntentService:
    @respx.mock
    def test_submit_intent_movie(self, settings):
        """Parse un intent film via submit_intent."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_tool_response(
                "submit_intent",
                '{"title": "Dune", "type": "movie", "year": 2021, "imdb_id": "tt1160419"}',
            )
        )
        svc = IntentService(settings)
        result = svc.parse([{"role": "user", "content": "Dune"}])
        assert result.is_intent
        assert isinstance(result.intent, Intent)
        assert result.intent.title == "Dune"
        assert result.intent.year == 2021
        assert result.intent.imdb_id == "tt1160419"
        assert result.usage["prompt_cache_hit_tokens"] == 30

    @respx.mock
    def test_submit_intent_serie(self, settings):
        """Parse un intent série via submit_intent."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_tool_response(
                "submit_intent",
                '{"title": "The Bear", "type": "serie", "season": 3, "episode": 4, "year": 2024}',
            )
        )
        svc = IntentService(settings)
        result = svc.parse([{"role": "user", "content": "The Bear S03E04"}])
        assert result.is_intent
        assert result.intent.episode == 4

    @respx.mock
    def test_ask_clarification(self, settings):
        """Retourne une demande de clarification."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_tool_response(
                "ask_clarification",
                '{"question": "Lequel ?", "options": ["Capra 1946", "Benigni 1997"], "missing_field": "disambiguation"}',
            )
        )
        svc = IntentService(settings)
        result = svc.parse([{"role": "user", "content": "La vie est belle"}])
        assert result.is_clarification
        assert isinstance(result.clarification, ClarificationRequest)
        assert result.clarification.question == "Lequel ?"
        assert len(result.clarification.options) == 2

    @respx.mock
    def test_invalid_intent_raises(self, settings):
        """Lève une erreur pour des arguments d'intent invalides."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_tool_response(
                "submit_intent",
                '{"title": "", "type": "movie"}',  # title vide
            )
        )
        svc = IntentService(settings)
        with pytest.raises(IntentValidationError):
            svc.parse([{"role": "user", "content": "x"}])

    @respx.mock
    def test_empty_history_raises(self, settings):
        """Lève une erreur sur un historique vide."""
        svc = IntentService(settings)
        with pytest.raises(IntentParseError):
            svc.parse([])

    @respx.mock
    def test_inserts_system_prompt(self, settings):
        """Insère le system prompt dans l'historique."""
        captured = {}

        def capture(request):
            import json as _json
            captured["body"] = _json.loads(request.content)
            return _tool_response(
                "submit_intent",
                '{"title": "X", "type": "movie"}',
            )

        respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=capture)
        svc = IntentService(settings)
        svc.parse([{"role": "user", "content": "x"}])
        # Le system prompt doit avoir été inséré en tête
        assert captured["body"]["messages"][0]["role"] == "system"
        assert "maVOD" in captured["body"]["messages"][0]["content"]


    @respx.mock
    def test_serie_without_season_asks_clarification(self, settings):
        """Série multi-saison sans numéro de saison → clarification missing_field=season."""
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            return_value=_tool_response(
                "ask_clarification",
                '{"question": "Quelle saison veux-tu ?", "missing_field": "season", "options": ["1", "2", "3"]}',
            )
        )
        svc = IntentService(settings)
        result = svc.parse([{"role": "user", "content": "Widows Bay"}])
        assert result.is_clarification
        assert result.clarification.missing_field == "season"


class TestMultiTurnFlow:
    """Scénarios complets : clarification → réponse utilisateur → intent final."""

    @respx.mock
    def test_disambiguation_then_submit(self, settings):
        """Tour 1 : ask_clarification. Tour 2 : submit_intent avec history enrichi."""
        responses = iter([
            _tool_response(
                "ask_clarification",
                '{"question": "Lequel ?", "options": ["Capra 1946", "Benigni 1997"], "missing_field": "disambiguation"}',
                call_id="call_disambig",
            ),
            _tool_response(
                "submit_intent",
                '{"title": "La vie est belle", "type": "movie", "year": 1997}',
                call_id="call_final",
            ),
        ])
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            side_effect=lambda request: next(responses)
        )
        svc = IntentService(settings)

        # Tour 1 — user demande titre ambigu
        history = [{"role": "user", "content": "La vie est belle"}]
        result1 = svc.parse(history)
        assert result1.is_clarification
        assert result1.clarification.question == "Lequel ?"
        assert result1.tool_call_id == "call_disambig"

        # Bot construit l'historique en ajoutant assistant_msg + réponse user tool
        history.append(result1.assistant_msg)
        history.append({
            "role": "tool",
            "tool_call_id": result1.tool_call_id,
            "content": "Benigni 1997",
        })

        # Tour 2 — IntentService retourne l'intent final
        result2 = svc.parse(history)
        assert result2.is_intent
        assert result2.intent.title == "La vie est belle"
        assert result2.intent.year == 1997

    @respx.mock
    def test_missing_episode_then_submit(self, settings):
        """Tour 1 : demande de saison/épisode. Tour 2 : intent serie complet."""
        responses = iter([
            _tool_response(
                "ask_clarification",
                '{"question": "Quelle saison ?", "missing_field": "season"}',
                call_id="call_season",
            ),
            _tool_response(
                "submit_intent",
                '{"title": "The Bear", "type": "serie", "season": 3, "year": 2024}',
                call_id="call_final",
            ),
        ])
        respx.post("https://api.deepseek.com/v1/chat/completions").mock(
            side_effect=lambda request: next(responses)
        )
        svc = IntentService(settings)

        history = [{"role": "user", "content": "The Bear"}]
        result1 = svc.parse(history)
        assert result1.is_clarification
        assert result1.clarification.missing_field == "season"

        history.append(result1.assistant_msg)
        history.append({
            "role": "tool",
            "tool_call_id": result1.tool_call_id,
            "content": "3",
        })

        result2 = svc.parse(history)
        assert result2.is_intent
        assert result2.intent.type == "serie"
        assert result2.intent.season == 3

    @respx.mock
    def test_history_grows_with_each_turn(self, settings):
        """Le history transmis à LLM inclut bien tous les tours précédents."""
        captured_bodies = []

        def capture(request):
            import json as _json
            body = _json.loads(request.content)
            captured_bodies.append(body)
            if len(captured_bodies) == 1:
                return _tool_response("ask_clarification",
                                      '{"question": "Lequel ?", "options": ["a", "b"]}',
                                      call_id="c1")
            return _tool_response("submit_intent",
                                  '{"title": "Y", "type": "movie", "year": 2020}',
                                  call_id="c2")

        respx.post("https://api.deepseek.com/v1/chat/completions").mock(side_effect=capture)
        svc = IntentService(settings)

        history = [{"role": "user", "content": "Y"}]
        r1 = svc.parse(history)
        history.append(r1.assistant_msg)
        history.append({"role": "tool", "tool_call_id": r1.tool_call_id, "content": "a"})
        svc.parse(history)

        # Le 2e appel doit inclure au moins 4 messages (system + user + assistant + tool)
        assert len(captured_bodies[1]["messages"]) >= 4
        roles = [m["role"] for m in captured_bodies[1]["messages"]]
        assert "tool" in roles
