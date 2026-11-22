"""Tiny in-memory rate limiter for the paid AI route.

Two limits:
  - per visitor (by IP): N requests per window. Every request counts, valid or not,
    so spamming the route is blocked too.
  - whole site: at most M AI runs per day. This is the real money guard: it holds
    even if someone fakes their IP to dodge the per-visitor limit.

Limits live in memory, so they reset when the server restarts (Render's free tier
restarts after idling). Fine for a class prototype; a multi-server app would need
a shared store such as Redis.
"""

import threading
import time
from collections import defaultdict, deque


class RateLimiter:
    def __init__(self, per_visitor: int, window_s: int, global_per_day: int):
        self.per_visitor = per_visitor
        self.window_s = window_s
        self.global_per_day = global_per_day
        self._hits: dict[str, deque] = defaultdict(deque)
        self._day = ""
        self._runs_today = 0
        self._lock = threading.Lock()

    def allow_visitor(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window_s:
                hits.popleft()
            if len(hits) >= self.per_visitor:
                return False
            hits.append(now)
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until this visitor's oldest request leaves the window."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 1
            return max(1, int(self.window_s - (time.monotonic() - hits[0])) + 1)

    def allow_ai_run(self) -> bool:
        today = time.strftime("%Y-%m-%d", time.gmtime())
        with self._lock:
            if today != self._day:
                self._day, self._runs_today = today, 0
            if self._runs_today >= self.global_per_day:
                return False
            self._runs_today += 1
            return True
