from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from bot.everything import FileHit


@dataclass
class UserSession:
    keyword: str
    ext: str | None
    results: list[FileHit]
    total: int
    page: int = 0
    more_formats: bool = False
    updated_at: float = field(default_factory=time.time)
    result_chat_id: int | None = None
    result_message_id: int | None = None

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionStore:
    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[int, UserSession] = {}

    def get(self, user_id: int) -> UserSession | None:
        session = self._sessions.get(user_id)
        if session is None:
            return None
        if time.time() - session.updated_at > self.ttl_seconds:
            self._sessions.pop(user_id, None)
            return None
        return session

    def put(self, user_id: int, session: UserSession) -> UserSession:
        session.touch()
        self._sessions[user_id] = session
        return session

    def clear(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._hits: dict[int, deque[float]] = defaultdict(deque)

    def allow(self, user_id: int) -> bool:
        now = time.time()
        bucket = self._hits[user_id]
        cutoff = now - self.window_seconds
        while bucket and bucket[0] <= cutoff:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return False
        bucket.append(now)
        return True
