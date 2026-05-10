#![forbid(unsafe_code)]
#![cfg_attr(docsrs, feature(doc_cfg))]
#![doc = include_str!("../README.md")]

pub mod adaptive_semaphore;
pub mod canonical_json;
pub mod config;
pub mod config_registry;
pub mod dispatch;
pub mod error;
#[cfg(any(
    feature = "providers-openai",
    feature = "providers-sno-gpu",
    feature = "providers-anthropic",
    feature = "providers-gemini"
))]
#[cfg_attr(
    docsrs,
    doc(cfg(any(
        feature = "providers-openai",
        feature = "providers-sno-gpu",
        feature = "providers-anthropic",
        feature = "providers-gemini"
    )))
)]
pub mod providers;
#[cfg(any(
    feature = "providers-openai",
    feature = "providers-sno-gpu",
    feature = "providers-anthropic",
    feature = "providers-gemini"
))]
#[doc(hidden)]
pub use providers as helpers;
pub mod key_pool;
pub mod pipeline;
pub mod provider_kwargs;
pub mod resilience;
pub mod response_cache;
pub mod thinking;
pub mod types;

pub use adaptive_semaphore::{parse_openai_ratelimit_headers, AdaptiveSemaphore, RateLimitHeaders};
pub use config::{
    load_config, load_config_preset, load_config_preset_with_options, load_config_with_options,
    resolve_config_dir, validate_module, validate_preset, validate_version, ConfigDirSource,
    LlmixPathConfig, MdaConfigLoadOptions, ResolvedConfigDir,
};
pub use config_registry::{ConfigRegistryManager, ConfigRegistryPublisher, PublishedRevision};
pub use dispatch::DispatchFn;
pub use error::{
    AdaptiveSemaphoreClosedError, CircuitOpenError, ConfigAccessError, ConfigNotFoundError,
    InvalidConfigError, KeyPoolExhaustedError, KillSwitchActiveError, LlmixError, LlmixResult,
    ProviderError, SecurityError,
};
pub use key_pool::{load_keys_from_env, KeyPool};
pub use pipeline::{CallPipeline, PipelineConfig};
pub use provider_kwargs::{
    apply_transform_kwargs, gemini_transform_kwargs, is_reasoning_model, openai_transform_kwargs,
    openrouter_transform_kwargs, provider_kwargs_callback, sno_gpu_transform_kwargs,
    TransformKwargsCallback, TransformKwargsContext, PROVIDER_KWARGS_REGISTRY,
};
#[cfg(feature = "providers-anthropic")]
#[cfg_attr(docsrs, doc(cfg(feature = "providers-anthropic")))]
pub use providers::anthropic::AnthropicChatHelper;
#[cfg(feature = "providers-gemini")]
#[cfg_attr(docsrs, doc(cfg(feature = "providers-gemini")))]
pub use providers::gemini::GeminiChatHelper;
#[cfg(feature = "providers-openai")]
#[cfg_attr(docsrs, doc(cfg(feature = "providers-openai")))]
pub use providers::openai::OpenAiChatHelper;
#[cfg(feature = "providers-sno-gpu")]
#[cfg_attr(docsrs, doc(cfg(feature = "providers-sno-gpu")))]
pub use providers::sno_gpu::SnoGpuChatHelper;
pub use resilience::{
    calculate_delay, is_retryable, parse_retry_after, resolve_state_dir, CircuitBreaker,
    CircuitState, FileLock, KillSwitch, RetryPolicy, RetryPolicyOptions, SharedCallResult,
    Singleflight,
};
pub use response_cache::{
    generate_cache_key, is_response_cache_strategy, resolve_response_cache_strategy,
    should_skip_cache, CacheKeyParams, CacheResult, TwoTierCache, TwoTierCacheConfig,
    CACHE_KEY_PREFIX,
};
pub use snoai_mda_config::{
    DidWebVerifier, RekorClient, RekorPolicy, SigstoreVerifier, TrustPolicy, TrustedSigner,
};
pub use thinking::{strip_thinking, StripThinkingResult};
pub use types::{
    CacheHitTier, CachingStrategy, CallInput, CallResponse, DispatchContext, LlmUsage,
    ProviderResult, ResponseCacheStats, ResponseCacheStrategy,
};
