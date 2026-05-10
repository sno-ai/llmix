#!/usr/bin/env python3
"""
Provider kwargs injection tests.

Tests each provider's kwargs transformation callback:
- OpenAI: reasoning model temperature/top_p stripping
- OpenRouter: extra_body.provider.sort injection
- Gemini: thinking_budget default
- Sno GPU: base URL construction
- No-op when callback is null

Run with: uv run --project packages/llmix/python python packages/llmix/python/tests/test_provider_kwargs.py
"""

import sys
from pathlib import Path

# Add python/ to path so llmix is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.provider_kwargs import (  # noqa: E402
    PROVIDER_KWARGS_REGISTRY,
    apply_transform_kwargs,
    gemini_transform_kwargs,
    openai_transform_kwargs,
    openrouter_transform_kwargs,
    sno_gpu_transform_kwargs,
)

passed = 0
failed = 0


def assert_eq(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"+ {msg}")
    else:
        failed += 1
        print(f"x {msg}")


# =============================================================================
# No-op when callback is None
# =============================================================================

base_ctx = {"model": "gpt-4o", "provider": "openai"}
base_kwargs = {"temperature": 0.7, "top_p": 0.9}

result = apply_transform_kwargs(base_ctx, base_kwargs, None)  # type: ignore[arg-type]
assert_eq(result is base_kwargs, "None callback returns kwargs unchanged (same reference)")

# =============================================================================
# OpenAI: reasoning model detection strips temperature and top_p
# =============================================================================

# Standard model -- no stripping
std_result = openai_transform_kwargs({"model": "gpt-4o", "provider": "openai"}, {"temperature": 0.5, "top_p": 0.8, "max_tokens": 100})  # type: ignore[arg-type]
assert_eq(std_result["temperature"] == 0.5, "OpenAI standard model keeps temperature")
assert_eq(std_result["top_p"] == 0.8, "OpenAI standard model keeps top_p")
assert_eq(std_result["max_tokens"] == 100, "OpenAI standard model keeps max_tokens")

# o-series reasoning model -- strip temperature and top_p
o3_result = openai_transform_kwargs({"model": "o3-mini", "provider": "openai"}, {"temperature": 0.5, "top_p": 0.8, "max_tokens": 100})  # type: ignore[arg-type]
assert_eq("temperature" not in o3_result, "OpenAI o3-mini strips temperature")
assert_eq("top_p" not in o3_result, "OpenAI o3-mini strips top_p")
assert_eq("max_tokens" not in o3_result, "OpenAI o3-mini removes max_tokens")
assert_eq(o3_result["max_completion_tokens"] == 100, "OpenAI o3-mini renames to max_completion_tokens")

# gpt-5 reasoning model -- strip
gpt5_result = openai_transform_kwargs({"model": "gpt-5", "provider": "openai"}, {"temperature": 0.7, "top_p": 0.9})  # type: ignore[arg-type]
assert_eq("temperature" not in gpt5_result, "OpenAI gpt-5 strips temperature")
assert_eq("top_p" not in gpt5_result, "OpenAI gpt-5 strips top_p")

# gpt-5-chat-latest IS a reasoning model — all gpt-5* variants strip temperature
gpt5_chat_result = openai_transform_kwargs({"model": "gpt-5-chat-latest", "provider": "openai"}, {"temperature": 0.7, "top_p": 0.9})  # type: ignore[arg-type]
assert_eq("temperature" not in gpt5_chat_result, "OpenAI gpt-5-chat-latest strips temperature (all gpt-5* are reasoning models)")
assert_eq("top_p" not in gpt5_chat_result, "OpenAI gpt-5-chat-latest strips top_p")

# o1 reasoning model
o1_result = openai_transform_kwargs({"model": "o1-preview", "provider": "openai"}, {"temperature": 0.3})  # type: ignore[arg-type]
assert_eq("temperature" not in o1_result, "OpenAI o1-preview strips temperature")

# codex- reasoning model
codex_result = openai_transform_kwargs({"model": "codex-mini", "provider": "openai"}, {"temperature": 0.5, "top_p": 0.7})  # type: ignore[arg-type]
assert_eq("temperature" not in codex_result, "OpenAI codex-mini strips temperature")
assert_eq("top_p" not in codex_result, "OpenAI codex-mini strips top_p")

# =============================================================================
# OpenRouter: inject extra_body.provider.sort = "price"
# =============================================================================

or_ctx = {"model": "deepseek/deepseek-chat", "provider": "deepseek"}

# No existing extra_body -- injects
or_result1 = openrouter_transform_kwargs(or_ctx, {"max_tokens": 100})  # type: ignore[arg-type]
assert_eq(
    or_result1.get("extra_body", {}).get("provider") == {"sort": "price"},
    "OpenRouter injects provider.sort=price when no extra_body",
)
assert_eq(or_result1["max_tokens"] == 100, "OpenRouter keeps other kwargs")

# Existing extra_body without provider -- injects
or_result2 = openrouter_transform_kwargs(or_ctx, {"extra_body": {"custom": "value"}})  # type: ignore[arg-type]
assert_eq(
    or_result2["extra_body"]["provider"] == {"sort": "price"},  # type: ignore[index]
    "OpenRouter injects provider when extra_body exists without it",
)
assert_eq(
    or_result2["extra_body"]["custom"] == "value",  # type: ignore[index]
    "OpenRouter preserves existing extra_body keys",
)

# Existing extra_body with provider -- does NOT override
or_result3 = openrouter_transform_kwargs(or_ctx, {"extra_body": {"provider": {"sort": "latency"}}})  # type: ignore[arg-type]
assert_eq(
    or_result3["extra_body"]["provider"] == {"sort": "latency"},  # type: ignore[index]
    "OpenRouter does NOT override existing provider config",
)

# provider_options.openrouter supplies provider/reasoning before defaults
or_ctx_config = {
    "model": "deepseek/deepseek-chat",
    "provider": "openrouter",
    "provider_options": {"openrouter": {"provider": {"sort": "latency"}, "reasoning": {"enabled": False}}},
}
or_result4 = openrouter_transform_kwargs(or_ctx_config, {"extra_body": {"custom": "value"}})  # type: ignore[arg-type]
assert_eq(
    or_result4["extra_body"]["provider"] == {"sort": "latency"},  # type: ignore[index]
    "OpenRouter uses provider_options.openrouter.provider before default routing",
)
assert_eq(
    or_result4["extra_body"]["reasoning"] == {"enabled": False},  # type: ignore[index]
    "OpenRouter forwards provider_options.openrouter.reasoning",
)
assert_eq(
    or_result4["extra_body"]["custom"] == "value",  # type: ignore[index]
    "OpenRouter keeps extra_body keys when applying provider_options",
)

# =============================================================================
# Gemini: thinking_budget defaults to 0 unless enable_thinking is requested
# =============================================================================

gemini_ctx = {"model": "gemini-2.5-pro", "provider": "google"}

# No thinking_config -- sets default
gem_result1 = gemini_transform_kwargs(gemini_ctx, {"max_tokens": 100})  # type: ignore[arg-type]
assert_eq(
    gem_result1.get("thinking_config") == {"thinking_budget": 0},
    "Gemini defaults thinking_budget to 0",
)

# enable_thinking=True without an explicit budget preserves provider defaults
gem_ctx_enable_thinking = {
    "model": "gemini-2.5-pro",
    "provider": "google",
    "enable_thinking": True,
}
gem_result_enable_thinking = gemini_transform_kwargs(gem_ctx_enable_thinking, {})  # type: ignore[arg-type]
assert_eq(
    "thinking_config" not in gem_result_enable_thinking,
    "Gemini leaves thinking budget unset when enable_thinking=True without explicit budget",
)

# Override from nested provider_options.google.thinking_config.thinking_budget
gem_ctx_budget = {
    "model": "gemini-2.5-pro",
    "provider": "google",
    "provider_options": {"google": {"thinking_config": {"thinking_budget": 4096}}},
}
gem_result2 = gemini_transform_kwargs(gem_ctx_budget, {})  # type: ignore[arg-type]
assert_eq(
    gem_result2.get("thinking_config") == {"thinking_budget": 4096},
    "Gemini uses provider_options.google.thinking_config.thinking_budget override",
)

# Legacy flat provider_options.google.thinking_budget still works
gem_ctx_legacy_budget = {
    "model": "gemini-2.5-pro",
    "provider": "google",
    "provider_options": {"google": {"thinking_budget": 1024}},
}
gem_result_legacy = gemini_transform_kwargs(gem_ctx_legacy_budget, {})  # type: ignore[arg-type]
assert_eq(
    gem_result_legacy.get("thinking_config") == {"thinking_budget": 1024},
    "Gemini still accepts legacy provider_options.google.thinking_budget override",
)

# Already set in kwargs -- preserves it
gem_result3 = gemini_transform_kwargs(gemini_ctx, {"thinking_config": {"thinking_budget": 2048}})  # type: ignore[arg-type]
assert_eq(
    gem_result3["thinking_config"] == {"thinking_budget": 2048},
    "Gemini preserves existing thinking_budget in kwargs",
)

# =============================================================================
# OpenAI: o2 is a reasoning model via ^o\d regex (Python + TS now aligned)
# =============================================================================

# Both Python (model_capabilities.py + provider_kwargs.py) and TypeScript use ^o\d.
# o2, o5, o6 are ALL treated as reasoning models: temperature/top_p stripped.

o2_result = openai_transform_kwargs({"model": "o2", "provider": "openai"}, {"temperature": 0.5, "top_p": 0.8})  # type: ignore[arg-type]
assert_eq(
    "temperature" not in o2_result,
    "Python: o2 IS a reasoning model (^o\\d) — temperature stripped",
)
assert_eq(
    "top_p" not in o2_result,
    "Python: o2 IS a reasoning model — top_p stripped",
)

# Confirm o2-mini follows same treatment
o2_mini_result = openai_transform_kwargs({"model": "o2-mini", "provider": "openai"}, {"temperature": 0.3})  # type: ignore[arg-type]
assert_eq(
    "temperature" not in o2_mini_result,
    "Python: o2-mini IS a reasoning model (^o\\d) — temperature stripped",
)

# =============================================================================
# Gemini: explicit budget=0 via providerOptions overrides enable_thinking=True
# =============================================================================

# Bug: enable_thinking=True with an explicit thinking_budget=0 in providerOptions
# should use the explicit budget (0), not skip injection. Without the explicit
# budget taking priority, the caller has no way to force thinking=off when
# enable_thinking is also set.
gem_ctx_explicit_zero = {
    "model": "gemini-2.5-pro",
    "provider": "google",
    "enable_thinking": True,
    "provider_options": {"google": {"thinking_config": {"thinking_budget": 0}}},
}
gem_result_explicit_zero = gemini_transform_kwargs(gem_ctx_explicit_zero, {})  # type: ignore[arg-type]
assert_eq(
    gem_result_explicit_zero.get("thinking_config") == {"thinking_budget": 0},
    "Gemini: explicit thinking_budget=0 wins over enable_thinking=True",
)

# Confirm baseline: enable_thinking=True WITHOUT providerOptions leaves thinking_config absent
gem_ctx_enable_only = {"model": "gemini-2.5-pro", "provider": "google", "enable_thinking": True}
gem_result_enable_only = gemini_transform_kwargs(gem_ctx_enable_only, {})  # type: ignore[arg-type]
assert_eq(
    "thinking_config" not in gem_result_enable_only,
    "Gemini: enable_thinking=True without explicit budget leaves thinking_config absent",
)

# =============================================================================
# Sno GPU: base URL construction
# =============================================================================

# With gpu_path
gpu_ctx = {
    "model": "qwen3.6-27b-extract",
    "provider": "sno-gpu",
    "base_url": "https://gpu.example.com",
    "provider_options": {"sno-gpu": {"gpu_path": "extract"}},
}
gpu_result1 = sno_gpu_transform_kwargs(gpu_ctx, {})  # type: ignore[arg-type]
assert_eq(
    gpu_result1["base_url"] == "https://gpu.example.com/extract/v1",
    "Sno GPU constructs URL with gpu_path",
)

# Without gpu_path -- fallback to /v1
gpu_ctx_no_path = {
    "model": "qwen3.6-27b-reason",
    "provider": "sno-gpu",
    "base_url": "https://gpu.example.com",
}
gpu_result2 = sno_gpu_transform_kwargs(gpu_ctx_no_path, {})  # type: ignore[arg-type]
assert_eq(
    gpu_result2["base_url"] == "https://gpu.example.com/v1",
    "Sno GPU falls back to /v1 without gpu_path",
)

# Base URL already has /v1 -- strips before reconstructing
gpu_ctx_v1 = {
    "model": "qwen3.6-27b-reason",
    "provider": "sno-gpu",
    "base_url": "https://gpu.example.com/v1",
    "provider_options": {"sno-gpu": {"gpu_path": "reason"}},
}
gpu_result3 = sno_gpu_transform_kwargs(gpu_ctx_v1, {})  # type: ignore[arg-type]
assert_eq(
    gpu_result3["base_url"] == "https://gpu.example.com/reason/v1",
    "Sno GPU strips existing /v1 before reconstructing with gpu_path",
)

# =============================================================================
# Registry has expected entries
# =============================================================================

assert_eq(PROVIDER_KWARGS_REGISTRY["openai"] is openai_transform_kwargs, "Registry has openai")
assert_eq(PROVIDER_KWARGS_REGISTRY["openrouter"] is openrouter_transform_kwargs, "Registry has openrouter -> openrouter")
assert_eq(PROVIDER_KWARGS_REGISTRY["deepseek"] is openrouter_transform_kwargs, "Registry has deepseek -> openrouter")
assert_eq(PROVIDER_KWARGS_REGISTRY["google"] is gemini_transform_kwargs, "Registry has google")
assert_eq(PROVIDER_KWARGS_REGISTRY["sno-gpu"] is sno_gpu_transform_kwargs, "Registry has sno-gpu")

# =============================================================================
# Summary
# =============================================================================

print(f"\n{passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
