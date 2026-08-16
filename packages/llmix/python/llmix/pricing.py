"""
LLM Model Pricing Module

AUTO-GENERATED from Helicone API (https://api.helicone.ai)
Last synced: 2026-01-29

Pricing: USD per 1M tokens (input/output)
- For rerankers: input = cost per 1M tokens processed, output = 0
- For embeddings: input = cost per 1M tokens, output = 0

Note: Date suffixes are stripped automatically in lookups.
e.g., "gpt-5-mini" -> "gpt-5-mini"
"""

import json
import re
import warnings
from importlib import resources
from math import isfinite
from typing import TypedDict

from llmix.model_id import strip_vendor_prefix


class ModelPricing(TypedDict):
    """Pricing for a model in USD per 1M tokens."""

    input: float
    output: float


class CostBreakdown(TypedDict):
    """Cost breakdown for an LLM call."""

    input_cost_usd: float
    output_cost_usd: float
    total_cost_usd: float


def _load_pricing_data() -> dict[str, ModelPricing]:
    """Load pricing data from packaged JSON data."""
    json_path = resources.files("llmix").joinpath("pricing.json")
    data = json.loads(json_path.read_text(encoding="utf-8"))

    # Filter out _meta key
    return {k: v for k, v in data.items() if not k.startswith("_")}


# Load pricing data at module import time
MODEL_PRICING: dict[str, ModelPricing] = _load_pricing_data()


def normalize_model_name(name: str) -> str:
    """
    Normalize model name for lookup.

    Transformations:
    - Lowercase
    - Strip 'models/' prefix
    - Strip 'qwen/qwen' -> 'qwen' prefix
    - Strip 'deepseek/' provider prefix
    - Strip date suffixes: -YYYY-MM-DD, -YYYYMMDD, -YYMM
    - Handle Anthropic naming: claude-haiku-4-5 -> claude-4.5-haiku

    Args:
        name: Raw model name (e.g., "claude-haiku-4-5-20251001")

    Returns:
        Normalized model name (e.g., "claude-4.5-haiku")
    """
    normalized = name.lower()

    # Remove any gateway vendor prefix: models/, qwen/, deepseek/, openai/, z-ai/ ...
    # Enumerating individual vendors silently lost pricing for every vendor not listed.
    normalized = strip_vendor_prefix(normalized)

    # Strip date suffixes:
    # -2025-08-07 (OpenAI YYYY-MM-DD)
    # -20251001 (Anthropic YYYYMMDD)
    # -2411 (Mistral YYMM)
    normalized = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", normalized)  # YYYY-MM-DD
    normalized = re.sub(r"-\d{8}$", "", normalized)  # YYYYMMDD
    normalized = re.sub(r"-\d{4}$", "", normalized)  # YYMM

    # Anthropic naming normalization: claude-haiku-4-5 -> claude-4.5-haiku
    # claude-sonnet-4-5 -> claude-4.5-sonnet
    anthropic_match = re.match(r"^claude-(haiku|sonnet|opus)-(\d+)-(\d+)$", normalized)
    if anthropic_match:
        tier, major, minor = anthropic_match.groups()
        normalized = f"claude-{major}.{minor}-{tier}"

    return normalized


def get_model_pricing(model_name: str) -> ModelPricing | None:
    """
    Get pricing for a specific model.

    Handles various input formats:
    - Exact match: "gpt-5-mini"
    - With date: "gpt-5-mini" -> "gpt-5-mini"
    - Anthropic: "claude-haiku-4-5-20251001" -> "claude-4.5-haiku"
    - With prefix: "models/gemini-2.5-flash" -> "gemini-2.5-flash"

    Args:
        model_name: Model identifier (date suffixes stripped automatically)

    Returns:
        {"input": float, "output": float} in USD per 1M tokens, or None if not found
    """
    # Try exact match first
    if model_name in MODEL_PRICING:
        return MODEL_PRICING[model_name]

    # Try normalized match
    normalized = normalize_model_name(model_name)
    if normalized in MODEL_PRICING:
        return MODEL_PRICING[normalized]

    # Try lowercase only (for case mismatches without date suffix)
    lowercase = model_name.lower()
    if lowercase in MODEL_PRICING:
        return MODEL_PRICING[lowercase]

    warnings.warn(f"[llmix/pricing] No pricing data for model: {model_name}", stacklevel=2)
    return None


def calculate_cost(model_name: str, input_tokens: int, output_tokens: int = 0) -> CostBreakdown:
    """
    Calculate costs for an LLM/embedding/reranker call.

    Args:
        model_name: Model identifier (date suffixes stripped automatically)
        input_tokens: Number of input tokens (prompt, documents, etc.)
        output_tokens: Number of output tokens (completion, 0 for embeddings/rerankers)

    Returns:
        Cost breakdown with input_cost_usd, output_cost_usd, total_cost_usd

    Raises:
        ValueError: If input_tokens or output_tokens are negative or non-finite
    """
    # Validate inputs to prevent NaN/negative costs corrupting billing
    if not isfinite(input_tokens) or not isfinite(output_tokens):
        msg = "input_tokens/output_tokens must be finite numbers"
        raise ValueError(msg)
    if input_tokens < 0 or output_tokens < 0:
        msg = "input_tokens/output_tokens must be >= 0"
        raise ValueError(msg)

    pricing = get_model_pricing(model_name)

    if pricing is None:
        return {"input_cost_usd": 0.0, "output_cost_usd": 0.0, "total_cost_usd": 0.0}

    input_cost_usd = (input_tokens / 1_000_000) * pricing["input"]
    output_cost_usd = (output_tokens / 1_000_000) * pricing["output"]
    total_cost_usd = input_cost_usd + output_cost_usd

    return {"input_cost_usd": round(input_cost_usd, 6), "output_cost_usd": round(output_cost_usd, 6), "total_cost_usd": round(total_cost_usd, 6)}


def calculate_rerank_cost(model_name: str, search_count: int = 1) -> float:
    """
    Calculate reranking cost (backwards compatibility).

    DEPRECATED: Use calculate_cost() instead.

    Args:
        model_name: Reranker model identifier
        search_count: Number of search operations

    Returns:
        Total cost in USD
    """
    # Rough estimate: 1 search ~ 1000 tokens
    estimated_tokens = search_count * 1000
    return calculate_cost(model_name, estimated_tokens, 0)["total_cost_usd"]
