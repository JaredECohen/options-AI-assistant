"""History stores used by ChatService.

Contract: get(key) returns the last max_turns*2 context entries
({"role", "text"}); append_turn records one user/assistant exchange.
InMemoryHistory is for tests and the eval harness only; the API uses
app.db.repo.DurableHistory, bound to a conversation the caller owns.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryStore:
    max_turns: int = 6  # user+assistant pairs fed back as context
    _store: dict[str, list[dict]] = field(default_factory=dict)

    def get(self, session_id: str) -> list[dict]:
        return list(self._store.get(session_id, []))

    def append_turn(
        self,
        session_id: str,
        *,
        user_context: str,
        assistant_context: str,
        user_display: str = "",
        assistant_display: str = "",
        kind: str = "",
    ) -> None:
        history = self._store.get(session_id, [])
        history.extend([{"role": "user", "text": user_context}, {"role": "assistant", "text": assistant_context}])
        self._store[session_id] = history[-self.max_turns * 2 :]

    def clear(self, session_id: str) -> None:
        self._store.pop(session_id, None)


InMemoryHistory = MemoryStore
