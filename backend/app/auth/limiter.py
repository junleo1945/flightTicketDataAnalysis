import threading
import time


class FixedWindowLimiter:
    """固定窗口限流（进程内存）。用于登录接口按 IP 和用户名分别限流。"""

    def __init__(self, limit: int, window_seconds: float, now=time.monotonic):
        self._limit = limit
        self._window = window_seconds
        self._now = now
        self._hits: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str) -> bool:
        """返回 True 表示放行。"""
        t = self._now()
        with self._lock:
            count, reset_at = self._hits.get(key, (0, 0.0))
            if reset_at <= t:
                self._hits[key] = (1, t + self._window)
                return True
            self._hits[key] = (count + 1, reset_at)
            return count + 1 <= self._limit
