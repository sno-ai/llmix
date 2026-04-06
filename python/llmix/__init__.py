"""
LLMix Python Library

Config-driven LLM configuration utilities for Python.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

__version__ = "2.0.0"

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

# v2 modules are lazy-loaded so that importing llmix never fails when
# optional dependencies (httpx, cachetools, anthropic SDK, etc.) are
# not installed.  Only the module actually used at runtime needs its
# dependencies present.

_LAZY_IMPORTS: dict[str, str] = {
    # client_v2
    "V2CallPipeline": "llmix.client_v2",
    "V2PipelineConfig": "llmix.client_v2",
    "V2CallInput": "llmix.client_v2",
    "V2CallResponse": "llmix.client_v2",
    "DispatchInput": "llmix.client_v2",
    "DispatchContext": "llmix.client_v2",
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
    "is_retryable": "llmix.resilience",
    # adaptive_semaphore
    "AdaptiveSemaphore": "llmix.adaptive_semaphore",
    "parse_openai_ratelimit_headers": "llmix.adaptive_semaphore",
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
    # response_cache
    "TwoTierCache": "llmix.response_cache",
    "generate_cache_key": "llmix.response_cache",
    "should_skip_cache": "llmix.response_cache",
}

if TYPE_CHECKING:
    from llmix.adaptive_semaphore import AdaptiveSemaphore as AdaptiveSemaphore
    from llmix.adaptive_semaphore import parse_openai_ratelimit_headers as parse_openai_ratelimit_headers
    from llmix.client_v2 import DispatchContext as DispatchContext
    from llmix.client_v2 import DispatchInput as DispatchInput
    from llmix.client_v2 import LLMUsage as LLMUsage
    from llmix.client_v2 import ProviderError as ProviderError
    from llmix.client_v2 import ProviderResult as ProviderResult
    from llmix.client_v2 import V2CallInput as V2CallInput
    from llmix.client_v2 import V2CallPipeline as V2CallPipeline
    from llmix.client_v2 import V2CallResponse as V2CallResponse
    from llmix.client_v2 import V2PipelineConfig as V2PipelineConfig
    from llmix.http2 import PROVIDER_TRANSPORT as PROVIDER_TRANSPORT
    from llmix.http2 import create_client_for_provider as create_client_for_provider
    from llmix.http2 import get_provider_transport as get_provider_transport
    from llmix.key_pool import KeyPool as KeyPool
    from llmix.key_pool import KeyPoolExhaustedError as KeyPoolExhaustedError
    from llmix.key_pool import load_keys_from_env as load_keys_from_env
    from llmix.provider_kwargs import PROVIDER_KWARGS_REGISTRY as PROVIDER_KWARGS_REGISTRY
    from llmix.provider_kwargs import apply_transform_kwargs as apply_transform_kwargs
    from llmix.resilience import CircuitBreaker as CircuitBreaker
    from llmix.resilience import CircuitOpenError as CircuitOpenError
    from llmix.resilience import CircuitState as CircuitState
    from llmix.resilience import FileLock as FileLock
    from llmix.resilience import KillSwitch as KillSwitch
    from llmix.resilience import KillSwitchActiveError as KillSwitchActiveError
    from llmix.resilience import RetryPolicy as RetryPolicy
    from llmix.resilience import Singleflight as Singleflight
    from llmix.resilience import is_retryable as is_retryable
    from llmix.response_cache import TwoTierCache as TwoTierCache
    from llmix.response_cache import generate_cache_key as generate_cache_key
    from llmix.response_cache import should_skip_cache as should_skip_cache
    from llmix.thinking import strip_thinking as strip_thinking

__all__ = [
    # pricing (eager)
    "MODEL_PRICING",
    "CostBreakdown",
    "ModelPricing",
    "calculate_cost",
    "calculate_rerank_cost",
    "get_model_pricing",
    "normalize_model_name",
    # v2 (lazy)
    *_LAZY_IMPORTS,
]


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        import importlib

        target_module = _LAZY_IMPORTS[name]
        try:
            mod = importlib.import_module(target_module)
        except ImportError as exc:
            raise ImportError(
                f"cannot import {name!r} from 'llmix': "
                f"failed to load {target_module!r} ({exc})"
            ) from exc
        try:
            attr = getattr(mod, name)
        except AttributeError as exc:
            raise AttributeError(
                f"module 'llmix' maps {name!r} to {target_module!r}, "
                f"but that module has no attribute {name!r}"
            ) from exc
        globals()[name] = attr  # cache for subsequent access
        return attr
    raise AttributeError(f"module 'llmix' has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(set(globals()) | set(_LAZY_IMPORTS))
