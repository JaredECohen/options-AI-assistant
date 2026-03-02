from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class MemoryStore:
    max_turns: int = 6  # number of user+assistant pairs to keep
    _store: Dict[str, List[dict]] = field(default_factory=dict)

    def get(self, session_id: str) -> List[dict]:
        return list(self._store.get(session_id, []))

    def set(self, session_id: str, history: List[dict]) -> None:
        self._store[session_id] = history

    def append(self, session_id: str, role: str, text: str) -> None:
        history = self._store.get(session_id, [])
        history.append({"role": role, "text": text})
        # keep last max_turns*2 messages
        self._store[session_id] = history[-self.max_turns * 2 :]

    def clear(self, session_id: str) -> None:
        if session_id in self._store:
            del self._store[session_id]
