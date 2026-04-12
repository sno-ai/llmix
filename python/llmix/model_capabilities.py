r"""
Model Capabilities - Provider-specific parameter filtering

Different model families support different parameters. This module provides
capability detection and parameter filtering to prevent API errors when
sending unsupported parameters.

Logic mirrors @ai-sdk/openai's internal implementation:
- Reasoning: Models matching o\d (o1, o3, o4...), "gpt-5*", "codex-", "computer-use"
- Standard: Everything else (gpt-4, gpt-4o, gpt-4.1, claude, gemini, etc.)

Parameter Support:
- reasoningEffort: Only reasoning models (AI SDK validates this client-side)
- textVerbosity: Only GPT-5 series (OpenAI API rejects for other models)
- temperature: Fixed at 1 for reasoning models

Ported from package/llmix/src/model-capabilities.ts
"""

import re
from typing import Literal, TypedDict, cast

from llmix.types import OpenAIProviderOptions

ModelClass = Literal["gpt5", "o-series", "codex", "standard"]


class ModelCapabilities(TypedDict):
    """Model capability flags."""

    is_reasoning_model: bool
    """Is this a reasoning model (o-series, gpt-5, codex)"""

    supports_text_verbosity: bool
    """Supports textVerbosity parameter (GPT-5 only)"""

    fixed_temperature: bool
    """Temperature is fixed at 1 (reasoning models)"""

    model_class: ModelClass
    """Model class for logging"""


class FilteredParams(TypedDict, total=False):
    """Parameters that were filtered out (for logging)."""

    reasoning_effort: str
    text_verbosity: str
    temperature: float


def _is_reasoning_model(model_id: str) -> bool:
    """Check if model is a reasoning model.

    Reasoning models: o{digit}* (o1, o3, o4...), gpt-5*, codex-*, computer-use-*
    All gpt-5 variants are reasoning models — none support temperature.
    # TEMP: regex patch — migrate to config-driven model capabilities (see model-capabilities.json)
    """
    lower = model_id.lower()
    return bool(re.match(r"^o\d", lower)) or lower.startswith(("gpt-5", "codex-", "computer-use"))


def _supports_text_verbosity(model_id: str) -> bool:
    """Check if model supports textVerbosity.

    Currently only GPT-5 series supports textVerbosity.
    o-series and other reasoning models do NOT support it.
    """
    lower = model_id.lower()
    return lower.startswith("gpt-5")


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """Detect model capabilities based on model ID.

    Uses same logic as AI SDK's internal implementation.

    Args:
        model_id: The model identifier string.

    Returns:
        ModelCapabilities with detected flags.
    """
    lower = model_id.lower()
    reasoning = _is_reasoning_model(model_id)

    # Determine model class for logging
    model_class: ModelClass = "standard"
    if lower.startswith("gpt-5"):
        model_class = "gpt5"
    # TEMP: regex patch — migrate to config-driven model capabilities (see model-capabilities.json)
    elif re.match(r"^o\d", lower):
        model_class = "o-series"
    elif lower.startswith(("codex-", "computer-use")):
        model_class = "codex"

    return ModelCapabilities(
        is_reasoning_model=reasoning, supports_text_verbosity=_supports_text_verbosity(model_id), fixed_temperature=reasoning, model_class=model_class
    )


class FilterResult(TypedDict):
    """Result of filter_openai_provider_options."""

    filtered_options: OpenAIProviderOptions | None
    filtered_params: FilteredParams
    capabilities: ModelCapabilities


def filter_openai_provider_options(model_id: str, options: OpenAIProviderOptions | None) -> FilterResult:
    """Filter OpenAI provider options based on model capabilities.

    Strips unsupported parameters to prevent API errors.
    Returns both filtered options and what was removed (for logging).

    Note: AI SDK already validates reasoningEffort client-side for non-reasoning
    models. We still filter here as a safety net and to provide consistent warnings.

    Args:
        model_id: The model identifier string.
        options: OpenAI provider options to filter, or None.

    Returns:
        Dict with filtered_options, filtered_params, and capabilities.
    """
    capabilities = get_model_capabilities(model_id)

    if options is None:
        return FilterResult(filtered_options=None, filtered_params=FilteredParams(), capabilities=capabilities)

    filtered_params: FilteredParams = {}
    filtered_options = cast("OpenAIProviderOptions", dict(options))

    # Filter reasoningEffort for non-reasoning models
    if not capabilities["is_reasoning_model"] and "reasoning_effort" in filtered_options:
        filtered_params["reasoning_effort"] = filtered_options.pop("reasoning_effort")

    # Filter textVerbosity for models that don't support it
    if not capabilities["supports_text_verbosity"] and "text_verbosity" in filtered_options:
        filtered_params["text_verbosity"] = filtered_options.pop("text_verbosity")

    return FilterResult(filtered_options=filtered_options if filtered_options else None, filtered_params=filtered_params, capabilities=capabilities)


class TemperatureResult(TypedDict, total=False):
    """Result of adjust_temperature_for_model."""

    adjusted_temperature: float | None
    was_adjusted: bool
    original_temperature: float


def adjust_temperature_for_model(model_id: str, temperature: float | None) -> TemperatureResult:
    """Check if temperature needs adjustment for reasoning models.

    Reasoning models (o-series, GPT-5) require temperature=1.
    Returns the adjusted temperature and whether it was changed.

    Args:
        model_id: The model identifier string.
        temperature: The requested temperature, or None.

    Returns:
        Dict with adjusted_temperature, was_adjusted, and optionally original_temperature.
    """
    capabilities = get_model_capabilities(model_id)

    # If model has fixed temperature and user specified non-1 temperature
    if capabilities["fixed_temperature"] and temperature is not None and temperature != 1:
        return TemperatureResult(adjusted_temperature=1.0, was_adjusted=True, original_temperature=temperature)

    return TemperatureResult(adjusted_temperature=temperature, was_adjusted=False)
