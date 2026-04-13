#![forbid(unsafe_code)]
#![cfg_attr(docsrs, feature(doc_cfg))]
#![doc = include_str!("../README.md")]

pub mod adaptive_semaphore;
pub mod canonical_json;
pub mod config;
pub mod dispatch;
pub mod error;
#[cfg(any(
    feature = "helpers-openai",
    feature = "helpers-snogpu",
    feature = "helpers-anthropic",
    feature = "helpers-gemini"
))]
#[cfg_attr(
    docsrs,
    doc(cfg(any(
        feature = "helpers-openai",
        feature = "helpers-snogpu",
        feature = "helpers-anthropic",
        feature = "helpers-gemini"
    )))
)]
pub mod helpers;
pub mod key_pool;
pub mod pipeline;
pub mod provider_kwargs;
pub mod resilience;
pub mod response_cache;
pub mod thinking;
pub mod types;

pub use adaptive_semaphore::{parse_openai_ratelimit_headers, AdaptiveSemaphore, RateLimitHeaders};
pub use config::{
    load_config, load_config_preset, load_config_preset_with_version, resolve_config_dir,
    validate_module, validate_preset, validate_version, ConfigDirSource, LlmixPathConfig,
    ResolvedConfigDir,
};
pub use dispatch::DispatchFn;
pub use error::{
    AdaptiveSemaphoreClosedError, CircuitOpenError, ConfigAccessError, ConfigNotFoundError,
    InvalidConfigError, KeyPoolExhaustedError, KillSwitchActiveError, LlmixError, LlmixResult,
    ProviderError, SecurityError,
};
#[cfg(feature = "helpers-anthropic")]
#[cfg_attr(docsrs, doc(cfg(feature = "helpers-anthropic")))]
pub use helpers::anthropic::AnthropicChatHelper;
#[cfg(feature = "helpers-gemini")]
#[cfg_attr(docsrs, doc(cfg(feature = "helpers-gemini")))]
pub use helpers::gemini::GeminiChatHelper;
#[cfg(feature = "helpers-openai")]
#[cfg_attr(docsrs, doc(cfg(feature = "helpers-openai")))]
pub use helpers::openai::OpenAiChatHelper;
#[cfg(feature = "helpers-snogpu")]
#[cfg_attr(docsrs, doc(cfg(feature = "helpers-snogpu")))]
pub use helpers::snogpu::SnoGpuChatHelper;
pub use key_pool::{load_keys_from_env, KeyPool};
pub use pipeline::{CallPipeline, PipelineConfig};
pub use provider_kwargs::{
    apply_transform_kwargs, gemini_transform_kwargs, is_reasoning_model, openai_transform_kwargs,
    openrouter_transform_kwargs, provider_kwargs_callback, sno_gpu_transform_kwargs,
    TransformKwargsCallback, TransformKwargsContext, PROVIDER_KWARGS_REGISTRY,
};
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
pub use thinking::{strip_thinking, StripThinkingResult};
pub use types::{
    CacheHitTier, CachingStrategy, CallInput, CallResponse, DispatchContext, LlmUsage,
    ProviderResult, ResponseCacheStats, ResponseCacheStrategy,
};
