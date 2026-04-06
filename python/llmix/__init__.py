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


def __getattr__(name: str):  # noqa: ANN001
    """Lazy-load v2 modules to avoid import failures when deps are absent."""
    _v2_modules = {
        # client_v2
        "V2CallPipeline": "llmix.client_v2",
        "V2PipelineConfig": "llmix.client_v2",
        "V2CallInput": "llmix.client_v2",
        "V2CallResponse": "llmix.client_v2",
        "DispatchInput": "llmix.client_v2",
        "ProviderResult": "llmix.client_v2",
        "ProviderError": "llmix.client_v2",
        "LLMUsage": "llmix.client_v2",
        # resilience
        "CircuitBreaker": "llmix.resilience",
        "CircuitOpenError": "llmix.resilience",
        "CircuitState": "llmix.resilience",
        "KillSwitch": "llmix.resilience",
        "KillSwitchActiveError": "llmix.resilience",
        "RetryPolicy": "llmix.resilience",
        "Singleflight": "llmix.resilience",
        "FileLock": "llmix.resilience",
        # adaptive_semaphore
        "AdaptiveSemaphore": "llmix.adaptive_semaphore",
        # key_pool
        "KeyPool": "llmix.key_pool",
        "KeyPoolExhaustedError": "llmix.key_pool",
        "load_keys_from_env": "llmix.key_pool",
        # thinking
        "strip_thinking": "llmix.thinking",
        # provider_kwargs
        "PROVIDER_KWARGS_REGISTRY": "llmix.provider_kwargs",
        "apply_transform_kwargs": "llmix.provider_kwargs",
        # http2
        "get_provider_transport": "llmix.http2",
        "PROVIDER_TRANSPORT": "llmix.http2",
        "create_client_for_provider": "llmix.http2",
    }
    if name in _v2_modules:
        import importlib
        mod = importlib.import_module(_v2_modules[name])
        return getattr(mod, name)
    raise AttributeError(f"module 'llmix' has no attribute {name!r}")
