"""AIMD Adaptive Semaphore for rate-limit-aware concurrency control.

Adjusts concurrency window dynamically:
- Additive increase on success (+1)
- Multiplicative decrease on 429 (/2)
- Preemptive backoff from rate-limit headers
"""

import asyncio
from collections import deque

DEFAULT_INITIAL = 32
DEFAULT_MIN_CONCURRENCY = 4
HEADER_BACKOFF_THRESHOLD = 0.10


class AdaptiveSemaphore:
    """Async semaphore with AIMD concurrency control and header-based early warning.

    When rate-limit headers are available (OpenAI):
      - on_header_feedback(remaining, limit): preemptive backoff when remaining < 10%.
        Above threshold -> AIMD grow as normal. Below -> scale proportionally.
    When no headers:
      - on_success(): window += 1 (additive increase)
    Always:
      - on_rate_limit(): window //= 2 (multiplicative decrease)
    """

    def __init__(
        self,
        initial: int = DEFAULT_INITIAL,
        min_concurrency: int = DEFAULT_MIN_CONCURRENCY,
    ) -> None:
        if initial < 1:
            raise ValueError(f"initial must be >= 1, got {initial}")
        if initial < min_concurrency:
            raise ValueError(
                f"initial ({initial}) must be >= min_concurrency ({min_concurrency})"
            )
        self._max = initial
        self._min = min_concurrency
        self._window = initial
        self._available = initial
        self._waiters: deque[asyncio.Future[None]] = deque()
        self._has_header_signal = False
        self._permits_to_absorb = 0

    @property
    def window(self) -> int:
        return self._window

    @property
    def max_concurrency(self) -> int:
        return self._max

    @property
    def min_concurrency(self) -> int:
        return self._min

    def rebind(self) -> None:
        """Reset loop-bound waiter state when reusing across asyncio.run() boundaries."""
        self._available = self._window
        self._waiters = deque()
        self._permits_to_absorb = 0

    async def acquire(self) -> None:
        if self._available > 0:
            self._available -= 1
            return

        fut = asyncio.get_running_loop().create_future()
        self._waiters.append(fut)
        try:
            await fut
        except Exception:
            if fut in self._waiters:
                self._waiters.remove(fut)
            elif fut.done() and not fut.cancelled():
                self.release()
            raise

    def release(self) -> None:
        if self._permits_to_absorb > 0:
            self._permits_to_absorb -= 1
            return
        if self._wake_waiter():
            return
        if self._available < self._window:
            self._available += 1

    def on_success(self) -> None:
        """AIMD additive increase -- only when no header signals are flowing."""
        if self._has_header_signal:
            return
        if self._window < self._max:
            self._adjust_window(self._window + 1)

    def on_rate_limit(self) -> None:
        """Hard 429 signal -- always halves, overrides headers."""
        new = max(self._window // 2, self._min)
        self._adjust_window(new)
        # Reset header latch so AIMD success growth resumes after the 429 storm passes
        self._has_header_signal = False

    def on_header_feedback(self, remaining: int, limit: int) -> None:
        """Header-based early warning. Backs off when remaining is scarce."""
        if limit <= 0:
            return
        self._has_header_signal = True
        ratio = remaining / limit
        if ratio >= HEADER_BACKOFF_THRESHOLD:
            # Plenty of headroom -- let AIMD grow normally
            if self._window < self._max:
                self._adjust_window(self._window + 1)
        else:
            # Scarce: scale proportionally within the danger zone
            # ratio=threshold -> full max, ratio=0 -> min_concurrency
            scale = ratio / HEADER_BACKOFF_THRESHOLD
            target = int(self._min + scale * (self._max - self._min))
            self._adjust_window(target)

    def _adjust_window(self, target: int) -> None:
        """Shared grow/shrink logic."""
        target = max(target, self._min)
        target = min(target, self._max)
        if target == self._window:
            return
        if target > self._window:
            grow = target - self._window
            absorbed = min(grow, self._permits_to_absorb)
            self._permits_to_absorb -= absorbed
            grow -= absorbed
            for _ in range(grow):
                if not self._wake_waiter():
                    self._available += 1
        else:
            shrink = self._window - target
            immediate = min(shrink, self._available)
            self._available -= immediate
            self._permits_to_absorb += shrink - immediate
        self._window = target

    def _wake_waiter(self) -> bool:
        while self._waiters:
            waiter = self._waiters.popleft()
            if waiter.cancelled():
                continue
            waiter.set_result(None)
            return True
        return False


def parse_openai_ratelimit_headers(headers: dict[str, str]) -> dict[str, int] | None:
    """Extract rate-limit info from OpenAI response headers.

    Returns {"remaining": int, "limit": int} or None if headers not present/valid.
    """
    remaining = headers.get("x-ratelimit-remaining-requests")
    limit = headers.get("x-ratelimit-limit-requests")
    if remaining is not None and limit is not None:
        try:
            rem, lim = int(remaining), int(limit)
            if lim > 0:
                return {"remaining": rem, "limit": lim}
        except (ValueError, TypeError):
            pass
    return None
