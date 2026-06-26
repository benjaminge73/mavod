"""État Telegram thread-safe.

Remplace l'utilisation directe de `context.user_data` (dict mutable
non-thread-safe) par un `UserSession` dataclass avec `asyncio.Lock`
par utilisateur. Élimine les races sur :
- TTL check + write d'historique (telegram_bot.py:172-176).
- pending_clarification pop + push (telegram_bot.py:234-237).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass(slots=True)
class PendingClarification:
    """Une clarification a été demandée et on attend la réponse user."""

    question: str
    options: Optional[Sequence[str]] = None
    missing_field: Optional[str] = None
    tool_call_id: str = ""
    asked_at: float = field(default_factory=time.time)


@dataclass(slots=True)
class UserSession:
    """Session par chat Telegram. Mutable mais accessible sous lock."""

    user_id: int
    history: List[Dict[str, Any]] = field(default_factory=list)
    pending_clarification: Optional[PendingClarification] = None
    last_turn_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def is_expired(self, ttl_seconds: int, *, now: Optional[float] = None) -> bool:
        """True si pas d'activité depuis plus de `ttl_seconds` (déclenche un flush)."""
        now = now if now is not None else time.time()
        return (now - self.last_turn_at) > ttl_seconds

    def reset(self, system_prompt: Optional[str] = None) -> None:
        """Flush l'historique. Si `system_prompt` fourni, on le réinsère en tête."""
        self.history.clear()
        if system_prompt:
            self.history.append({"role": "system", "content": system_prompt})
        self.pending_clarification = None
        self.last_turn_at = time.time()

    def touch(self) -> None:
        """Marque la session comme active (reset du TTL)."""
        self.last_turn_at = time.time()

    def truncate_history(self, max_messages: int) -> None:
        """Garde le system prompt (premier message si role=system) + les N derniers."""
        if len(self.history) <= max_messages + 1:
            return
        if self.history and self.history[0].get("role") == "system":
            self.history = [self.history[0]] + self.history[-max_messages:]
        else:
            self.history = self.history[-max_messages:]


class UserSessionStore:
    """Map user_id → UserSession. Crée à la demande, thread-safe via asyncio.Lock."""

    def __init__(self):
        self._sessions: Dict[int, UserSession] = {}
        self._store_lock = asyncio.Lock()

    async def get(self, user_id: int) -> UserSession:
        """Retourne (ou crée) la session d'un utilisateur."""
        async with self._store_lock:
            session = self._sessions.get(user_id)
            if session is None:
                session = UserSession(user_id=user_id)
                self._sessions[user_id] = session
            return session

    async def discard(self, user_id: int) -> None:
        """Supprime la session d'un utilisateur (utilisé par /reset)."""
        async with self._store_lock:
            self._sessions.pop(user_id, None)

    def snapshot(self) -> Dict[int, UserSession]:
        """Copie superficielle pour debug / introspection (pas sous lock)."""
        return dict(self._sessions)
