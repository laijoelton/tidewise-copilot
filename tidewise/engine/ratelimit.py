"""Fixed-window rate limiter."""

import time
from typing import Dict, Tuple


class RateLimiter:
    def __init__(self, window_s: float = 1.0):
        self.window_s = window_s
        self._state: Dict[str, Tuple[float, int]] = {}

    def allow(self, key: str, capacity: int) -> bool:
        now = time.time()
        start, count = self._state.get(key, (now, 0))
        if now - start >= self.window_s:
            start, count = now, 0
        if count >= capacity:
            self._state[key] = (start, count)
            return False
        self._state[key] = (start, count + 1)
        return True
