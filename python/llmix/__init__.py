"""
LLMix Python Library

Config-driven LLM configuration utilities for Python.
"""

__version__ = "1.0.0"

# Pricing is always available (no external deps beyond stdlib + json)
from llmix.pricing import (
    MODEL_PRICING,
    CostBreakdown,
    ModelPricing,
    calculate_cost,
    calculate_rerank_cost,
    get_model_pricing,
    normalize_model_name,
)

# Everything else is lazy-loaded to avoid import failures when
# sno-cortex dependencies (lib.infra) are not available.
# These will be progressively replaced during v2 migration.

__all__ = [
    "MODEL_PRICING",
    "CostBreakdown",
    "ModelPricing",
    "calculate_cost",
    "calculate_rerank_cost",
    "get_model_pricing",
    "normalize_model_name",
]
