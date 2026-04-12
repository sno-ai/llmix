"""Provider-level circuit breaker for LLMix fallback."""

import logging
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(slots=True)
class ProviderCircuitBreakerConfig:
    failure_threshold: int = 3
    recovery_timeout: float = 60.0
    success_threshold: int = 1


class ProviderCircuitBreaker:
    _instances: ClassVar[dict[str, ProviderCircuitBreaker]] = {}
    _instances_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, provider: str, config: ProviderCircuitBreakerConfig | None = None) -> None:
        self._provider = provider
        self._config = config if config is not None else ProviderCircuitBreakerConfig()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_monotonic: float = 0.0
        self._half_open_in_flight = False
        self._lock = threading.Lock()

    @classmethod
    def for_provider(cls, provider: str, config: ProviderCircuitBreakerConfig | None = None) -> ProviderCircuitBreaker:
        key = provider.strip().lower()
        with cls._instances_lock:
            existing = cls._instances.get(key)
            if existing is None:
                existing = cls(provider=key, config=config)
                cls._instances[key] = existing
            elif config is not None and existing._config != config:
                logger.warning("[CB:%s] ignoring mismatched config for existing singleton (requested=%s, existing=%s)", key, config, existing._config)
            return existing

    @classmethod
    def reset_all(cls) -> None:
        with cls._instances_lock:
            cls._instances.clear()

    @classmethod
    def reset_provider(cls, provider: str) -> None:
        key = provider.strip().lower()
        with cls._instances_lock:
            cls._instances.pop(key, None)

    @property
    def state(self) -> CircuitState:
        return self._state

    def should_attempt_primary(self) -> bool:
        with self._lock:
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.OPEN:
                elapsed = time.monotonic() - self._last_failure_monotonic
                if elapsed >= self._config.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._success_count = 0
                    self._half_open_in_flight = True
                    logger.info("[CB:%s] OPEN -> HALF_OPEN (%.0fs elapsed, probing primary)", self._provider, elapsed)
                    return True
                return False
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_in_flight:
                    return False
                self._half_open_in_flight = True
            return True

    async def should_attempt_primary_async(self) -> bool:
        return self.should_attempt_primary()

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._half_open_in_flight = False
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._config.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._success_count = 0
                    logger.info("[CB:%s] HALF_OPEN -> CLOSED (primary recovered)", self._provider)
            elif self._state != CircuitState.CLOSED:
                self._state = CircuitState.CLOSED
                self._success_count = 0
                logger.info("[CB:%s] -> CLOSED (primary recovered)", self._provider)

    async def record_success_async(self) -> None:
        self.record_success()

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_monotonic = time.monotonic()
            self._half_open_in_flight = False

            if self._state == CircuitState.CLOSED:
                if self._failure_count >= self._config.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        "[CB:%s] CLOSED -> OPEN (consecutive_failures=%d >= threshold=%d)",
                        self._provider,
                        self._failure_count,
                        self._config.failure_threshold,
                    )
            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._success_count = 0
                logger.warning("[CB:%s] HALF_OPEN -> OPEN (probe failed)", self._provider)

    async def record_failure_async(self) -> None:
        self.record_failure()

    def force_open(self) -> None:
        with self._lock:
            self._state = CircuitState.OPEN
            self._last_failure_monotonic = time.monotonic()
            self._half_open_in_flight = False
            self._success_count = 0
            logger.warning("[CB:%s] force-opened by external trigger", self._provider)

    def get_state(self) -> dict[str, str | int | float]:
        with self._lock:
            return {
                "provider": self._provider,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
            }


__all__ = ["CircuitState", "ProviderCircuitBreaker", "ProviderCircuitBreakerConfig"]
