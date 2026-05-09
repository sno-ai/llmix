"""
LLMix Integration Test Infrastructure

Shared helpers for real integration tests:
- Environment validation and skip decorators
- Instrumented real-dispatch wrapper (real HTTP + observability)
- Real provider dispatch functions (OpenAI, Anthropic, Gemini)
- Assertion helpers with deep result evaluation
- Timing utilities
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure llmix is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from llmix.pipeline import (
    DispatchInput,
    LLMUsage,
    ProviderError,  # noqa: F401 — re-exported for test files
    ProviderResult,
    CallInput,
    CallPipeline,
    PipelineConfig,
    CallResponse,
)
from llmix.key_pool import KeyPool
from llmix.response_cache import TwoTierCache


# =============================================================================
# Environment helpers
# =============================================================================

_ENV_ALIASES: dict[str, tuple[str, ...]] = {
    "SNO_LLM_API_KEY": ("SNO_LLM_API_KEY", "INTERNAL_SERVICE_SECRET"),
}


def env(name: str) -> str | None:
    names = _ENV_ALIASES.get(name, (name,))
    for candidate in names:
        value = os.environ.get(candidate)
        if value:
            return value
    return None


def require_env(name: str) -> str:
    val = env(name)
    if not val:
        raise EnvironmentError(f"Missing required env var: {name}")
    return val


def has_env(*names: str) -> bool:
    return all(env(n) for n in names)


def skip_unless(*env_names: str):
    """Decorator: skip test if any env var is missing."""
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            missing = [n for n in env_names if not env(n)]
            if missing:
                print(f"  [SKIP] {fn.__name__}: missing {', '.join(missing)}")
                return "skipped"
            return await fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


TIER_T1 = os.environ.get("LLMIX_TEST_TIER", "t1")
TIER_T2 = TIER_T1 in ("t2", "t3")
TIER_T3 = TIER_T1 == "t3"


def skip_unless_tier(min_tier: str):
    """Decorator: skip if test tier is below minimum."""
    tier_order = {"t1": 1, "t2": 2, "t3": 3}
    def decorator(fn):
        async def wrapper(*args, **kwargs):
            if tier_order.get(TIER_T1, 1) < tier_order[min_tier]:
                print(f"  [SKIP] {fn.__name__}: requires tier {min_tier}, running {TIER_T1}")
                return "skipped"
            return await fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# =============================================================================
# Instrumented dispatch wrapper
# =============================================================================

@dataclass
class DispatchRecord:
    """Single dispatch invocation record."""
    provider: str
    model: str
    api_key: str
    messages: list[dict[str, Any]]
    kwargs: dict[str, Any]
    result: ProviderResult | None = None
    error: Exception | None = None
    elapsed_ms: float = 0.0


@dataclass
class InstrumentedDispatch:
    """Wraps a real dispatch function with call recording.

    NOT a mock — makes real HTTP calls. Adds observability:
    - Counts invocations
    - Captures API keys used
    - Records response headers
    - Measures latency
    """
    real_dispatch: Any  # The actual dispatch function
    records: list[DispatchRecord] = field(default_factory=list)

    @property
    def call_count(self) -> int:
        return len(self.records)

    @property
    def keys_used(self) -> list[str]:
        return [r.api_key for r in self.records]

    @property
    def unique_keys(self) -> set[str]:
        return {r.api_key for r in self.records}

    @property
    def last_result(self) -> ProviderResult | None:
        return self.records[-1].result if self.records else None

    async def __call__(self, ctx: DispatchInput) -> ProviderResult:
        record = DispatchRecord(
            provider=ctx.provider,
            model=ctx.model,
            api_key=ctx.api_key,
            messages=ctx.messages,
            kwargs=ctx.kwargs,
        )
        t0 = time.monotonic()
        try:
            result = await self.real_dispatch(ctx)
            record.result = result
            record.elapsed_ms = (time.monotonic() - t0) * 1000
            self.records.append(record)
            return result
        except Exception as exc:
            record.error = exc
            record.elapsed_ms = (time.monotonic() - t0) * 1000
            self.records.append(record)
            raise


# =============================================================================
# Real provider dispatch functions
# =============================================================================

async def openai_dispatch(ctx: DispatchInput) -> ProviderResult:
    """Real OpenAI dispatch via SDK. No mocks."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=ctx.api_key)

    kwargs: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        kwargs["temperature"] = ctx.kwargs["temperature"]
    if ctx.kwargs.get("max_tokens") is not None:
        kwargs["max_tokens"] = ctx.kwargs["max_tokens"]
    if ctx.kwargs.get("top_p") is not None:
        kwargs["top_p"] = ctx.kwargs["top_p"]
    if ctx.kwargs.get("seed") is not None:
        kwargs["seed"] = ctx.kwargs["seed"]
    if ctx.kwargs.get("response_format") is not None:
        kwargs["response_format"] = ctx.kwargs["response_format"]

    response = await client.chat.completions.create(
        model=ctx.model,
        messages=ctx.messages,  # type: ignore[arg-type]
        **kwargs,
    )

    choice = response.choices[0]
    content = choice.message.content or ""
    usage = response.usage
    headers_dict: dict[str, str] = {}
    if hasattr(response, "_raw_response") and hasattr(response._raw_response, "headers"):
        for k, v in response._raw_response.headers.items():
            if k.startswith("x-ratelimit"):
                headers_dict[k] = v

    return ProviderResult(
        content=content,
        model=response.model,
        usage=LLMUsage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        ),
        headers=headers_dict or None,
    )


async def anthropic_dispatch(ctx: DispatchInput) -> ProviderResult:
    """Real Anthropic dispatch via SDK. No mocks."""
    import anthropic

    client = anthropic.AsyncAnthropic(api_key=ctx.api_key)

    # Extract system message (Anthropic requires it as separate param)
    system_parts: list[str] = []
    user_messages: list[dict[str, str]] = []
    for msg in ctx.messages:
        if msg.get("role") == "system":
            system_parts.append(msg.get("content", ""))
        else:
            user_messages.append(msg)

    kwargs: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        kwargs["temperature"] = ctx.kwargs["temperature"]
    max_tokens = ctx.kwargs.get("max_tokens") or 1024
    system_text = "\n".join(system_parts) if system_parts else None

    create_kwargs: dict[str, Any] = {
        "model": ctx.model,
        "messages": user_messages,
        "max_tokens": max_tokens,
        **kwargs,
    }
    if system_text:
        create_kwargs["system"] = system_text

    response = await client.messages.create(**create_kwargs)  # type: ignore[arg-type]

    content = ""
    for block in response.content:
        if hasattr(block, "text"):
            content += block.text

    return ProviderResult(
        content=content,
        model=response.model,
        usage=LLMUsage(
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            total_tokens=response.usage.input_tokens + response.usage.output_tokens,
        ),
    )


async def gemini_dispatch(ctx: DispatchInput) -> ProviderResult:
    """Real Gemini dispatch via google-genai SDK. No mocks."""
    from google import genai

    client = genai.Client(api_key=ctx.api_key)

    # Reformat messages for Gemini
    system_instruction = None
    contents: list[dict[str, Any]] = []
    for msg in ctx.messages:
        role = msg.get("role", "user")
        text = msg.get("content", "")
        if role == "system":
            system_instruction = text
        elif role == "assistant":
            contents.append({"role": "model", "parts": [{"text": text}]})
        else:
            contents.append({"role": "user", "parts": [{"text": text}]})

    # Ensure conversation ends with user role
    if contents and contents[-1]["role"] == "model":
        contents.append({"role": "user", "parts": [{"text": "Continue."}]})

    config: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        config["temperature"] = ctx.kwargs["temperature"]
    if ctx.kwargs.get("max_tokens") is not None:
        config["max_output_tokens"] = ctx.kwargs["max_tokens"]
    if ctx.kwargs.get("top_p") is not None:
        config["top_p"] = ctx.kwargs["top_p"]

    # ThinkingConfig support — pipeline sets thinking_config via gemini_transform_kwargs
    thinking_config = ctx.kwargs.get("thinking_config")
    if thinking_config and isinstance(thinking_config, dict):
        budget = thinking_config.get("thinking_budget")
        if budget is not None:
            config["thinking_config"] = genai.types.ThinkingConfig(
                thinking_budget=budget,
            )

    response = await asyncio.to_thread(
        client.models.generate_content,
        model=ctx.model,
        contents=contents,  # type: ignore[arg-type]
        config=genai.types.GenerateContentConfig(
            system_instruction=system_instruction,
            **config,
        ),
    )

    content = response.text or ""
    usage_meta = response.usage_metadata
    return ProviderResult(
        content=content,
        model=ctx.model,
        usage=LLMUsage(
            input_tokens=usage_meta.prompt_token_count if usage_meta else 0,
            output_tokens=usage_meta.candidates_token_count if usage_meta else 0,
            total_tokens=usage_meta.total_token_count if usage_meta else 0,
        ),
    )


async def sno_gpu_dispatch(ctx: DispatchInput) -> ProviderResult:
    """Real SnoGPU dispatch via OpenAI-compatible endpoint. No mocks."""
    from openai import AsyncOpenAI

    base_url = ctx.kwargs.get("base_url") or ctx.kwargs.get("baseUrl") or ""
    headers = {}
    secret = env("SNO_LLM_API_KEY")
    if secret:
        headers["X-Sno-LLM-Key"] = secret

    client = AsyncOpenAI(
        api_key=ctx.api_key or "not-needed",
        base_url=base_url,
        default_headers=headers,
    )

    kwargs: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        kwargs["temperature"] = ctx.kwargs["temperature"]
    if ctx.kwargs.get("max_tokens") is not None:
        kwargs["max_tokens"] = ctx.kwargs["max_tokens"]

    # Thinking mode control for Qwen3.5
    # Send both top-level and chat_template_kwargs for OpenAI-compatible servers
    # that inspect either location.
    enable_thinking = ctx.kwargs.get("enable_thinking", False)
    extra_body: dict[str, Any] = {"enable_thinking": enable_thinking}
    chat_kwargs: dict[str, Any] = {"enable_thinking": enable_thinking}
    extra_body["chat_template_kwargs"] = chat_kwargs
    kwargs["extra_body"] = extra_body

    response = await client.chat.completions.create(
        model=ctx.model,
        messages=ctx.messages,  # type: ignore[arg-type]
        **kwargs,
    )

    choice = response.choices[0]
    content = choice.message.content or ""

    usage = response.usage
    return ProviderResult(
        content=content,
        model=response.model,
        usage=LLMUsage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        ),
    )


async def novita_dispatch(ctx: DispatchInput) -> ProviderResult:
    """Real Novita dispatch via OpenAI-compatible endpoint. No mocks."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=ctx.api_key,
        base_url="https://api.novita.ai/v3/openai",
    )

    kwargs: dict[str, Any] = {}
    if ctx.kwargs.get("temperature") is not None:
        kwargs["temperature"] = ctx.kwargs["temperature"]
    if ctx.kwargs.get("max_tokens") is not None:
        kwargs["max_tokens"] = ctx.kwargs["max_tokens"]

    response = await client.chat.completions.create(
        model=ctx.model,
        messages=ctx.messages,  # type: ignore[arg-type]
        **kwargs,
    )

    choice = response.choices[0]
    content = choice.message.content or ""
    usage = response.usage

    # Novita returns thinking in reasoning_content (separate from content).
    # Wrap it in <think> tags so the pipeline's strip_thinking can process it.
    reasoning = getattr(choice.message, "reasoning_content", None)
    if reasoning:
        content = f"<think>{reasoning}</think>{content}"

    return ProviderResult(
        content=content,
        model=response.model,
        usage=LLMUsage(
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
        ),
    )


# =============================================================================
# Pipeline factory
# =============================================================================

def make_real_pipeline(
    dispatch_fn,
    provider: str = "openai",
    api_key: str | None = None,
    *,
    cache: TwoTierCache | None = None,
    max_retries: int = 2,
    **overrides,
) -> tuple[CallPipeline, InstrumentedDispatch]:
    """Create a pipeline with an instrumented REAL dispatch.

    Returns (pipeline, instrumented_dispatch) so tests can inspect call records.
    """
    instrumented = InstrumentedDispatch(real_dispatch=dispatch_fn)
    config = PipelineConfig(
        dispatch=instrumented,
        max_retries=max_retries,
        retry_base_ms=500,
        retry_max_delay_ms=5000,
        response_cache=cache,
        **overrides,
    )
    pipeline = CallPipeline(config)
    key = api_key or env(f"{provider.upper()}_API_KEY") or "no-key"
    pipeline.set_key_pool(provider, KeyPool([key]))
    return pipeline, instrumented


def make_call_input(
    provider: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
    seed: int | None = None,
    top_p: float | None = None,
    keep_thinking_output: bool = False,
    caching_strategy: str = "disabled",
    provider_options: dict | None = None,
    base_url: str | None = None,
) -> CallInput:
    """Build a CallInput with real config values."""
    common: dict[str, Any] = {}
    if temperature is not None:
        common["temperature"] = temperature
    if max_output_tokens is not None:
        common["max_output_tokens"] = max_output_tokens
    if seed is not None:
        common["seed"] = seed
    if top_p is not None:
        common["top_p"] = top_p
    if keep_thinking_output:
        common["keep_thinking_output"] = True

    config: dict[str, Any] = {
        "provider": provider,
        "model": model,
        "common": common,
        "caching": {"strategy": caching_strategy},
    }
    if provider_options:
        config["provider_options"] = provider_options
    if base_url:
        config["baseUrl"] = base_url

    return CallInput(config=config, messages=messages)


# =============================================================================
# Assertion helpers — evaluate deeply, don't just check "got output"
# =============================================================================

_passed = 0
_failed = 0
_skipped = 0


def assert_true(condition: bool, msg: str) -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"  [PASS] {msg}")
    else:
        _failed += 1
        print(f"  [FAIL] {msg}")


def assert_eq(actual: object, expected: object, msg: str) -> None:
    if actual == expected:
        assert_true(True, msg)
    else:
        assert_true(False, f"{msg}: expected {expected!r}, got {actual!r}")


def assert_contains(haystack: str, needle: str, msg: str) -> None:
    if needle in haystack:
        assert_true(True, msg)
    else:
        assert_true(False, f"{msg}: {needle!r} not found in {haystack[:100]!r}...")


def assert_not_contains(haystack: str, needle: str, msg: str) -> None:
    if needle not in haystack:
        assert_true(True, msg)
    else:
        assert_true(False, f"{msg}: {needle!r} FOUND in {haystack[:100]!r}...")


def assert_gt(actual: float | int, threshold: float | int, msg: str) -> None:
    assert_true(actual > threshold, f"{msg}: {actual} > {threshold}")


def assert_lt(actual: float | int, threshold: float | int, msg: str) -> None:
    assert_true(actual < threshold, f"{msg}: {actual} < {threshold}")


def assert_success(r: CallResponse, msg: str) -> None:
    """Assert call succeeded with non-empty content and valid usage."""
    assert_true(r.success, f"{msg}: success=True")
    assert_true(len(r.content) > 0, f"{msg}: content non-empty ({len(r.content)} chars)")
    assert_true(r.error is None, f"{msg}: no error")


def assert_failed(r: CallResponse, msg: str) -> None:
    """Assert call failed with error message."""
    assert_true(not r.success, f"{msg}: success=False")
    assert_true(r.error is not None and len(r.error) > 0, f"{msg}: error present")


def assert_valid_usage(r: CallResponse, msg: str) -> None:
    """Assert usage tokens are consistent and non-zero.

    Note: total_tokens >= input + output because some providers (Gemini)
    include internal thinking tokens in total_token_count.
    """
    u = r.usage
    assert_gt(u.input_tokens, 0, f"{msg}: input_tokens > 0")
    assert_gt(u.output_tokens, 0, f"{msg}: output_tokens > 0")
    assert_true(
        u.total_tokens >= u.input_tokens + u.output_tokens,
        f"{msg}: total >= input + output ({u.total_tokens} >= {u.input_tokens} + {u.output_tokens})",
    )


def assert_json_parseable(content: str, msg: str) -> dict | list | None:
    """Assert content is valid JSON, return parsed."""
    try:
        parsed = json.loads(content)
        assert_true(True, f"{msg}: valid JSON")
        return parsed
    except json.JSONDecodeError as e:
        assert_true(False, f"{msg}: invalid JSON — {e}")
        return None


def print_summary(suite_name: str) -> int:
    global _passed, _failed, _skipped
    total = _passed + _failed
    print(f"\n{'='*60}")
    print(f"{suite_name}: {_passed} passed, {_failed} failed, {total} total")
    if _skipped:
        print(f"  ({_skipped} skipped)")
    print(f"{'='*60}")
    code = 1 if _failed > 0 else 0
    # Reset for next suite
    _passed = _failed = _skipped = 0
    return code
