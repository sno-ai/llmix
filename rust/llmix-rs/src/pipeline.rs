use crate::adaptive_semaphore::{parse_openai_ratelimit_headers, AdaptiveSemaphore};
use crate::dispatch::DispatchFn;
use crate::error::{LlmixError, LlmixResult};
use crate::key_pool::KeyPool;
use crate::provider_kwargs::{
    apply_transform_kwargs, TransformKwargsCallback, TransformKwargsContext,
    PROVIDER_KWARGS_REGISTRY,
};
use crate::resilience::{
    is_retryable, CircuitBreaker, CircuitState, FileLock, KillSwitch, RetryPolicy,
    RetryPolicyOptions, Singleflight,
};
use crate::response_cache::{generate_cache_key, should_skip_cache, CacheKeyParams, TwoTierCache};
use crate::thinking::{strip_thinking, StripThinkingResult};
use crate::types::{
    CachingStrategy, CallInput, CallResponse, DispatchContext, LlmUsage, ProviderResult,
};
use serde_json::{Map, Value};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

const DEFAULT_MAX_RETRIES: u32 = 3;
const DEFAULT_RETRY_BASE_MS: u64 = 1_000;
const DEFAULT_RETRY_MAX_DELAY_MS: u64 = 30_000;
const DEFAULT_RETRY_JITTER_MS: u64 = 1_000;
const DEFAULT_RETRY_MAX_RETRY_AFTER_MS: u64 = 60_000;
const DEFAULT_CIRCUIT_BREAKER_THRESHOLD: u32 = 3;
const DEFAULT_CIRCUIT_BREAKER_COOLDOWN: Duration = Duration::from_secs(30);
const DEFAULT_SEMAPHORE_INITIAL: usize = 32;
const DEFAULT_SEMAPHORE_MIN: usize = 4;

#[derive(Clone)]
pub struct PipelineConfig {
    pub dispatch: Arc<dyn DispatchFn>,
    pub max_retries: u32,
    pub retry_base_ms: u64,
    pub retry_max_delay_ms: u64,
    pub retry_jitter_ms: u64,
    pub retry_max_retry_after_ms: u64,
    pub circuit_breaker_threshold: u32,
    pub circuit_breaker_cooldown: Duration,
    pub semaphore_initial: usize,
    pub semaphore_min: usize,
    pub kill_switch_state_dir: Option<PathBuf>,
    pub transform_kwargs_overrides: HashMap<String, TransformKwargsCallback>,
    pub response_cache: Option<Arc<TwoTierCache>>,
    /// When true (default), `CallPipeline::close()` also closes
    /// `response_cache`. Set to false when sharing one `TwoTierCache` across
    /// multiple pipelines so the first `close()` does not tear down Redis for
    /// the others.
    pub close_response_cache: bool,
}

impl PipelineConfig {
    pub fn new<D>(dispatch: D) -> Self
    where
        D: DispatchFn + 'static,
    {
        Self {
            dispatch: Arc::new(dispatch),
            max_retries: DEFAULT_MAX_RETRIES,
            retry_base_ms: DEFAULT_RETRY_BASE_MS,
            retry_max_delay_ms: DEFAULT_RETRY_MAX_DELAY_MS,
            retry_jitter_ms: DEFAULT_RETRY_JITTER_MS,
            retry_max_retry_after_ms: DEFAULT_RETRY_MAX_RETRY_AFTER_MS,
            circuit_breaker_threshold: DEFAULT_CIRCUIT_BREAKER_THRESHOLD,
            circuit_breaker_cooldown: DEFAULT_CIRCUIT_BREAKER_COOLDOWN,
            semaphore_initial: DEFAULT_SEMAPHORE_INITIAL,
            semaphore_min: DEFAULT_SEMAPHORE_MIN,
            kill_switch_state_dir: None,
            transform_kwargs_overrides: HashMap::new(),
            response_cache: None,
            close_response_cache: true,
        }
    }
}

pub struct CallPipeline {
    dispatch: Arc<dyn DispatchFn>,
    kill_switch: KillSwitch,
    singleflight: Singleflight<ProviderResult, String>,
    retry_policy: RetryPolicy,
    file_lock: FileLock,
    circuit_breakers: Mutex<HashMap<String, Arc<CircuitBreaker>>>,
    semaphores: Mutex<HashMap<String, Arc<AdaptiveSemaphore>>>,
    key_pools: Mutex<HashMap<String, Arc<KeyPool>>>,
    transform_kwargs: HashMap<String, TransformKwargsCallback>,
    response_cache: Option<Arc<TwoTierCache>>,
    close_response_cache: bool,
    circuit_breaker_threshold: u32,
    circuit_breaker_cooldown: Duration,
    semaphore_initial: usize,
    semaphore_min: usize,
}

impl CallPipeline {
    pub fn new(config: PipelineConfig) -> LlmixResult<Self> {
        let retry_policy = RetryPolicy::new(RetryPolicyOptions {
            max_retries: config.max_retries,
            base_ms: config.retry_base_ms,
            max_delay_ms: config.retry_max_delay_ms,
            jitter_ms: config.retry_jitter_ms,
            max_retry_after_ms: config.retry_max_retry_after_ms,
        })?;

        let kill_switch = match config.kill_switch_state_dir {
            Some(path) => KillSwitch::with_state_dir(path)?,
            None => KillSwitch::new()?,
        };

        let mut transform_kwargs = PROVIDER_KWARGS_REGISTRY
            .iter()
            .map(|(provider, callback)| ((*provider).to_owned(), *callback))
            .collect::<HashMap<_, _>>();
        transform_kwargs.extend(config.transform_kwargs_overrides);

        Ok(Self {
            dispatch: config.dispatch,
            kill_switch,
            singleflight: Singleflight::new(),
            retry_policy,
            file_lock: FileLock::new()?,
            circuit_breakers: Mutex::new(HashMap::new()),
            semaphores: Mutex::new(HashMap::new()),
            key_pools: Mutex::new(HashMap::new()),
            transform_kwargs,
            response_cache: config.response_cache,
            close_response_cache: config.close_response_cache,
            circuit_breaker_threshold: config.circuit_breaker_threshold,
            circuit_breaker_cooldown: config.circuit_breaker_cooldown,
            semaphore_initial: config.semaphore_initial,
            semaphore_min: config.semaphore_min,
        })
    }

    pub fn set_key_pool(&self, provider: impl Into<String>, pool: KeyPool) {
        self.key_pools
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .insert(provider.into(), Arc::new(pool));
    }

    pub async fn close(&self) {
        let semaphores = self
            .semaphores
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .values()
            .cloned()
            .collect::<Vec<_>>();

        for semaphore in semaphores {
            semaphore.close();
        }

        if self.close_response_cache {
            if let Some(cache) = self.response_cache.as_ref() {
                cache.close().await;
            }
        }
    }

    pub async fn call(&self, input: CallInput) -> CallResponse {
        let provider = self
            .provider_name(&input.config)
            .unwrap_or_else(|| "unknown".to_owned());
        let model = self
            .model_name(&input.config)
            .unwrap_or_else(|| "unknown".to_owned());

        match self.call_inner(&input, &provider, &model).await {
            Ok(response) => response,
            Err(error) => CallResponse {
                content: String::new(),
                model,
                provider,
                usage: LlmUsage::default(),
                success: false,
                error: Some(error),
                thinking_content: None,
                cache_hit: None,
                tool_calls: None,
            },
        }
    }

    pub fn get_circuit_breaker_state(
        &self,
        provider: &str,
        base_url: impl AsRef<str>,
    ) -> Option<CircuitState> {
        let key = format!("{provider}:{}", base_url.as_ref());
        self.circuit_breakers
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(&key)
            .map(|breaker| breaker.state())
    }

    pub fn get_semaphore_window(&self, provider: &str) -> Option<usize> {
        self.semaphores
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(provider)
            .map(|semaphore| semaphore.window())
    }

    pub fn singleflight_count(&self) -> usize {
        self.singleflight.in_flight_count()
    }

    async fn call_inner(
        &self,
        input: &CallInput,
        provider: &str,
        model: &str,
    ) -> Result<CallResponse, String> {
        let effective_base_url = self
            .resolve_effective_base_url(&input.config, &input.messages)
            .map_err(|error| error.to_string())?;

        self.kill_switch
            .check()
            .map_err(|error| error.to_string())?;

        let cache_params = self
            .build_cache_key_params(
                &input.config,
                &input.messages,
                provider,
                model,
                &effective_base_url,
            )
            .map_err(|error| error.to_string())?;
        let request_key = generate_cache_key(&cache_params).map_err(|error| error.to_string())?;
        let cache_key = self
            .response_cache
            .as_ref()
            .filter(|_| !should_skip_cache(self.caching_strategy(&input.config)))
            .map(|_| request_key.clone());

        if let (Some(cache), Some(key)) = (self.response_cache.as_ref(), cache_key.as_ref()) {
            if let Some(hit) = cache.get(key).await {
                let StripThinkingResult {
                    content,
                    thinking_content,
                } = self.apply_thinking_strip(&hit.value, &input.config);
                return Ok(CallResponse {
                    content,
                    model: model.to_owned(),
                    provider: provider.to_owned(),
                    usage: LlmUsage::default(),
                    success: true,
                    error: None,
                    thinking_content,
                    cache_hit: Some(hit.tier),
                    tool_calls: None,
                });
            }
        }

        let circuit_breaker = self.get_circuit_breaker(provider, &effective_base_url);
        let config = input.config.clone();
        let messages = input.messages.clone();
        let singleflight_key = input
            .singleflight_key
            .clone()
            .unwrap_or_else(|| request_key.clone());
        let provider_result = self
            .singleflight
            .do_call(singleflight_key, || async {
                self.execute_with_circuit_breaker(circuit_breaker.as_ref(), &config, &messages)
                    .await
                    .map_err(|error| error.to_string())
            })
            .await
            .map_err(|error| (*error).clone())?;

        let StripThinkingResult {
            content,
            thinking_content,
        } = self.apply_thinking_strip(&provider_result.content, &input.config);

        // Skip cache write when the response contains tool_calls: CachedValue
        // stores only the text body, so a future hit would silently drop the
        // function-call structure. See GH issue #6.
        let has_tool_calls = provider_result
            .tool_calls
            .as_ref()
            .is_some_and(|calls| !calls.is_empty());
        if let (Some(cache), Some(key)) = (self.response_cache.as_ref(), cache_key.as_ref()) {
            if !has_tool_calls {
                cache.set(key, &provider_result.content).await;
            }
        }

        Ok(CallResponse {
            content,
            model: provider_result.model.clone(),
            provider: provider.to_owned(),
            usage: provider_result.usage.clone(),
            success: true,
            error: None,
            thinking_content,
            cache_hit: None,
            tool_calls: provider_result.tool_calls.clone(),
        })
    }

    async fn execute_with_circuit_breaker(
        &self,
        circuit_breaker: &CircuitBreaker,
        config: &Value,
        messages: &[Value],
    ) -> LlmixResult<ProviderResult> {
        let state = circuit_breaker.state();
        circuit_breaker.check()?;
        let probe_admitted = matches!(state, CircuitState::HalfOpen);

        match self
            .retry_policy
            .execute_with_hooks(
                || self.execute_retry_body(config, messages),
                Some(|error: &LlmixError| self.is_retryable_error(error)),
                Some(|error: &LlmixError| self.retry_after_header(error)),
            )
            .await
        {
            Ok(result) => {
                circuit_breaker.on_success();
                Ok(result)
            }
            Err(error) => {
                if self.is_local_error(&error) {
                    if probe_admitted {
                        circuit_breaker.cancel_probe();
                    }
                } else if !matches!(error, LlmixError::CircuitOpen(_)) {
                    let status_code = self.status_code(&error);
                    circuit_breaker.on_failure(status_code, status_code.is_none());
                }
                Err(error)
            }
        }
    }

    async fn execute_retry_body(
        &self,
        config: &Value,
        messages: &[Value],
    ) -> LlmixResult<ProviderResult> {
        let provider = self
            .provider_name(config)
            .unwrap_or_else(|| "unknown".to_owned());
        let model = self
            .model_name(config)
            .unwrap_or_else(|| "unknown".to_owned());
        let semaphore = self.get_semaphore(&provider);

        let _permit = semaphore.acquire_guard().await?;

        let mut api_key = None::<String>;
        let result = async {
            let (selected_key, kwargs) = self.with_file_lock(|| {
                let pool = self
                    .get_key_pool(&provider)
                    .ok_or_else(|| self.no_api_key_pool_error(&provider))?;
                let selected_key = pool.select()?;
                let kwargs = self.build_request_kwargs(config, messages)?;
                Ok((selected_key, kwargs))
            })?;

            api_key = Some(selected_key.clone());

            let result = self
                .dispatch
                .dispatch(DispatchContext {
                    provider: provider.clone(),
                    model: model.clone(),
                    api_key: selected_key,
                    messages: messages.to_vec(),
                    kwargs,
                    config: config.clone(),
                })
                .await?;

            if let Some(headers) = result.headers.as_ref() {
                let normalized_headers = normalize_headers(headers);
                if let Some(headers) = parse_openai_ratelimit_headers(&normalized_headers) {
                    semaphore.on_header_feedback(headers.remaining, headers.limit);
                } else {
                    semaphore.on_success();
                }
            } else {
                semaphore.on_success();
            }

            Ok(result)
        }
        .await;

        let outcome = match result {
            Ok(value) => Ok(value),
            Err(error) => {
                let status_code = self.status_code(&error);
                if status_code == Some(429) {
                    semaphore.on_rate_limit();
                }

                if let Some(selected_key) = api_key.as_ref() {
                    match status_code {
                        Some(429) => {
                            let _ = self.with_file_lock(|| {
                                let pool = self
                                    .get_key_pool(&provider)
                                    .ok_or_else(|| self.no_api_key_pool_error(&provider))?;
                                pool.rotate();
                                Ok(())
                            });
                        }
                        Some(401 | 403) => {
                            self.with_file_lock(|| {
                                let pool = self
                                    .get_key_pool(&provider)
                                    .ok_or_else(|| self.no_api_key_pool_error(&provider))?;
                                pool.mark_dead(selected_key)?;
                                Ok(())
                            })?;
                        }
                        _ => {}
                    }
                }

                Err(error)
            }
        };
        outcome
    }

    fn build_request_kwargs(
        &self,
        config: &Value,
        messages: &[Value],
    ) -> LlmixResult<Map<String, Value>> {
        let provider = self
            .provider_name(config)
            .unwrap_or_else(|| "unknown".to_owned());
        let model = self.model_name(config).unwrap_or_default();
        let common = common_object(config);
        let mut kwargs = Map::new();

        insert_cloned(
            &mut kwargs,
            "temperature",
            common.and_then(|map| clone_alias(map, &["temperature"])),
        );
        insert_cloned(
            &mut kwargs,
            "top_p",
            common.and_then(|map| clone_alias(map, &["top_p", "topP"])),
        );
        insert_cloned(
            &mut kwargs,
            "max_tokens",
            common.and_then(|map| clone_alias(map, &["max_output_tokens", "maxOutputTokens"])),
        );
        insert_cloned(
            &mut kwargs,
            "top_k",
            common.and_then(|map| clone_alias(map, &["top_k", "topK"])),
        );
        insert_cloned(
            &mut kwargs,
            "presence_penalty",
            common.and_then(|map| clone_alias(map, &["presence_penalty", "presencePenalty"])),
        );
        insert_cloned(
            &mut kwargs,
            "frequency_penalty",
            common.and_then(|map| clone_alias(map, &["frequency_penalty", "frequencyPenalty"])),
        );
        insert_cloned(
            &mut kwargs,
            "stop",
            common.and_then(|map| clone_alias(map, &["stop", "stop_sequences", "stopSequences"])),
        );
        insert_cloned(
            &mut kwargs,
            "seed",
            common.and_then(|map| clone_alias(map, &["seed"])),
        );
        insert_cloned(
            &mut kwargs,
            "response_format",
            self.response_format_value(config, common),
        );

        let ctx = TransformKwargsContext {
            model,
            provider: provider.clone(),
            messages: Some(messages.to_vec()),
            temperature: common.and_then(|map| f64_alias(map, &["temperature"])),
            top_p: common.and_then(|map| f64_alias(map, &["top_p", "topP"])),
            enable_thinking: common
                .and_then(|map| bool_alias(map, &["enable_thinking", "enableThinking"])),
            provider_options: self.provider_options_map(config),
            base_url: self.base_url_value(config),
        };

        apply_transform_kwargs(&ctx, kwargs, self.transform_kwargs.get(&provider).copied())
    }

    fn resolve_effective_base_url(
        &self,
        config: &Value,
        messages: &[Value],
    ) -> LlmixResult<String> {
        let kwargs = self.build_request_kwargs(config, messages)?;
        if let Some(base_url) = string_alias(&kwargs, &["base_url", "baseUrl"]) {
            let trimmed = base_url.trim();
            if !trimmed.is_empty() {
                return Ok(trimmed.to_owned());
            }
        }

        Ok(self.base_url_value(config).unwrap_or_default())
    }

    fn build_cache_key_params(
        &self,
        config: &Value,
        messages: &[Value],
        provider: &str,
        model: &str,
        effective_base_url: &str,
    ) -> LlmixResult<CacheKeyParams> {
        let common = common_object(config);

        Ok(CacheKeyParams {
            provider: provider.to_owned(),
            model: model.to_owned(),
            messages: messages.to_vec(),
            base_url: (!effective_base_url.trim().is_empty())
                .then(|| effective_base_url.to_owned()),
            enable_thinking: common
                .and_then(|map| bool_alias(map, &["enable_thinking", "enableThinking"])),
            temperature: common.and_then(|map| f64_alias(map, &["temperature"])),
            max_output_tokens: common
                .and_then(|map| u64_alias(map, &["max_output_tokens", "maxOutputTokens"])),
            response_format: self.response_format_value(config, common),
            provider_options: self.provider_options_value(config),
            seed: common.and_then(|map| i64_alias(map, &["seed"])),
            top_p: common.and_then(|map| f64_alias(map, &["top_p", "topP"])),
            top_k: common.and_then(|map| i64_alias(map, &["top_k", "topK"])),
            presence_penalty: common
                .and_then(|map| f64_alias(map, &["presence_penalty", "presencePenalty"])),
            frequency_penalty: common
                .and_then(|map| f64_alias(map, &["frequency_penalty", "frequencyPenalty"])),
            stop_sequences: common.and_then(|map| {
                string_array_alias(map, &["stop_sequences", "stopSequences", "stop"])
            }),
        })
    }

    fn apply_thinking_strip(&self, content: &str, config: &Value) -> StripThinkingResult {
        if common_object(config)
            .and_then(|common| bool_alias(common, &["keep_thinking_output", "keepThinkingOutput"]))
            .unwrap_or(false)
        {
            return StripThinkingResult {
                content: content.to_owned(),
                thinking_content: None,
            };
        }

        strip_thinking(content)
    }

    fn is_local_error(&self, error: &LlmixError) -> bool {
        matches!(
            error,
            LlmixError::KeyPoolExhausted(_)
                | LlmixError::InvalidProviderKwargsConfig(_)
                | LlmixError::AdaptiveSemaphoreClosed(_)
                | LlmixError::InvalidKeyPoolConfig(_)
                | LlmixError::InvalidFileLockConfig(_)
                | LlmixError::InvalidAdaptiveSemaphoreConfig(_)
                | LlmixError::InvalidRetryPolicyConfig(_)
                | LlmixError::InvalidResponseCacheConfig(_)
                | LlmixError::UnknownKeyPoolKey(_)
                | LlmixError::CanonicalJson(_)
                | LlmixError::ConfigNotFound(_)
                | LlmixError::ConfigAccess(_)
                | LlmixError::InvalidConfig(_)
                | LlmixError::Security(_)
        )
    }

    fn is_retryable_error(&self, error: &LlmixError) -> bool {
        if matches!(error, LlmixError::CircuitOpen(_)) || self.is_local_error(error) {
            return false;
        }

        if let Some(status_code) = self.status_code(error) {
            return is_retryable(status_code);
        }

        true
    }

    fn retry_after_header(&self, error: &LlmixError) -> Option<String> {
        let LlmixError::Provider(provider_error) = error else {
            return None;
        };

        provider_error.headers.as_ref().and_then(|headers| {
            headers.iter().find_map(|(key, value)| {
                key.eq_ignore_ascii_case("retry-after")
                    .then(|| value.clone())
            })
        })
    }

    fn status_code(&self, error: &LlmixError) -> Option<u16> {
        match error {
            LlmixError::Provider(provider_error) => provider_error.status_code,
            _ => None,
        }
    }

    fn get_circuit_breaker(&self, provider: &str, base_url: &str) -> Arc<CircuitBreaker> {
        let key = format!("{provider}:{base_url}");
        let mut circuit_breakers = self
            .circuit_breakers
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        circuit_breakers
            .entry(key)
            .or_insert_with(|| {
                Arc::new(CircuitBreaker::with_options(
                    provider.to_owned(),
                    base_url.to_owned(),
                    self.circuit_breaker_threshold,
                    self.circuit_breaker_cooldown,
                    10,
                ))
            })
            .clone()
    }

    fn get_semaphore(&self, provider: &str) -> Arc<AdaptiveSemaphore> {
        let mut semaphores = self
            .semaphores
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        semaphores
            .entry(provider.to_owned())
            .or_insert_with(|| {
                Arc::new(
                    AdaptiveSemaphore::new(self.semaphore_initial, self.semaphore_min)
                        .expect("pipeline semaphore configuration must be valid"),
                )
            })
            .clone()
    }

    fn get_key_pool(&self, provider: &str) -> Option<Arc<KeyPool>> {
        self.key_pools
            .lock()
            .unwrap_or_else(|e| e.into_inner())
            .get(provider)
            .cloned()
    }

    fn with_file_lock<T, F>(&self, func: F) -> LlmixResult<T>
    where
        F: FnOnce() -> LlmixResult<T>,
    {
        let guard = self.file_lock.acquire_guard()?;
        let result = func();
        let release_result = guard.release();

        match (result, release_result) {
            (Ok(value), Ok(())) => Ok(value),
            (Err(error), Ok(())) => Err(error),
            (Ok(_), Err(error)) => Err(error),
            (Err(error), Err(_)) => Err(error),
        }
    }

    fn provider_name(&self, config: &Value) -> Option<String> {
        let config = config.as_object()?;
        string_alias(config, &["provider"])
            .map(ToOwned::to_owned)
            .or_else(|| {
                common_object_value(config)
                    .and_then(|common| string_alias(common, &["provider"]).map(ToOwned::to_owned))
            })
    }

    fn model_name(&self, config: &Value) -> Option<String> {
        let config = config.as_object()?;
        string_alias(config, &["model"])
            .map(ToOwned::to_owned)
            .or_else(|| {
                common_object_value(config)
                    .and_then(|common| string_alias(common, &["model"]).map(ToOwned::to_owned))
            })
    }

    fn base_url_value(&self, config: &Value) -> Option<String> {
        config
            .as_object()
            .and_then(|map| string_alias(map, &["baseUrl", "base_url"]).map(ToOwned::to_owned))
            .filter(|value| !value.trim().is_empty())
    }

    fn provider_options_value(&self, config: &Value) -> Option<Value> {
        config
            .as_object()
            .and_then(|map| clone_alias(map, &["provider_options", "providerOptions"]))
            .filter(|value| !value.is_null())
    }

    fn provider_options_map(&self, config: &Value) -> Option<Map<String, Value>> {
        self.provider_options_value(config)
            .and_then(|value| value.as_object().cloned())
    }

    fn response_format_value(
        &self,
        config: &Value,
        common: Option<&Map<String, Value>>,
    ) -> Option<Value> {
        config
            .as_object()
            .and_then(|map| clone_alias(map, &["responseFormat", "response_format"]))
            .or_else(|| {
                common.and_then(|map| clone_alias(map, &["response_format", "responseFormat"]))
            })
            .filter(|value| !value.is_null())
    }

    fn caching_strategy(&self, config: &Value) -> CachingStrategy {
        let Some(strategy) = config
            .as_object()
            .and_then(|map| map.get("caching"))
            .and_then(Value::as_object)
            .and_then(|caching| string_alias(caching, &["strategy"]))
        else {
            return CachingStrategy::Disabled;
        };

        match strategy
            .trim()
            .to_ascii_lowercase()
            .replace('_', "-")
            .as_str()
        {
            "native" => CachingStrategy::Native,
            "gateway" => CachingStrategy::Gateway,
            "disabled" => CachingStrategy::Disabled,
            "redis" => CachingStrategy::Redis,
            "redis-or-memory" => CachingStrategy::RedisOrMemory,
            "memory" => CachingStrategy::Memory,
            _ => CachingStrategy::Disabled,
        }
    }

    fn no_api_key_pool_error(&self, provider: &str) -> LlmixError {
        let upper = provider.to_ascii_uppercase();
        LlmixError::InvalidKeyPoolConfig(format!(
            "No API key pool for provider \"{provider}\". Set {upper}_API_KEY or {upper}_KEYS."
        ))
    }
}

fn normalize_headers(headers: &HashMap<String, String>) -> HashMap<String, String> {
    headers
        .iter()
        .map(|(key, value)| (key.to_ascii_lowercase(), value.clone()))
        .collect()
}

fn common_object(config: &Value) -> Option<&Map<String, Value>> {
    config.as_object().and_then(common_object_value)
}

fn common_object_value(config: &Map<String, Value>) -> Option<&Map<String, Value>> {
    config.get("common").and_then(Value::as_object)
}

fn insert_cloned(map: &mut Map<String, Value>, key: &str, value: Option<Value>) {
    if let Some(value) = value {
        map.insert(key.to_owned(), value);
    }
}

fn clone_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<Value> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .cloned()
        .filter(|value| !value.is_null())
}

fn string_alias<'a>(map: &'a Map<String, Value>, aliases: &[&str]) -> Option<&'a str> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(Value::as_str)
}

fn bool_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<bool> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(Value::as_bool)
}

fn f64_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<f64> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(Value::as_f64)
}

fn u64_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<u64> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(|value| {
            value.as_u64().or_else(|| {
                value
                    .as_i64()
                    .filter(|candidate| *candidate >= 0)
                    .map(|candidate| candidate as u64)
            })
        })
}

fn string_array_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<Vec<String>> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(|value| match value {
            Value::Array(array) => Some(
                array
                    .iter()
                    .filter_map(|entry| entry.as_str().map(str::to_owned))
                    .collect(),
            ),
            Value::String(value) => Some(vec![value.clone()]),
            _ => None,
        })
}

fn i64_alias(map: &Map<String, Value>, aliases: &[&str]) -> Option<i64> {
    aliases
        .iter()
        .find_map(|alias| map.get(*alias))
        .and_then(|value| {
            value.as_i64().or_else(|| {
                value
                    .as_u64()
                    .and_then(|candidate| i64::try_from(candidate).ok())
            })
        })
}
