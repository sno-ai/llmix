"""
LLMix Resilience Module

Circuit breaker, kill switch, singleflight deduplication, and retry with
exponential backoff + jitter. All four primitives live in this single module
to keep the dependency graph flat.
"""

import asyncio
import enum
import hashlib
import os
import random
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar

try:
    import fcntl as _fcntl
except ImportError:
    _fcntl = None  # Windows

T = TypeVar("T")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_FAILURE_THRESHOLD = 3
_DEFAULT_COOLDOWN_SECONDS = 30.0
_DEFAULT_MAX_DELAY_MS = 30_000
_DEFAULT_BASE_DELAY_MS = 1_000
_DEFAULT_JITTER_MS = 1_000
_DEFAULT_MAX_RETRY_AFTER_MS = 60_000
_KILLSWITCH_FILENAME = "killswitch"
_KILLSWITCH_SUBDIR = "llmix2"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_retryable_status(status_code: int) -> bool:
    """Return True for 408, 429, and 5xx status codes."""
    return status_code == 408 or status_code == 429 or 500 <= status_code <= 599


def _resolve_state_dir() -> Path:
    """Resolve the LLMix state directory.

    Priority:
    1. LLMIX_STATE_DIR env var
    2. XDG_STATE_HOME/llmix2
    3. ~/.local/state/llmix2
    """
    env_dir = os.environ.get("LLMIX_STATE_DIR")
    if env_dir:
        return Path(env_dir)

    xdg = os.environ.get("XDG_STATE_HOME")
    if xdg:
        return Path(xdg) / _KILLSWITCH_SUBDIR

    return Path.home() / ".local" / "state" / _KILLSWITCH_SUBDIR


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class CircuitOpenError(Exception):
    """Raised when the circuit breaker is OPEN and rejecting calls."""

    def __init__(self, provider: str, base_url: str) -> None:
        self.provider = provider
        self.base_url = base_url
        super().__init__(f"Circuit breaker OPEN for ({provider}, {base_url})")


class KillSwitchActiveError(Exception):
    """Raised when the kill switch file is present."""

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(f"Kill switch active: {path} exists. All LLMix calls are blocked.")


# ---------------------------------------------------------------------------
# Circuit Breaker
# ---------------------------------------------------------------------------


class CircuitState(enum.Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


_DEFAULT_PERMITTED_HALF_OPEN_CALLS = 10


class CircuitBreaker:
    """Per-(provider, baseUrl) circuit breaker (Resilience4j-style).

    State machine:
      CLOSED    -- N consecutive retryable failures --> OPEN
      OPEN      -- cooldown expires --> HALF_OPEN
      HALF_OPEN -- allows up to `permitted_half_open_calls` concurrent probes
                   if probe success rate >= 50% --> CLOSED
                   if probe failure rate > 50%  --> OPEN (with doubled cooldown)
    """

    def __init__(
        self,
        provider: str,
        base_url: str,
        failure_threshold: int = _DEFAULT_FAILURE_THRESHOLD,
        cooldown_seconds: float = _DEFAULT_COOLDOWN_SECONDS,
        permitted_half_open_calls: int = _DEFAULT_PERMITTED_HALF_OPEN_CALLS,
    ) -> None:
        self.provider = provider
        self.base_url = base_url
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.permitted_half_open_calls = permitted_half_open_calls
        self._base_cooldown = cooldown_seconds

        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at: float = 0.0
        # HALF_OPEN probe tracking (Resilience4j-style)
        self._half_open_active: int = 0
        self._half_open_successes: int = 0
        self._half_open_failures: int = 0

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for cooldown transitions."""
        if self._state == CircuitState.OPEN:
            elapsed = time.monotonic() - self._opened_at
            if elapsed >= self.cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._half_open_active = 0
                self._half_open_successes = 0
                self._half_open_failures = 0
                return CircuitState.HALF_OPEN
        return self._state

    def check(self) -> None:
        """Check whether a request is allowed.

        Raises CircuitOpenError if the breaker is OPEN or HALF_OPEN slots full.
        In HALF_OPEN, allows up to `permitted_half_open_calls` concurrent probes.
        """
        current = self.state
        if current == CircuitState.CLOSED:
            return
        if current == CircuitState.HALF_OPEN:
            if self._half_open_active >= self.permitted_half_open_calls:
                raise CircuitOpenError(self.provider, self.base_url)
            self._half_open_active += 1
            return
        # OPEN
        raise CircuitOpenError(self.provider, self.base_url)

    def _evaluate_half_open(self) -> None:
        """Evaluate HALF_OPEN results once enough probes have completed.

        Uses a fixed window (permitted_half_open_calls) — not the active count —
        so we wait for the full sample before deciding. If a probe is lost
        (timeout/crash), the failure path in cancel_probe() counts it as a
        failure so the window always completes.
        """
        total_completed = self._half_open_successes + self._half_open_failures
        if total_completed < self.permitted_half_open_calls:
            return  # Need more samples before deciding

        if self._half_open_successes > self._half_open_failures:
            # Majority success — service recovered
            self._state = CircuitState.CLOSED
            self._consecutive_failures = 0
            self.cooldown_seconds = self._base_cooldown
        else:
            # Majority failures — re-open with exponential backoff (cap 5 min)
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self.cooldown_seconds = min(self.cooldown_seconds * 2, 300.0)

    def on_success(self) -> None:
        """Record a successful request."""
        if self._state == CircuitState.HALF_OPEN:
            self._half_open_successes += 1
            self._evaluate_half_open()
            return
        # CLOSED state — reset failure counter
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0

    def on_failure(self, status_code: int | None = None, *, network_error: bool = False) -> None:
        """Record a failed request.

        Only retryable errors (408, 429, 5xx, network errors) increment the counter.
        Auth errors (401, 403) are ignored in CLOSED state but still finalize
        the HALF_OPEN probe.
        """
        retryable = network_error
        if status_code is not None:
            retryable = retryable or _is_retryable_status(status_code)

        if self._state == CircuitState.HALF_OPEN:
            if retryable or network_error:
                self._half_open_failures += 1
            else:
                # Non-retryable (400, 404) = server is reachable, count as success
                self._half_open_successes += 1
            self._evaluate_half_open()
            return

        # Auth errors do NOT trip the breaker in CLOSED state.
        if status_code is not None and status_code in (401, 403):
            self._consecutive_failures = 0
            return

        if not retryable:
            self._consecutive_failures = 0
            return

        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()

    def cancel_probe(self) -> None:
        """Cancel an in-flight HALF_OPEN probe without recording success or failure.

        Safety net for when on_success/on_failure were never called (e.g. crash).
        No-op if the probe was already finalized — prevents double-counting when
        on_failure() and cancel_probe() both fire for the same probe.
        """
        if self._state != CircuitState.HALF_OPEN:
            return
        total_finalized = self._half_open_successes + self._half_open_failures
        if total_finalized >= self._half_open_active:
            return  # All admitted probes already reported — this is a duplicate
        self._half_open_failures += 1
        self._evaluate_half_open()

    def reset(self) -> None:
        """Manually reset the breaker to CLOSED."""
        self._state = CircuitState.CLOSED
        self._consecutive_failures = 0
        self._opened_at = 0.0
        self._half_open_active = 0
        self._half_open_successes = 0
        self._half_open_failures = 0
        self.cooldown_seconds = self._base_cooldown


# ---------------------------------------------------------------------------
# Kill Switch
# ---------------------------------------------------------------------------


class KillSwitch:
    """File-based kill switch.

    Checks for the existence of ``{stateDir}/llmix2/killswitch``.
    If the file exists, all calls are blocked.
    """

    def __init__(self, state_dir: Path | None = None) -> None:
        base = state_dir or _resolve_state_dir()
        self._path = base / _KILLSWITCH_FILENAME

    @property
    def path(self) -> Path:
        return self._path

    def check(self) -> None:
        """Raise KillSwitchActiveError if the kill switch file exists."""
        try:
            self._path.stat()
            raise KillSwitchActiveError(self._path)
        except FileNotFoundError:
            return

    def is_active(self) -> bool:
        """Return True if the kill switch file exists."""
        try:
            self._path.stat()
            return True
        except FileNotFoundError:
            return False


# ---------------------------------------------------------------------------
# Singleflight
# ---------------------------------------------------------------------------


class Singleflight:
    """Deduplicates concurrent identical async calls.

    Uses SHA-256 to key in-flight requests. Concurrent callers with the same
    key share a single future; the result (or error) is propagated to all
    waiters.
    """

    def __init__(self) -> None:
        self._in_flight: dict[str, asyncio.Future[Any]] = {}

    @staticmethod
    def make_key(data: str) -> str:
        """Generate a SHA-256 hex key from a string."""
        return hashlib.sha256(data.encode()).hexdigest()

    async def do(self, key: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Execute fn or join an existing in-flight call for the same key.

        Args:
            key: Deduplication key (use ``make_key`` for raw strings).
            fn: Async callable to execute if no in-flight request exists.

        Returns:
            The result of fn.

        Raises:
            Any exception raised by fn is propagated to all waiters.
        """
        if key in self._in_flight:
            # Shield so a cancelled follower doesn't poison the shared Future
            return await asyncio.shield(self._in_flight[key])

        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        # Prevent "Future exception was never retrieved" when no second caller awaits
        future.add_done_callback(lambda f: f.exception() if f.done() and not f.cancelled() else None)
        self._in_flight[key] = future

        try:
            result = await fn()
            future.set_result(result)
            return result
        except BaseException as exc:
            future.set_exception(exc)
            raise
        finally:
            self._in_flight.pop(key, None)

    @property
    def in_flight_count(self) -> int:
        return len(self._in_flight)


# ---------------------------------------------------------------------------
# Retry with Exponential Backoff + Jitter
# ---------------------------------------------------------------------------


def calculate_delay(
    attempt: int, *, base_ms: int = _DEFAULT_BASE_DELAY_MS, max_delay_ms: int = _DEFAULT_MAX_DELAY_MS, jitter_ms: int = _DEFAULT_JITTER_MS
) -> int:
    """Calculate retry delay in milliseconds.

    Formula: min(2^attempt * base_ms, max_delay_ms) + random(0, jitter_ms)
    """
    exponential = min((2**attempt) * base_ms, max_delay_ms)
    jitter = random.randint(0, jitter_ms)
    return exponential + jitter


def parse_retry_after(header_value: str | None, *, max_ms: int = _DEFAULT_MAX_RETRY_AFTER_MS) -> int | None:
    """Parse Retry-After header value to milliseconds.

    Supports integer seconds and HTTP-date (RFC 7231) formats.
    Returns None if unparseable. Caps at max_ms.
    """
    if header_value is None:
        return None
    try:
        seconds = int(header_value)
        if seconds < 0:
            return None
        return min(seconds * 1000, max_ms)
    except (ValueError, TypeError):
        pass

    # Fallback: try HTTP-date format (RFC 7231 §7.1.1.1)
    from email.utils import parsedate_to_datetime

    try:
        dt = parsedate_to_datetime(header_value)
        delta_ms = int((dt.timestamp() - time.time()) * 1000)
        if delta_ms > 0:
            return min(delta_ms, max_ms)
    except (ValueError, TypeError):
        pass

    return None


def is_retryable(status_code: int) -> bool:
    """Return True if the status code is retryable (408, 429, or 5xx)."""
    return _is_retryable_status(status_code)


class RetryPolicy:
    """Retry with exponential backoff and jitter.

    Can be used as an async context or called directly.
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_ms: int = _DEFAULT_BASE_DELAY_MS,
        max_delay_ms: int = _DEFAULT_MAX_DELAY_MS,
        jitter_ms: int = _DEFAULT_JITTER_MS,
        max_retry_after_ms: int = _DEFAULT_MAX_RETRY_AFTER_MS,
    ) -> None:
        self.max_retries = max_retries
        self.base_ms = base_ms
        self.max_delay_ms = max_delay_ms
        self.jitter_ms = jitter_ms
        self.max_retry_after_ms = max_retry_after_ms

    def get_delay_ms(self, attempt: int, retry_after_header: str | None = None) -> int:
        """Get the delay for a given attempt in milliseconds.

        If a Retry-After header is provided and valid, it takes precedence
        (capped at max_retry_after_ms).
        """
        retry_after = parse_retry_after(retry_after_header, max_ms=self.max_retry_after_ms)
        if retry_after is not None:
            return retry_after

        return calculate_delay(attempt, base_ms=self.base_ms, max_delay_ms=self.max_delay_ms, jitter_ms=self.jitter_ms)

    async def execute(self, fn: Callable[[], Awaitable[T]], *, is_retryable_fn: Callable[[BaseException], bool] | None = None) -> T:
        """Execute fn with retries.

        Args:
            fn: Async callable to retry.
            is_retryable_fn: Optional predicate to determine retryability.
                Defaults to always-retryable for any exception.
        """
        last_exception: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                return await fn()
            except (asyncio.CancelledError, KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:
                last_exception = exc
                if attempt >= self.max_retries:
                    raise
                if is_retryable_fn and not is_retryable_fn(exc):
                    raise
                retry_after = getattr(exc, "headers", {})
                retry_after_val = retry_after.get("retry-after") if isinstance(retry_after, dict) else None
                delay_ms = self.get_delay_ms(attempt, retry_after_val)
                await asyncio.sleep(delay_ms / 1000.0)

        # Should never reach here, but satisfy type checker
        assert last_exception is not None
        raise last_exception


# ---------------------------------------------------------------------------
# Cross-Process File Lock (opt-in via LLM_GLOBAL_CONCURRENCY env var)
# ---------------------------------------------------------------------------


class FileLock:
    """Cross-process file lock using fcntl.flock().

    Only active when ``LLM_GLOBAL_CONCURRENCY`` env var is set.
    When not set, acquire/release are no-ops.
    """

    def __init__(self, lock_path: Path | None = None) -> None:
        concurrency = os.environ.get("LLM_GLOBAL_CONCURRENCY")
        self._enabled = concurrency is not None and concurrency.strip() != ""

        if self._enabled:
            base = _resolve_state_dir()
            self._lock_path = lock_path or (base / "llmix.lock")
            self._fd: int | None = None
        else:
            self._lock_path = None
            self._fd = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def acquire(self) -> None:
        """Acquire the file lock (blocking). No-op if not enabled or on Windows."""
        if not self._enabled or self._lock_path is None:
            return
        if _fcntl is None:
            return
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self._lock_path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd

    def release(self) -> None:
        """Release the file lock. No-op if not enabled or not acquired."""
        if self._fd is not None:
            if _fcntl is not None:
                _fcntl.flock(self._fd, _fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None

    def __enter__(self) -> FileLock:
        self.acquire()
        return self

    def __exit__(self, *_: Any) -> None:
        self.release()


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "FileLock",
    "KillSwitch",
    "KillSwitchActiveError",
    "RetryPolicy",
    "Singleflight",
    "calculate_delay",
    "is_retryable",
    "parse_retry_after",
]
