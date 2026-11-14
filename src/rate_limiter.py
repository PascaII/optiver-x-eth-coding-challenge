"""Sliding-window request counter for insert/amend/delete requests.

Exceeding the exchange's 25 requests/s disconnects us, so every order request is
counted here before it is sent. Reads (books, positions, trades) are client-side
cache reads and are not counted.
"""

import time
from collections import deque
from typing import Callable


class RateLimiter:
    def __init__(self, max_requests: int, window_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.max_requests = max_requests
        self.window_s = window_s
        self._clock = clock
        self._sleep = sleep
        self._sent: deque[float] = deque()  # monotonic send times inside the window

    def _prune(self, now: float) -> None:
        while self._sent and now - self._sent[0] >= self.window_s:
            self._sent.popleft()

    def used(self) -> int:
        """Requests counted in the current window."""
        self._prune(self._clock())
        return len(self._sent)

    def try_acquire(self, n: int = 1) -> bool:
        """Reserve n requests if the budget allows; never blocks."""
        now = self._clock()
        self._prune(now)
        if len(self._sent) + n > self.max_requests:
            return False
        self._sent.extend([now] * n)
        return True

    def acquire_blocking(self, n: int = 1) -> None:
        """Wait until n requests fit, then reserve them. Used only during shutdown.

        n is capped at max_requests, which means waiting for an empty window.
        """
        n = min(n, self.max_requests)
        while not self.try_acquire(n):
            self._sleep(0.05)
