"""Client LLM agnostique (API OpenAI-compatible) : Settings-driven, retry unifié, cache-aware.

Provider-agnostic : tout service exposant `/v1/chat/completions` (DeepSeek, OpenAI,
Mistral, Groq, OpenRouter, xAI, Together, local…). Le provider/base_url/modèle sont
résolus dans `Settings` (cf. `LLM_PROVIDERS`). Caractéristiques :
- Plus de `os.environ.get` : consomme `Settings`.
- Retry consolidé (élimine la duplication chat / chat_with_tools).
- Honore les HTTP dates dans `Retry-After`.
- Logger structuré (event names, extras).
- Expose `prompt_cache_hit_tokens` dans `usage` pour tracker l'efficacité du cache.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Literal, Optional, Tuple

import httpx

from mavod.adapters._retry import parse_retry_after
from mavod.config import Settings
from mavod.exceptions import (
    LLMError,
    LLMMalformed,
    LLMRateLimit,
    LLMTimeout,
)
from mavod.logging_setup import get_logger


log = get_logger(__name__)


class LLMAdapter:
    """Adapter HTTP `/v1/chat/completions` (OpenAI-compatible). Thread-safe via httpx.Client."""

    def __init__(
        self,
        settings: Settings,
        *,
        http_client: Optional[httpx.Client] = None,
    ):
        self._settings = settings
        self._owned_http = http_client is None
        self._http = http_client or httpx.Client(timeout=settings.llm_timeout)

    @property
    def model(self) -> str:
        return self._settings.llm_model

    # ─── API publique ──────────────────────────────────────────────────────

    def chat(
        self,
        *,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.1,
        response_format: Literal["text", "json_object"] = "text",
    ) -> str:
        """Appel chat simple : retourne uniquement le `content` final (pas l'usage)."""
        content, _r, _u = self.chat_with_usage(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=temperature,
            response_format=response_format,
        )
        return content

    def chat_with_usage(
        self,
        *,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        temperature: float = 0.1,
        response_format: Literal["text", "json_object"] = "text",
    ) -> Tuple[str, Optional[str], Dict[str, Any]]:
        """Appel chat enrichi : retourne (content, reasoning_content, usage) — utile pour le ranker."""
        body: dict = {
            "model": self._settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens if max_tokens is not None
                          else self._settings.llm_intent_max_tokens,
            "temperature": temperature,
        }
        if response_format == "json_object":
            body["response_format"] = {"type": "json_object"}

        payload = self._post(body)

        try:
            msg = payload["choices"][0]["message"]
            content = msg["content"]
            reasoning = msg.get("reasoning_content")
            usage = payload.get("usage") or {}
            return content, reasoning, usage
        except (KeyError, IndexError, ValueError, json.JSONDecodeError) as e:
            raise LLMMalformed(f"Réponse invalide: {e}") from e

    def chat_with_tools(
        self,
        *,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        tool_choice: str = "auto",
        max_tokens: Optional[int] = None,
        temperature: float = 0.0,
    ) -> Dict[str, Any]:
        """Appel function-calling : retourne le premier tool_call (nom + arguments parsés).

        Note : certains backends reasoner (ex. `deepseek-v4-pro/flash`) rejettent
        `tool_choice="required"` (HTTP 400) → on reste sur `"auto"` + le system
        prompt mandate "exactly ONE tool per turn".
        """
        body: Dict[str, Any] = {
            "model": self._settings.llm_model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_tokens": max_tokens if max_tokens is not None
                          else self._settings.llm_intent_max_tokens,
            "temperature": temperature,
        }

        payload = self._post(body)

        try:
            msg = payload["choices"][0]["message"]
        except (KeyError, IndexError) as e:
            raise LLMMalformed(f"Réponse sans message: {e}") from e

        tool_calls = msg.get("tool_calls") or []
        if not tool_calls:
            raise LLMMalformed(
                f"Pas de tool_calls dans la réponse (content={msg.get('content','')[:200]!r})"
            )

        call = tool_calls[0]
        fn = call.get("function") or {}
        tool_name = fn.get("name")
        arguments_raw = fn.get("arguments", "{}")
        if not tool_name:
            raise LLMMalformed(f"tool_call sans nom de fonction: {call!r}")
        try:
            arguments = (
                json.loads(arguments_raw)
                if isinstance(arguments_raw, str)
                else (arguments_raw or {})
            )
        except json.JSONDecodeError as e:
            raise LLMMalformed(
                f"Arguments tool_call non-JSON: {arguments_raw!r} ({e})"
            ) from e

        return {
            "tool_name":     tool_name,
            "arguments":     arguments,
            "assistant_msg": msg,
            "raw_response":  payload,
            "usage":         payload.get("usage") or {},
        }

    def close(self) -> None:
        """Ferme le httpx.Client interne (uniquement si on l'a créé nous-mêmes)."""
        if self._owned_http:
            self._http.close()

    def __enter__(self) -> "LLMAdapter":
        return self

    def __exit__(self, *a) -> None:
        self.close()

    # ─── Interne : retry loop ──────────────────────────────────────────────

    def _post(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """POST /v1/chat/completions avec retry exponentiel sur 429/5xx/timeout."""
        url = f"{self._settings.llm_base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._settings.llm_api_key}",
            "Content-Type":  "application/json",
        }
        max_attempts = self._settings.llm_max_retries
        delay = 1.0

        last_error: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                r = self._http.post(url, json=body, headers=headers)
            except httpx.TimeoutException as e:
                last_error = e
                if attempt == max_attempts:
                    raise LLMTimeout(
                        f"Timeout après {max_attempts} tentatives"
                    ) from e
                log.warning(
                    "llm.timeout",
                    extra={"attempt": attempt, "max": max_attempts, "delay": delay},
                )
                _sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

            if r.status_code == 429:
                if attempt == max_attempts:
                    raise LLMRateLimit(r.text)
                retry_after = parse_retry_after(r.headers.get("Retry-After"), delay)
                log.warning(
                    "llm.rate_limit",
                    extra={"attempt": attempt, "max": max_attempts, "retry_after": retry_after},
                )
                _sleep(retry_after)
                delay = min(delay * 2, 60.0)
                continue

            if r.status_code >= 500:
                last_error = LLMError(f"{r.status_code}: {r.text}")
                if attempt == max_attempts:
                    raise last_error
                log.warning(
                    "llm.server_error",
                    extra={"attempt": attempt, "max": max_attempts, "status": r.status_code, "delay": delay},
                )
                _sleep(delay)
                delay = min(delay * 2, 60.0)
                continue

            if r.status_code != 200:
                raise LLMError(f"HTTP {r.status_code}: {r.text}")

            try:
                return r.json()
            except (ValueError, json.JSONDecodeError) as e:
                raise LLMMalformed(f"Réponse JSON invalide: {e}") from e

        # Inatteignable
        raise LLMError(f"Retries épuisés ({last_error})")


def _sleep(seconds: float) -> None:
    """Wrapper testable autour de time.sleep."""
    import time
    time.sleep(seconds)
