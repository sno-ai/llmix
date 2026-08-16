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

import json
import re
from importlib import resources
from typing import Literal, TypedDict, cast

from llmix.model_id import strip_vendor_prefix
from llmix.types import OpenAIProviderOptions

ModelClass = Literal["gpt5", "o-series", "codex", "standard"]


def _load_capability_rules() -> dict[str, object]:
    """Load classification rules from packaged JSON data.

    The same file ships in the TypeScript package at ``data/model-capabilities.json``;
    the two copies are kept byte-identical the way the two ``pricing.json`` files are.
    """
    json_path = resources.files("llmix").joinpath("model-capabilities.json")
    return cast("dict[str, object]", json.loads(json_path.read_text(encoding="utf-8")))


_RULES = _load_capability_rules()


def _compile(key: str) -> tuple[re.Pattern[str], ...]:
    patterns = cast("list[str]", _RULES[key])
    return tuple(re.compile(pattern) for pattern in patterns)


_REASONING_PATTERNS = _compile("reasoningModelPrefixes")
_TEXT_VERBOSITY_PATTERNS = _compile("textVerbosityPrefixes")
# Being a reasoning model and being forbidden to send a temperature are different
# facts. The temperature restriction belongs to the OpenAI families; a reasoning
# model from another vendor keeps its temperature.
_FIXED_TEMPERATURE_PATTERNS = _compile("fixedTemperaturePrefixes")
_MODEL_CLASS_RULES: tuple[tuple[re.Pattern[str], ModelClass], ...] = tuple(
    (re.compile(rule["prefix"]), cast("ModelClass", rule["class"]))
    for rule in cast("list[dict[str, str]]", _RULES["modelClassRules"])
)
_DEFAULT_MODEL_CLASS: ModelClass = cast("ModelClass", _RULES["defaultModelClass"])


def _matches_any(patterns: tuple[re.Pattern[str], ...], normalized_id: str) -> bool:
    return any(pattern.search(normalized_id) for pattern in patterns)


def _classify(normalized_id: str) -> ModelClass:
    for pattern, model_class in _MODEL_CLASS_RULES:
        if pattern.search(normalized_id):
            return model_class
    return _DEFAULT_MODEL_CLASS


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


def get_model_capabilities(model_id: str) -> ModelCapabilities:
    """Detect model capabilities based on model ID.

    Rules come from the packaged model-capabilities.json, which the TypeScript
    package consumes too. The id is normalized first so a gateway-addressed
    model (``openai/gpt-5.6-luna``) classifies identically to its bare form.

    Args:
        model_id: The model identifier string.

    Returns:
        ModelCapabilities with detected flags.
    """
    normalized = strip_vendor_prefix(model_id)

    model_class = _classify(normalized)

    return ModelCapabilities(
        is_reasoning_model=_matches_any(_REASONING_PATTERNS, normalized),
        supports_text_verbosity=_matches_any(_TEXT_VERBOSITY_PATTERNS, normalized),
        fixed_temperature=_matches_any(_FIXED_TEMPERATURE_PATTERNS, normalized),
        model_class=model_class,
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
