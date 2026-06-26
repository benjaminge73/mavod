"""Tests de mavod.telegram.state."""

from __future__ import annotations

import asyncio
import time

import pytest

from mavod.telegram.state import (
    PendingClarification,
    UserSession,
    UserSessionStore,
)

pytestmark = pytest.mark.unit


class TestUserSession:
    def test_initial_state(self):
        """État initial d'une UserSession."""
        s = UserSession(user_id=1)
        assert s.user_id == 1
        assert s.history == []
        assert s.pending_clarification is None

    def test_is_expired(self):
        """Détecte une session expirée selon le TTL."""
        s = UserSession(user_id=1)
        s.last_turn_at = time.time() - 1000
        assert s.is_expired(ttl_seconds=500) is True
        assert s.is_expired(ttl_seconds=2000) is False

    def test_reset_with_prompt(self):
        """Reset conserve le system prompt fourni."""
        s = UserSession(user_id=1)
        s.history = [{"role": "user", "content": "foo"}]
        s.pending_clarification = PendingClarification(question="?")
        s.reset(system_prompt="SYS")
        assert s.history == [{"role": "system", "content": "SYS"}]
        assert s.pending_clarification is None

    def test_reset_without_prompt(self):
        """Reset sans prompt vide totalement l'historique."""
        s = UserSession(user_id=1)
        s.history = [{"role": "user", "content": "foo"}]
        s.reset()
        assert s.history == []

    def test_truncate_preserves_system(self):
        """Le truncate conserve le system prompt en tête."""
        s = UserSession(user_id=1)
        s.history = [{"role": "system", "content": "SYS"}]
        for i in range(30):
            s.history.append({"role": "user", "content": f"msg{i}"})
        s.truncate_history(max_messages=10)
        assert len(s.history) == 11
        assert s.history[0]["role"] == "system"
        assert s.history[-1]["content"] == "msg29"

    def test_truncate_no_system_prompt(self):
        """Le truncate fonctionne sans system prompt."""
        s = UserSession(user_id=1)
        for i in range(15):
            s.history.append({"role": "user", "content": f"msg{i}"})
        s.truncate_history(max_messages=5)
        assert len(s.history) == 5
        assert s.history[0]["content"] == "msg10"


# UserSessionStore tests : pas de pytest-asyncio → wrappers `asyncio.run`.


def test_session_store_get_creates_lazily():
    """Le store crée la session paresseusement."""
    async def run():
        store = UserSessionStore()
        s1 = await store.get(1)
        s2 = await store.get(1)
        assert s1 is s2

    asyncio.run(run())


def test_session_store_per_user():
    """Chaque utilisateur a sa propre session."""
    async def run():
        store = UserSessionStore()
        s1 = await store.get(1)
        s2 = await store.get(2)
        assert s1 is not s2

    asyncio.run(run())


def test_session_store_discard():
    """discard supprime la session d'un utilisateur."""
    async def run():
        store = UserSessionStore()
        s1 = await store.get(1)
        await store.discard(1)
        s2 = await store.get(1)
        assert s1 is not s2

    asyncio.run(run())


def test_session_store_snapshot():
    """snapshot retourne un instantané des sessions."""
    async def run():
        store = UserSessionStore()
        await store.get(1)
        await store.get(2)
        snap = store.snapshot()
        assert set(snap.keys()) == {1, 2}

    asyncio.run(run())
