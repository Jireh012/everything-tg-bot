from __future__ import annotations

import secrets
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


class InlineHitStore:
    """Inline 结果短链，供 /start dl_<token> 在私聊下载。"""

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._hits: dict[str, tuple[float, FileHit]] = {}

    def put(self, hit: FileHit) -> str:
        self._purge()
        token = secrets.token_urlsafe(8)
        self._hits[token] = (time.time(), hit)
        return token

    def get(self, token: str) -> FileHit | None:
        item = self._hits.get(token)
        if item is None:
            return None
        ts, hit = item
        if time.time() - ts > self.ttl_seconds:
            self._hits.pop(token, None)
            return None
        return hit

    def _purge(self) -> None:
        now = time.time()
        expired = [key for key, (ts, _) in self._hits.items() if now - ts > self.ttl_seconds]
        for key in expired:
            self._hits.pop(key, None)


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
