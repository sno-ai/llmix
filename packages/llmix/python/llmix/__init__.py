"""
LLMix Python Library

Config-driven LLM configuration utilities for Python.
"""

from typing import TYPE_CHECKING

__version__ = "2.0.7"

# Pricing is always available (no external deps beyond stdlib + json)
from llmix.pricing import MODEL_PRICING, CostBreakdown, ModelPricing, calculate_cost, calculate_rerank_cost, get_model_pricing, normalize_model_name

# Pipeline-era modules are lazy-loaded so that importing llmix never fails when
# optional dependencies (httpx, cachetools, anthropic SDK, etc.) are
# not installed.  Only the module actually used at runtime needs its
# dependencies present.

_LAZY_IMPORTS: dict[str, str] = {
    # pipeline
    "CallPipeline": "llmix.pipeline",
    "PipelineConfig": "llmix.pipeline",
    "CallInput": "llmix.pipeline",
    "CallResponse": "llmix.pipeline",
    "DispatchInput": "llmix.pipeline",
    "DispatchContext": "llmix.pipeline",
    "ProviderResult": "llmix.pipeline",
    "ProviderError": "llmix.pipeline",
    "LLMUsage": "llmix.pipeline",
    # dispatchers
    "openai_dispatch": "llmix.dispatchers",
    "anthropic_dispatch": "llmix.dispatchers",
    "deepinfra_dispatch": "llmix.dispatchers",
    "gemini_dispatch": "llmix.dispatchers",
    "novita_dispatch": "llmix.dispatchers",
    "openrouter_dispatch": "llmix.dispatchers",
    "sno_gpu_dispatch": "llmix.dispatchers",
    "together_dispatch": "llmix.dispatchers",
    # config
    "LLMixPathConfig": "llmix.config",
    "ResolvedConfigDir": "llmix.config",
    "resolve_config_dir": "llmix.config",
    "MdaConfigLoadOptions": "llmix.mda_loader",
    "build_mda_config_file_path": "llmix.mda_loader",
    "load_mda_config": "llmix.mda_loader",
    "load_mda_config_from_file": "llmix.mda_loader",
    "load_mda_config_preset": "llmix.mda_loader",
    "ConfigRegistryOpenOptions": "llmix.config_registry",
    "ConfigRegistryPublishOptions": "llmix.config_registry",
    "ConfigRegistryManager": "llmix.config_registry",
    "ConfigRegistryPublisher": "llmix.config_registry",
    "LLMIX_TRUST_MANIFEST_KIND": "llmix.config_registry",
    "LLMIX_TRUST_MANIFEST_VERSION": "llmix.config_registry",
    "LlmixTrustManifest": "llmix.config_registry",
    "LlmixTrustManifestRegistryRoot": "llmix.config_registry",
    "LlmixTrustManifestReleasePlan": "llmix.config_registry",
    "PublishedRevision": "llmix.config_registry",
    "RegistryRootFreshnessInput": "llmix.config_registry",
    "RegistryRootHighWatermark": "llmix.config_registry",
    "RegistryRootSigner": "llmix.config_registry",
    "RegistryRootSigningInput": "llmix.config_registry",
    "RegistryRootSigningOptions": "llmix.config_registry",
    "RegistryRootVerificationOptions": "llmix.config_registry",
    "load_llmix_trust_manifest": "llmix.config_registry",
    "parse_llmix_trust_manifest": "llmix.config_registry",
    "registry_root_options_from_trust_manifest": "llmix.config_registry",
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
    # telemetry
    "TelemetryPlugin": "llmix.telemetry",
}

if TYPE_CHECKING:
    from llmix.adaptive_semaphore import AdaptiveSemaphore as AdaptiveSemaphore
    from llmix.adaptive_semaphore import parse_openai_ratelimit_headers as parse_openai_ratelimit_headers
    from llmix.config import LLMixPathConfig as LLMixPathConfig
    from llmix.config import ResolvedConfigDir as ResolvedConfigDir
    from llmix.config import resolve_config_dir as resolve_config_dir
    from llmix.config_registry import ConfigRegistryOpenOptions as ConfigRegistryOpenOptions
    from llmix.config_registry import ConfigRegistryPublishOptions as ConfigRegistryPublishOptions
    from llmix.config_registry import ConfigRegistryManager as ConfigRegistryManager
    from llmix.config_registry import ConfigRegistryPublisher as ConfigRegistryPublisher
    from llmix.config_registry import LLMIX_TRUST_MANIFEST_KIND as LLMIX_TRUST_MANIFEST_KIND
    from llmix.config_registry import LLMIX_TRUST_MANIFEST_VERSION as LLMIX_TRUST_MANIFEST_VERSION
    from llmix.config_registry import LlmixTrustManifest as LlmixTrustManifest
    from llmix.config_registry import LlmixTrustManifestRegistryRoot as LlmixTrustManifestRegistryRoot
    from llmix.config_registry import LlmixTrustManifestReleasePlan as LlmixTrustManifestReleasePlan
    from llmix.config_registry import PublishedRevision as PublishedRevision
    from llmix.config_registry import RegistryRootFreshnessInput as RegistryRootFreshnessInput
    from llmix.config_registry import RegistryRootHighWatermark as RegistryRootHighWatermark
    from llmix.config_registry import RegistryRootSigner as RegistryRootSigner
    from llmix.config_registry import RegistryRootSigningInput as RegistryRootSigningInput
    from llmix.config_registry import RegistryRootSigningOptions as RegistryRootSigningOptions
    from llmix.config_registry import RegistryRootVerificationOptions as RegistryRootVerificationOptions
    from llmix.config_registry import load_llmix_trust_manifest as load_llmix_trust_manifest
    from llmix.config_registry import parse_llmix_trust_manifest as parse_llmix_trust_manifest
    from llmix.config_registry import registry_root_options_from_trust_manifest as registry_root_options_from_trust_manifest
    from llmix.mda_loader import MdaConfigLoadOptions as MdaConfigLoadOptions
    from llmix.mda_loader import build_mda_config_file_path as build_mda_config_file_path
    from llmix.mda_loader import load_mda_config as load_mda_config
    from llmix.mda_loader import load_mda_config_from_file as load_mda_config_from_file
    from llmix.mda_loader import load_mda_config_preset as load_mda_config_preset
    from llmix.dispatchers import anthropic_dispatch as anthropic_dispatch
    from llmix.dispatchers import deepinfra_dispatch as deepinfra_dispatch
    from llmix.dispatchers import gemini_dispatch as gemini_dispatch
    from llmix.dispatchers import novita_dispatch as novita_dispatch
    from llmix.dispatchers import openai_dispatch as openai_dispatch
    from llmix.dispatchers import openrouter_dispatch as openrouter_dispatch
    from llmix.dispatchers import sno_gpu_dispatch as sno_gpu_dispatch
    from llmix.dispatchers import together_dispatch as together_dispatch
    from llmix.pipeline import CallInput as CallInput
    from llmix.pipeline import CallPipeline as CallPipeline
    from llmix.pipeline import CallResponse as CallResponse
    from llmix.pipeline import DispatchContext as DispatchContext
    from llmix.pipeline import DispatchInput as DispatchInput
    from llmix.pipeline import LLMUsage as LLMUsage
    from llmix.pipeline import PipelineConfig as PipelineConfig
    from llmix.pipeline import ProviderError as ProviderError
    from llmix.pipeline import ProviderResult as ProviderResult
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
    from llmix.telemetry import TelemetryPlugin as TelemetryPlugin
    from llmix.response_cache import generate_cache_key as generate_cache_key
    from llmix.response_cache import should_skip_cache as should_skip_cache
    from llmix.thinking import strip_thinking as strip_thinking

__all__ = [
    "MODEL_PRICING",
    "CostBreakdown",
    "ModelPricing",
    "calculate_cost",
    "calculate_rerank_cost",
    "get_model_pricing",
    "normalize_model_name",
    "CallPipeline",
    "PipelineConfig",
    "CallInput",
    "CallResponse",
    "DispatchInput",
    "DispatchContext",
    "ProviderResult",
    "ProviderError",
    "LLMUsage",
    "openai_dispatch",
    "anthropic_dispatch",
    "deepinfra_dispatch",
    "gemini_dispatch",
    "novita_dispatch",
    "openrouter_dispatch",
    "sno_gpu_dispatch",
    "together_dispatch",
    "LLMixPathConfig",
    "ResolvedConfigDir",
    "resolve_config_dir",
    "MdaConfigLoadOptions",
    "build_mda_config_file_path",
    "load_mda_config",
    "load_mda_config_from_file",
    "load_mda_config_preset",
    "ConfigRegistryOpenOptions",
    "ConfigRegistryPublishOptions",
    "ConfigRegistryManager",
    "ConfigRegistryPublisher",
    "LLMIX_TRUST_MANIFEST_KIND",
    "LLMIX_TRUST_MANIFEST_VERSION",
    "LlmixTrustManifest",
    "LlmixTrustManifestRegistryRoot",
    "LlmixTrustManifestReleasePlan",
    "PublishedRevision",
    "RegistryRootFreshnessInput",
    "RegistryRootHighWatermark",
    "RegistryRootSigner",
    "RegistryRootSigningInput",
    "RegistryRootSigningOptions",
    "RegistryRootVerificationOptions",
    "load_llmix_trust_manifest",
    "parse_llmix_trust_manifest",
    "registry_root_options_from_trust_manifest",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitState",
    "KillSwitch",
    "KillSwitchActiveError",
    "RetryPolicy",
    "Singleflight",
    "FileLock",
    "is_retryable",
    "AdaptiveSemaphore",
    "parse_openai_ratelimit_headers",
    "KeyPool",
    "KeyPoolExhaustedError",
    "load_keys_from_env",
    "strip_thinking",
    "PROVIDER_KWARGS_REGISTRY",
    "apply_transform_kwargs",
    "get_provider_transport",
    "PROVIDER_TRANSPORT",
    "create_client_for_provider",
    "TwoTierCache",
    "generate_cache_key",
    "should_skip_cache",
    "TelemetryPlugin",
]


def __getattr__(name: str) -> object:
    if name in _LAZY_IMPORTS:
        import importlib

        target_module = _LAZY_IMPORTS[name]
        try:
            mod = importlib.import_module(target_module)
        except ImportError as exc:
            raise ImportError(f"cannot import {name!r} from 'llmix': failed to load {target_module!r} ({exc})") from exc
        try:
            attr = getattr(mod, name)
        except AttributeError as exc:
            raise AttributeError(f"module 'llmix' maps {name!r} to {target_module!r}, but that module has no attribute {name!r}") from exc
        globals()[name] = attr  # cache for subsequent access
        return attr
    raise AttributeError(f"module 'llmix' has no attribute {name!r}")


def __dir__() -> list[str]:
    return list(set(globals()) | set(_LAZY_IMPORTS))
