"""Service de parsing d'intent.

Remplace `mavod/intent_parser.py` à terme. Consomme `LLMAdapter` +
`Settings` + prompts externalisés. Renvoie un `IntentResult`
(`Intent | ClarificationRequest`) typé.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Sequence

from mavod.adapters.llm import LLMAdapter
from mavod.adapters.llm.prompts import load_intent_prompt, prompt_hash
from mavod.config import Settings
from mavod.domain import ClarificationRequest, Intent, IntentResult
from mavod.exceptions import (
    LLMError,
    IntentParseError,
    IntentValidationError,
)
from mavod.logging_setup import get_logger


log = get_logger(__name__)


# ─── Tool schemas (function calling) ─────────────────────────────────────────

SUBMIT_INTENT_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "submit_intent",
        "description": (
            "Call this when you can confidently identify a single work. Fill year "
            "from your own knowledge even if the user did not provide it."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "title":   {"type": "string"},
                "type":    {"type": "string", "enum": ["movie", "serie"]},
                "year":    {"type": ["integer", "null"]},
                "season":  {"type": ["integer", "null"]},
                "episode": {"type": ["integer", "null"]},
                "imdb_id": {"type": ["string", "null"], "pattern": r"^tt\d{7,8}$"},
            },
            "required": ["title", "type"],
        },
    },
}

ASK_CLARIFICATION_TOOL: Dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ask_clarification",
        "description": (
            "Call this when the request is ambiguous. Use `options` when the same "
            "title matches multiple known works."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options":  {"type": "array", "items": {"type": "string"}},
                "missing_field": {
                    "type": "string",
                    "enum": ["season", "episode", "type", "year", "title", "disambiguation"],
                },
            },
            "required": ["question"],
        },
    },
}

INTENT_TOOLS: Sequence[Dict[str, Any]] = (SUBMIT_INTENT_TOOL, ASK_CLARIFICATION_TOOL)


class IntentService:
    """Multi-turn function-calling parser (avec gestion clarifications)."""

    def __init__(self, settings: Settings, *, adapter: LLMAdapter = None):
        self._settings = settings
        self._adapter = adapter or LLMAdapter(settings)
        self._system_prompt = load_intent_prompt()
        log.info("intent.service.init", extra={
            "model": self._adapter.model,
            "prompt_hash": prompt_hash(self._system_prompt),
        })

    def parse(self, history: List[Dict[str, Any]]) -> "IntentTurnResult":
        """Parse l'historique → soumission intent OU demande de clarification.

        L'historique est en format OpenAI ; le system prompt est inséré
        automatiquement s'il n'est pas en première position.
        """
        if not history:
            raise IntentParseError("history vide")

        if history[0].get("role") != "system":
            full_history: List[Dict[str, Any]] = [
                {"role": "system", "content": self._system_prompt}
            ] + history
        else:
            full_history = history

        try:
            response = self._adapter.chat_with_tools(
                messages=full_history,
                tools=list(INTENT_TOOLS),
                tool_choice="auto",
                temperature=0.0,
                max_tokens=self._settings.llm_intent_max_tokens,
            )
        except LLMError as e:
            raise IntentParseError(f"LLM error: {e}") from e

        tool = response["tool_name"]
        args = response["arguments"]
        assistant_msg = response["assistant_msg"]
        tool_call_id = (assistant_msg.get("tool_calls") or [{}])[0].get("id", "")

        if tool == "submit_intent":
            try:
                intent = Intent.from_dict(args)
            except IntentValidationError:
                raise
            return IntentTurnResult(
                result=intent,
                tool_call_id=tool_call_id,
                assistant_msg=assistant_msg,
                usage=response.get("usage") or {},
            )

        if tool == "ask_clarification":
            question = args.get("question")
            if not isinstance(question, str) or not question.strip():
                raise IntentParseError(f"ask_clarification sans question valide: {args!r}")
            clarification = ClarificationRequest(
                question=question.strip(),
                options=tuple(args.get("options") or ()) or None,
                missing_field=args.get("missing_field"),
                tool_call_id=tool_call_id,
            )
            return IntentTurnResult(
                result=clarification,
                tool_call_id=tool_call_id,
                assistant_msg=assistant_msg,
                usage=response.get("usage") or {},
            )

        raise IntentParseError(f"Tool inattendu: {tool!r}")


# Tuple-like return container — gardé léger pour ne pas surcharger le domain.
from dataclasses import dataclass, field as _field


@dataclass(frozen=True, slots=True)
class IntentTurnResult:
    """Sortie d'un tour de parsing (intent OU clarification + métadonnées)."""

    result: IntentResult
    tool_call_id: str
    assistant_msg: Dict[str, Any]
    usage: Dict[str, Any] = _field(default_factory=dict)

    @property
    def is_intent(self) -> bool:
        return isinstance(self.result, Intent)

    @property
    def is_clarification(self) -> bool:
        return isinstance(self.result, ClarificationRequest)

    @property
    def intent(self) -> Intent:
        if not self.is_intent:
            raise AttributeError("result is not an Intent")
        return self.result  # type: ignore[return-value]

    @property
    def clarification(self) -> ClarificationRequest:
        if not self.is_clarification:
            raise AttributeError("result is not a ClarificationRequest")
        return self.result  # type: ignore[return-value]
