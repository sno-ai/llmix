#!/usr/bin/env python3
"""
Test suite for model capability detection.

Mirrors packages/llmix/typescript/tests/model-capabilities.test.ts.

Run with: uv run python -m pytest tests/test_model_capabilities.py
"""

import sys
import warnings
from pathlib import Path

# Add python/ to path and import directly (avoids __init__.py cascading imports)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.model_capabilities import (  # noqa: E402
    adjust_temperature_for_model,
    filter_openai_provider_options,
    get_model_capabilities,
)
from llmix.pricing import get_model_pricing  # noqa: E402

warnings.filterwarnings("ignore", module="pricing")

# A gateway-addressed id must classify exactly like its bare form.
# Regression: "openai/gpt-5.6-luna" classified as standard, so reasoning_effort was
# silently dropped before the request and the effort setting did nothing.
PREFIX_EQUIVALENCE: list[tuple[str, str]] = [
    ("openai/gpt-5.6-luna", "gpt-5.6-luna"),
    ("openai/gpt-5-mini", "gpt-5-mini"),
    ("openai/o3-mini", "o3-mini"),
    ("z-ai/glm-5.2", "glm-5.2"),
    ("deepseek/deepseek-v4-flash-0731", "deepseek-v4-flash-0731"),
]

KEEPS_EFFORT: list[str] = [
    "gpt-5.6-luna",
    "openai/gpt-5.6-luna",
    "glm-5.2",
    "z-ai/glm-5.2",
    "o3-mini",
    "openai/o3-mini",
]

DROPS_EFFORT: list[str] = [
    "deepseek/deepseek-v4-flash-0731",
    "deepseek-v4-flash-0731",
    "gpt-4o",
    "openai/gpt-4o",
    "claude-4.5-haiku",
]

PRICED_THROUGH_GATEWAY: list[str] = [
    "openai/gpt-5.6-luna",
    "z-ai/glm-5.2",
    "deepseek/deepseek-v4-flash-0731",
    "models/gemini-3-flash-preview",
]


def test_vendor_prefix_does_not_change_classification() -> None:
    for prefixed, bare in PREFIX_EQUIVALENCE:
        assert get_model_capabilities(prefixed) == get_model_capabilities(bare), prefixed


def test_reasoning_models_keep_effort() -> None:
    for model_id in KEEPS_EFFORT:
        result = filter_openai_provider_options(model_id, {"reasoning_effort": "xhigh"})
        options = result["filtered_options"]
        assert options is not None, model_id
        assert options.get("reasoning_effort") == "xhigh", model_id


def test_non_reasoning_models_still_drop_effort() -> None:
    for model_id in DROPS_EFFORT:
        result = filter_openai_provider_options(model_id, {"reasoning_effort": "xhigh"})
        options = result["filtered_options"]
        assert options is None or "reasoning_effort" not in options, model_id
        assert result["filtered_params"].get("reasoning_effort") == "xhigh", model_id


def test_fixed_temperature_applies_to_openai_families_only() -> None:
    # Being a reasoning model and being forbidden to send a temperature are
    # different facts. Only the OpenAI families carry the restriction.
    assert get_model_capabilities("gpt-5.6-luna")["fixed_temperature"] is True
    assert get_model_capabilities("o3-mini")["fixed_temperature"] is True
    assert get_model_capabilities("glm-5.2")["is_reasoning_model"] is True
    assert get_model_capabilities("glm-5.2")["fixed_temperature"] is False
    assert adjust_temperature_for_model("z-ai/glm-5.2", 0.3)["adjusted_temperature"] == 0.3
    assert adjust_temperature_for_model("openai/gpt-5.6-luna", 0.3)["adjusted_temperature"] == 1.0


def test_pricing_resolves_through_a_gateway_prefix() -> None:
    # Regression: enumerating individual vendor prefixes lost pricing for every
    # vendor that was not on the list.
    for model_id in PRICED_THROUGH_GATEWAY:
        assert get_model_pricing(model_id) is not None, model_id
    assert get_model_pricing("vendor/nonexistent-model-xyz") is None
