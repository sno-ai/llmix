"""Runtime validation for projected LLMix configs."""

from __future__ import annotations

from math import isfinite
from pathlib import Path
from typing import Any, cast

from llmix.types import InvalidConfigError


_VALID_PROVIDERS = {
    "openai",
    "anthropic",
    "google",
    "deepseek",
    "openrouter",
    "deepinfra",
    "together",
    "novita",
    "sno-gpu",
}
_VALID_CACHE_STRATEGIES = {
    "native",
    "gateway",
    "disabled",
    "redis",
    "redis-or-memory",
    "memory",
}
_ANTHROPIC_MIN_BUDGET_TOKENS = 1024


def _require_object(value: Any, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidConfigError(f"{path} must be an object")
    return cast(dict[str, Any], value)


def _require_bool(value: Any, path: str) -> None:
    if not isinstance(value, bool):
        raise InvalidConfigError(f"{path} must be a boolean")


def _require_int(value: Any, path: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidConfigError(f"{path} must be an integer")
    return value


def _require_positive_int(value: Any, path: str) -> None:
    if _require_int(value, path) <= 0:
        raise InvalidConfigError(f"{path} must be a positive integer")


def _require_non_negative_int(value: Any, path: str) -> None:
    if _require_int(value, path) < 0:
        raise InvalidConfigError(f"{path} must be a non-negative integer")


def _require_finite_number(value: Any, path: str) -> float:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(float(value))
    ):
        raise InvalidConfigError(f"{path} must be a finite number")
    return float(value)


def _require_number_range(
    value: Any, path: str, minimum: float, maximum: float
) -> None:
    numeric_value = _require_finite_number(value, path)
    if numeric_value < minimum or numeric_value > maximum:
        raise InvalidConfigError(f"{path} must be between {minimum:g} and {maximum:g}")


def _require_positive_number(value: Any, path: str) -> None:
    if _require_finite_number(value, path) <= 0:
        raise InvalidConfigError(f"{path} must be a positive number")


def _require_literal(value: Any, path: str, allowed: set[str]) -> None:
    if not isinstance(value, str) or value not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise InvalidConfigError(f"{path} must be one of: {allowed_text}")


def _validate_common(common: dict[str, Any], path: str) -> None:
    if "max_output_tokens" in common:
        _require_positive_int(common["max_output_tokens"], f"{path}.max_output_tokens")
    if "top_k" in common:
        _require_positive_int(common["top_k"], f"{path}.top_k")
    if "temperature" in common:
        _require_number_range(common["temperature"], f"{path}.temperature", 0, 2)
    if "top_p" in common:
        _require_number_range(common["top_p"], f"{path}.top_p", 0, 1)
    if "max_retries" in common:
        _require_non_negative_int(common["max_retries"], f"{path}.max_retries")


def _validate_caching(caching: dict[str, Any], path: str) -> None:
    _require_literal(
        caching.get("strategy"), f"{path}.strategy", _VALID_CACHE_STRATEGIES
    )
    if "ttl" in caching:
        _require_positive_int(caching["ttl"], f"{path}.ttl")
    if "max_items" in caching:
        _require_positive_int(caching["max_items"], f"{path}.max_items")


def _validate_timeout(timeout: dict[str, Any], path: str) -> None:
    if "total_time" in timeout:
        _require_positive_number(timeout["total_time"], f"{path}.total_time")
    if "stream_first_chunk_time" in timeout:
        _require_positive_number(
            timeout["stream_first_chunk_time"], f"{path}.stream_first_chunk_time"
        )


def _validate_provider_options(
    provider: str, provider_options: dict[str, Any], path: str
) -> None:
    if "openai" in provider_options:
        openai = _require_object(provider_options["openai"], f"{path}.openai")
        if "reasoning_effort" in openai:
            _require_literal(
                openai["reasoning_effort"],
                f"{path}.openai.reasoning_effort",
                {"minimal", "low", "medium", "high", "xhigh"},
            )
        if "max_completion_tokens" in openai:
            _require_positive_int(
                openai["max_completion_tokens"], f"{path}.openai.max_completion_tokens"
            )
        if "service_tier" in openai:
            _require_literal(
                openai["service_tier"],
                f"{path}.openai.service_tier",
                {"auto", "flex", "priority", "default"},
            )
        if "text_verbosity" in openai:
            _require_literal(
                openai["text_verbosity"],
                f"{path}.openai.text_verbosity",
                {"low", "medium", "high"},
            )

    if "anthropic" in provider_options:
        anthropic = _require_object(provider_options["anthropic"], f"{path}.anthropic")
        thinking = anthropic.get("thinking")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.anthropic.thinking")
            if "type" in thinking_obj:
                _require_literal(
                    thinking_obj["type"],
                    f"{path}.anthropic.thinking.type",
                    {"enabled", "disabled"},
                )
            if "budget_tokens" in thinking_obj:
                _require_positive_int(
                    thinking_obj["budget_tokens"],
                    f"{path}.anthropic.thinking.budget_tokens",
                )
                if (
                    provider == "anthropic"
                    and thinking_obj.get("type") == "enabled"
                    and thinking_obj["budget_tokens"] < _ANTHROPIC_MIN_BUDGET_TOKENS
                ):
                    raise InvalidConfigError(
                        f"{path}.anthropic.thinking.budget_tokens must be >= {_ANTHROPIC_MIN_BUDGET_TOKENS} when Anthropic thinking is enabled"
                    )

    if "google" in provider_options:
        google = _require_object(provider_options["google"], f"{path}.google")
        thinking = google.get("thinking_config")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.google.thinking_config")
            if "thinking_level" in thinking_obj:
                _require_literal(
                    thinking_obj["thinking_level"],
                    f"{path}.google.thinking_config.thinking_level",
                    {"low", "high"},
                )
            if "thinking_budget" in thinking_obj:
                _require_positive_int(
                    thinking_obj["thinking_budget"],
                    f"{path}.google.thinking_config.thinking_budget",
                )

    if "deepseek" in provider_options:
        deepseek = _require_object(provider_options["deepseek"], f"{path}.deepseek")
        thinking = deepseek.get("thinking")
        if thinking is not None:
            thinking_obj = _require_object(thinking, f"{path}.deepseek.thinking")
            if "type" in thinking_obj:
                _require_literal(
                    thinking_obj["type"],
                    f"{path}.deepseek.thinking.type",
                    {"enabled", "disabled"},
                )

    for provider_name in ("deepinfra", "novita", "sno-gpu"):
        if provider_name not in provider_options:
            continue
        provider_value = _require_object(
            provider_options[provider_name], f"{path}.{provider_name}"
        )
        if "enable_thinking" in provider_value:
            _require_bool(
                provider_value["enable_thinking"],
                f"{path}.{provider_name}.enable_thinking",
            )
        if "thinking_budget" in provider_value:
            _require_positive_int(
                provider_value["thinking_budget"],
                f"{path}.{provider_name}.thinking_budget",
            )


def _validate_runtime_config(value: dict[str, Any], source: str | Path) -> None:
    provider = value.get("provider")
    if not isinstance(provider, str) or provider not in _VALID_PROVIDERS:
        raise InvalidConfigError(f"Invalid or missing provider in {source}")
    model = value.get("model")
    if not isinstance(model, str) or not model:
        raise InvalidConfigError(f"Invalid or missing model in {source}")

    common = value.get("common")
    if common is not None:
        _validate_common(_require_object(common, "common"), "common")
    caching = value.get("caching")
    if caching is not None:
        _validate_caching(_require_object(caching, "caching"), "caching")
    timeout = value.get("timeout")
    if timeout is not None:
        _validate_timeout(_require_object(timeout, "timeout"), "timeout")
    provider_options = value.get("provider_options")
    if provider_options is not None:
        _validate_provider_options(
            provider,
            _require_object(provider_options, "provider_options"),
            "provider_options",
        )
