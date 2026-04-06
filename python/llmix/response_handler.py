"""Response parsing, JSON repair, token counting, and error classification for LLMix.

Extracted from lib/llmix/client.py as part of god class decomposition.
Contains all standalone helper functions used by LLMClient for processing
LLM responses, classifying errors, and managing caching strategies.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any

import json_repair

from llmix.types import BATCH_CAPABLE_MODEL_PATTERNS, CachingConfig, ConfigCapabilities, FallbackTrigger, LLMUsage

logger = logging.getLogger(__name__)

__all__ = ["ParsedPreset", "derive_capabilities", "extract_usage", "is_batch_capable", "parse_preset", "resolve_caching_strategy"]

# =============================================================================
# CONSTANTS
# =============================================================================

_NON_RETRYABLE_ERROR_HINTS: tuple[str, ...] = (
    "api key",
    "auth",
    "unauthorized",
    "forbidden",
    "invalid request",
    "validation",
    "not found",
    "quota exceeded",
)
_JSON_RETRY_MESSAGE = {
    "role": "user",
    "content": "Your previous response was not valid JSON. Return ONLY a raw JSON object or array with no markdown, prose, or extra text.",
}
# Pattern for valid prompt cache keys (alphanumeric, dots, colons, hyphens, underscores)
_VALID_CACHE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")

# Maximum length for error messages in telemetry events
_MAX_TELEMETRY_ERROR_LENGTH = 500

# Patterns that suggest sensitive data in error messages
_SENSITIVE_PATTERNS: tuple[str, ...] = ("sk-", "api_key", "apikey", "secret", "token", "password", "authorization", "bearer")

# JSON parse helpers
_THINK_BLOCK_RE = re.compile(r"^\s*<think>[\s\S]*?</think>\s*")


# =============================================================================
# DATACLASSES
# =============================================================================


@dataclass
class ParsedPreset:
    """Parsed preset result."""

    module: str
    preset: str


@dataclass
class _CallResult:
    """Result from a single LLM call attempt, used internally for fallback logic."""

    response: dict[str, Any]
    trigger: FallbackTrigger | None  # None = success or non-fallbackable error


# =============================================================================
# PRESET PARSING
# =============================================================================


def parse_preset(preset_string: str) -> ParsedPreset:
    """Parse preset string into module and preset.

    Args:
        preset_string: "module:preset" or "preset"

    Returns:
        ParsedPreset with module and preset

    Example:
        >>> parse_preset("hrkg:extraction")
        ParsedPreset(module="hrkg", preset="extraction")
        >>> parse_preset("extraction")
        ParsedPreset(module="_default", preset="extraction")
    """
    colon_index = preset_string.find(":")
    if colon_index == -1:
        return ParsedPreset(module="_default", preset=preset_string)
    return ParsedPreset(module=preset_string[:colon_index], preset=preset_string[colon_index + 1 :])


def is_batch_capable(model: str) -> bool:
    """Check if a model supports OpenAI Batch API."""
    return any(re.match(pattern, model) for pattern in BATCH_CAPABLE_MODEL_PATTERNS)


# =============================================================================
# CACHING & CAPABILITIES
# =============================================================================


def resolve_caching_strategy(config: dict[str, Any], override_bypass_gateway: bool | None = None) -> CachingConfig:
    """Resolve effective caching strategy from config and overrides.

    Priority: override.bypass_gateway (legacy) > config.caching > config.bypass_gateway (legacy) > default
    """
    if override_bypass_gateway is not None:
        logger.warning("[LLMix] DEPRECATED: bypassGateway override used. Use caching.strategy instead.")
        if override_bypass_gateway:
            caching = config.get("caching", {})
            return {"strategy": "native", "key": caching.get("key")}
        return {"strategy": "gateway"}

    if config.get("caching"):
        return config["caching"]

    if "bypass_gateway" in config:
        bypass = config.get("bypass_gateway")
        if bypass is not None:
            config_id = config.get("config_id", "unknown")
            logger.warning("[LLMix] DEPRECATED: bypass_gateway config used in %s. Use caching.strategy instead.", config_id)
            return {"strategy": "native"} if bypass else {"strategy": "gateway"}

    return {"strategy": "gateway"}


def derive_capabilities(config: dict[str, Any], effective_model: str | None = None) -> ConfigCapabilities:
    """Derive capabilities from resolved config."""
    model = effective_model or config.get("model", "")
    provider = config.get("provider", "openai")
    return {
        "provider": provider,
        "is_proprietary": provider in ("openai", "anthropic", "google"),
        "supports_openai_batch": provider == "openai" and is_batch_capable(model),
    }


# =============================================================================
# USAGE EXTRACTION
# =============================================================================


def extract_usage(usage: dict[str, Any] | None) -> LLMUsage:
    """Extract usage from LLM response."""
    if not usage:
        return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    input_tokens = usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", input_tokens + output_tokens)

    cached_input_tokens = None
    if "cached_input_tokens" in usage:
        cached_input_tokens = usage["cached_input_tokens"]
    elif "prompt_tokens_details" in usage:
        details = usage["prompt_tokens_details"]
        if isinstance(details, dict):
            cached_input_tokens = details.get("cached_tokens")

    result: LLMUsage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens}
    if cached_input_tokens is not None:
        result["cached_input_tokens"] = cached_input_tokens

    return result


# =============================================================================
# RESPONSE FORMAT NORMALIZATION
# =============================================================================


def _normalize_openai_text_format(response_format: dict[str, Any]) -> dict[str, Any]:
    """Normalize internal response_format into OpenAI Responses text.format shape."""
    if response_format.get("type") != "json_schema":
        return response_format

    wrapped_schema = response_format.get("json_schema")
    if isinstance(wrapped_schema, dict):
        schema_payload = wrapped_schema
    else:
        schema_payload = response_format

    raw_schema = schema_payload.get("schema")
    schema: dict[str, Any] = raw_schema if isinstance(raw_schema, dict) else {}

    raw_name = schema_payload.get("name") or schema.get("title") or "structured_response"
    normalized_name = re.sub(r"[^A-Za-z0-9_-]", "_", str(raw_name))[:64] or "structured_response"

    raw_strict = schema_payload.get("strict")
    strict = raw_strict if isinstance(raw_strict, bool) else True

    normalized: dict[str, Any] = {"type": "json_schema", "name": normalized_name, "schema": schema, "strict": strict}
    description = schema_payload.get("description")
    if isinstance(description, str) and description:
        normalized["description"] = description

    return normalized


# =============================================================================
# ERROR CLASSIFICATION
# =============================================================================


def _is_retryable_error_message(error_message: str | None) -> bool:
    """Classify retryability from raw provider/error text."""
    if not error_message:
        return True
    lowered = error_message.lower()
    return not any(hint in lowered for hint in _NON_RETRYABLE_ERROR_HINTS)


def _classify_fallback_trigger(error: Exception | None, error_message: str | None, is_timeout: bool) -> FallbackTrigger | None:
    """Classify an error into a fallback trigger category.

    Returns None if the error should NOT trigger fallback (e.g. 4xx client errors).
    """
    if is_timeout:
        return "timeout"

    if error_message and not _is_retryable_error_message(error_message):
        return None

    error_type_name = type(error).__name__ if error else ""
    if error_type_name in ("APIConnectionError", "ConnectError", "ConnectionError"):
        return "connection_error"
    if error_type_name in ("APITimeoutError",):
        return "timeout"
    if error_type_name in ("InternalServerError",):
        return "5xx"

    msg = (error_message or "").lower()
    if any(hint in msg for hint in ("connect", "connection", "unreachable", "dns", "refused")):
        return "connection_error"
    if any(hint in msg for hint in ("500", "502", "503", "504", "internal server error", "bad gateway", "service unavailable")):
        return "5xx"
    if any(hint in msg for hint in ("timeout", "timed out")):
        return "timeout"

    return "5xx"


def _sanitize_error_for_telemetry(error_message: str | None) -> str:
    """Sanitize error message for telemetry to prevent leaking sensitive data."""
    if not error_message:
        return ""
    sanitized = error_message[:_MAX_TELEMETRY_ERROR_LENGTH]
    if len(error_message) > _MAX_TELEMETRY_ERROR_LENGTH:
        sanitized += "...[truncated]"
    lowered = sanitized.lower()
    for pattern in _SENSITIVE_PATTERNS:
        if pattern in lowered:
            return "[REDACTED - contains sensitive data]"
    return sanitized


# =============================================================================
# JSON PARSE HELPERS
# =============================================================================


def _strip_think_blocks(content: str) -> str:
    """Strip leading <think> blocks from Qwen3 non-thinking mode residual output."""
    return _THINK_BLOCK_RE.sub("", content)


def _try_parse_json(content: str) -> dict[str, Any] | list[Any]:
    """Parse JSON from LLM content using json_repair. Rejects scalars.

    Raises ValueError if content cannot be parsed to dict/list.
    """
    stripped = _strip_think_blocks(content)
    parsed = json_repair.loads(stripped)
    if isinstance(parsed, (dict, list)):
        return parsed
    raise ValueError(f"Expected dict/list from JSON parse, got {type(parsed).__name__}")


def _merge_usage(target: dict[str, Any], source: dict[str, Any]) -> None:
    """Merge token usage from a retry response into the primary response."""
    target_usage = target.get("usage", {})
    source_usage = source.get("usage", {})
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        target_usage[key] = target_usage.get(key, 0) + source_usage.get(key, 0)
    target["usage"] = target_usage
