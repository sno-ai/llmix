#!/usr/bin/env python3
"""Tests for HTTP/2 transport configuration.

Run with: uv run --project packages/llmix/python python packages/llmix/python/tests/test_http2.py

Verifies provider registry flags and factory functions without making
actual network calls.
"""

import sys
from pathlib import Path

# Add python/ to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.http2 import (
    PROVIDER_TRANSPORT,
    ProviderTransportConfig,
    create_client_for_provider,
    create_http1_client,
    create_http2_client,
    create_ratelimit_hook,
    get_provider_transport,
)

passed = 0
failed = 0


def assert_eq(actual: object, expected: object, msg: str) -> None:
    global passed, failed
    if actual == expected:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}: expected {expected!r}, got {actual!r}")


def assert_true(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


# ---- Provider registry flags ----


def test_openai_http2_enabled() -> None:
    cfg = get_provider_transport("openai")
    assert_eq(cfg.http2, True, "OpenAI should use HTTP/2")
    assert_eq(cfg.name, "openai", "OpenAI name matches")


def test_google_http2_enabled() -> None:
    cfg = get_provider_transport("google")
    assert_eq(cfg.http2, True, "Google (Gemini) should use HTTP/2 in Python")


def test_proxy_providers_http1() -> None:
    for provider in ("openrouter", "helicone"):
        cfg = get_provider_transport(provider)
        assert_eq(cfg.http2, False, f"{provider} should use HTTP/1.1")


def test_anthropic_http1() -> None:
    cfg = get_provider_transport("anthropic")
    assert_eq(cfg.http2, False, "Anthropic should use HTTP/1.1")


def test_deepseek_http1() -> None:
    cfg = get_provider_transport("deepseek")
    assert_eq(cfg.http2, False, "DeepSeek should use HTTP/1.1")


def test_unknown_provider_defaults_http1() -> None:
    cfg = get_provider_transport("some-new-provider")
    assert_eq(cfg.http2, False, "Unknown provider defaults to HTTP/1.1")
    assert_eq(cfg.name, "some-new-provider", "Unknown provider preserves name")


def test_registry_completeness() -> None:
    expected = {"openai", "anthropic", "google", "deepseek", "openrouter", "helicone"}
    assert_eq(set(PROVIDER_TRANSPORT.keys()), expected, "Registry has all expected providers")


# ---- Factory functions ----


def test_create_http2_client() -> None:
    import asyncio

    client = create_http2_client()
    assert_true(client is not None, "create_http2_client returns a client")
    asyncio.run(client.aclose())


def test_create_http1_client() -> None:
    import asyncio

    client = create_http1_client()
    assert_true(client is not None, "create_http1_client returns a client")
    asyncio.run(client.aclose())


def test_create_client_for_provider_openai() -> None:
    import asyncio

    client = create_client_for_provider("openai")
    assert_true(client is not None, "create_client_for_provider('openai') returns a client")
    asyncio.run(client.aclose())


def test_create_client_for_provider_openrouter() -> None:
    import asyncio

    client = create_client_for_provider("openrouter")
    assert_true(client is not None, "create_client_for_provider('openrouter') returns a client")
    asyncio.run(client.aclose())


# ---- Rate-limit hook ----


class FakeSemaphore:
    """Minimal stand-in for AdaptiveSemaphore to capture hook calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    def on_header_feedback(self, remaining: int, limit: int) -> None:
        self.calls.append((remaining, limit))


def test_ratelimit_hook_structure() -> None:
    sem = FakeSemaphore()
    hooks = create_ratelimit_hook(sem)
    assert_true("response" in hooks, "Hook dict contains 'response' key")
    assert_eq(len(hooks["response"]), 1, "Hook has exactly one response handler")


def test_ratelimit_hook_extracts_headers() -> None:
    """Verify the hook calls on_header_feedback with parsed header values."""
    import asyncio

    import httpx

    sem = FakeSemaphore()
    hooks = create_ratelimit_hook(sem)
    handler = hooks["response"][0]

    # Build a fake response with rate-limit headers
    response = httpx.Response(
        200,
        headers={
            "x-ratelimit-remaining-requests": "42",
            "x-ratelimit-limit-requests": "100",
        },
    )
    asyncio.run(handler(response))
    assert_eq(len(sem.calls), 1, "Hook called on_header_feedback once")
    assert_eq(sem.calls[0], (42, 100), "Hook parsed correct remaining/limit")


def test_ratelimit_hook_ignores_missing_headers() -> None:
    """Hook should silently skip when rate-limit headers are absent."""
    import asyncio

    import httpx

    sem = FakeSemaphore()
    hooks = create_ratelimit_hook(sem)
    handler = hooks["response"][0]

    response = httpx.Response(200)
    asyncio.run(handler(response))
    assert_eq(len(sem.calls), 0, "Hook does not call feedback when headers missing")


def test_ratelimit_hook_ignores_zero_limit() -> None:
    """Hook should skip when limit header is 0."""
    import asyncio

    import httpx

    sem = FakeSemaphore()
    hooks = create_ratelimit_hook(sem)
    handler = hooks["response"][0]

    response = httpx.Response(
        200,
        headers={
            "x-ratelimit-remaining-requests": "5",
            "x-ratelimit-limit-requests": "0",
        },
    )
    asyncio.run(handler(response))
    assert_eq(len(sem.calls), 0, "Hook does not call feedback when limit is 0")


# ---- Run all ----

if __name__ == "__main__":
    test_openai_http2_enabled()
    test_google_http2_enabled()
    test_proxy_providers_http1()
    test_anthropic_http1()
    test_deepseek_http1()
    test_unknown_provider_defaults_http1()
    test_registry_completeness()
    test_create_http2_client()
    test_create_http1_client()
    test_create_client_for_provider_openai()
    test_create_client_for_provider_openrouter()
    test_ratelimit_hook_structure()
    test_ratelimit_hook_extracts_headers()
    test_ratelimit_hook_ignores_missing_headers()
    test_ratelimit_hook_ignores_zero_limit()

    print(f"\n{'=' * 40}")
    print(f"Results: {passed} passed, {failed} failed")
    if failed > 0:
        sys.exit(1)
    print("All tests passed!")
