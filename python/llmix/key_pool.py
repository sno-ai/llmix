"""Key rotation pool for API key management.

Round-robin key selection with dead-key tracking.
On 429: rotate() advances to next key.
On 401/403: mark_dead() permanently removes a key from rotation.
Thread-safe via threading.Lock.

Env var convention:
  {PROVIDER}_KEYS  — comma-separated list (preferred)
  {PROVIDER}_API_KEY — single key fallback (pool of 1)
"""

import os
import threading


class KeyPoolExhaustedError(Exception):
    """All keys in the pool are dead (401/403)."""


class KeyPool:
    """Round-robin pool of API keys with dead-key tracking."""

    def __init__(self, keys: list[str]) -> None:
        cleaned = [k.strip() for k in keys if k.strip()]
        if not cleaned:
            raise ValueError("KeyPool requires at least one non-empty key")
        self._keys = list(dict.fromkeys(cleaned))
        self._dead: set[str] = set()
        self._idx = 0
        self._lock = threading.Lock()

    def select(self) -> str:
        """Return the current key, skipping dead keys.

        Raises KeyPoolExhaustedError if all keys are dead.
        """
        with self._lock:
            if len(self._dead) >= len(self._keys):
                raise KeyPoolExhaustedError(f"All {len(self._keys)} keys are dead (401/403). Replace keys or wait for provider resolution.")

            for offset in range(len(self._keys)):
                idx = (self._idx + offset) % len(self._keys)
                key = self._keys[idx]
                if key not in self._dead:
                    self._idx = idx
                    return key

            raise KeyPoolExhaustedError("All keys are dead")

    def rotate(self) -> None:
        """Advance to the next key (called on 429)."""
        with self._lock:
            if len(self._dead) >= len(self._keys):
                return

            for offset in range(1, len(self._keys) + 1):
                idx = (self._idx + offset) % len(self._keys)
                if self._keys[idx] not in self._dead:
                    self._idx = idx
                    return

    def mark_dead(self, key: str) -> None:
        """Mark a key as permanently failed (called on 401/403)."""
        with self._lock:
            if key not in self._keys:
                raise ValueError("Key not in pool: cannot mark unknown key as dead")
            self._dead.add(key)

    def is_exhausted(self) -> bool:
        """Return True if all keys are dead."""
        with self._lock:
            return len(self._dead) >= len(self._keys)

    @property
    def alive_count(self) -> int:
        """Number of keys still alive."""
        with self._lock:
            return len(self._keys) - len(self._dead)

    @property
    def total_count(self) -> int:
        """Total number of keys in the pool."""
        return len(self._keys)


def load_keys_from_env(provider: str) -> KeyPool:
    """Create a KeyPool from environment variables.

    Checks {PROVIDER}_KEYS first (comma-separated), then falls back
    to {PROVIDER}_API_KEY (pool of 1).

    Args:
        provider: Provider name (e.g. "OPENAI", "ANTHROPIC").
                  Converted to uppercase automatically.

    Raises:
        ValueError: If neither env var is set.
    """
    provider_upper = provider.upper()

    # Try {PROVIDER}_KEYS first (comma-separated pool)
    keys_var = f"{provider_upper}_KEYS"
    keys_raw = os.getenv(keys_var, "")
    if keys_raw.strip():
        return KeyPool(keys_raw.split(","))

    # Fall back to {PROVIDER}_API_KEY (single key)
    key_var = f"{provider_upper}_API_KEY"
    single_key = os.getenv(key_var, "")
    if single_key.strip():
        return KeyPool([single_key])

    raise ValueError(f"No API keys found for {provider_upper}. Set {keys_var} (comma-separated) or {key_var}.")
