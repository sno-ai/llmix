use crate::canonical_json;
use crate::error::{LlmixError, LlmixResult};
use crate::types::{CacheHitTier, CachingStrategy, ResponseCacheStats, ResponseCacheStrategy};
use async_trait::async_trait;
use lru::LruCache;
#[cfg(feature = "redis")]
use redis::{aio::MultiplexedConnection, AsyncCommands, Client};
use serde::{Deserialize, Serialize};
use serde_json::{Map, Number, Value};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::num::NonZeroUsize;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::Mutex;

pub const CACHE_KEY_PREFIX: &str = "llmix:resp:";
const DEFAULT_L1_MAX: usize = 1000;
const DEFAULT_TTL_SECONDS: u64 = 3600;
const DEFAULT_L2_TTL_SECONDS: u64 = 3600;

const CACHE_KEY_FIELDS: [&str; 11] = [
    "baseUrl",
    "enableThinking",
    "maxOutputTokens",
    "messages",
    "model",
    "provider",
    "providerOptions",
    "responseFormat",
    "seed",
    "temperature",
    "topP",
];

fn has_redis_url(redis_url: Option<&str>) -> bool {
    redis_url.is_some_and(|value| !value.trim().is_empty())
}

#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct CacheKeyParams {
    pub provider: String,
    pub model: String,
    pub messages: Vec<Value>,
    #[serde(default)]
    pub base_url: Option<String>,
    #[serde(default)]
    pub enable_thinking: Option<bool>,
    #[serde(default)]
    pub temperature: Option<f64>,
    #[serde(default)]
    pub max_output_tokens: Option<u64>,
    #[serde(default)]
    pub response_format: Option<Value>,
    #[serde(default)]
    pub provider_options: Option<Value>,
    #[serde(default)]
    pub seed: Option<i64>,
    #[serde(default)]
    pub top_p: Option<f64>,
}

impl CacheKeyParams {
    fn to_canonical_value(&self) -> Value {
        let mut fields = HashMap::new();

        fields.insert("provider", Value::String(self.provider.clone()));
        fields.insert("model", Value::String(self.model.clone()));
        fields.insert("messages", Value::Array(self.messages.clone()));

        if let Some(base_url) = self.base_url.as_ref() {
            fields.insert("baseUrl", Value::String(base_url.clone()));
        }
        if let Some(enable_thinking) = self.enable_thinking {
            fields.insert("enableThinking", Value::Bool(enable_thinking));
        }
        if let Some(value) = self.max_output_tokens {
            fields.insert("maxOutputTokens", Value::Number(Number::from(value)));
        }
        if let Some(value) = self
            .response_format
            .clone()
            .filter(|value| !value.is_null())
        {
            fields.insert("responseFormat", value);
        }
        if let Some(value) = self
            .provider_options
            .clone()
            .filter(|value| !value.is_null())
        {
            fields.insert("providerOptions", value);
        }
        if let Some(value) = self.seed {
            fields.insert("seed", Value::Number(Number::from(value)));
        }
        if let Some(value) = finite_number(self.temperature) {
            fields.insert("temperature", Value::Number(value));
        }
        if let Some(value) = finite_number(self.top_p) {
            fields.insert("topP", Value::Number(value));
        }

        let mut map = Map::new();
        for field in CACHE_KEY_FIELDS {
            if let Some(value) = fields.remove(field) {
                map.insert(field.to_owned(), value);
            }
        }
        Value::Object(map)
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CacheResult {
    pub value: String,
    pub tier: CacheHitTier,
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct CachedValue {
    data: String,
    cached_at: u64,
}

#[derive(Debug, Clone)]
pub struct TwoTierCacheConfig {
    pub max_items: usize,
    pub ttl_seconds: u64,
    pub redis_url: Option<String>,
}

impl Default for TwoTierCacheConfig {
    fn default() -> Self {
        Self {
            max_items: DEFAULT_L1_MAX,
            ttl_seconds: DEFAULT_TTL_SECONDS,
            redis_url: None,
        }
    }
}

#[derive(Debug, Serialize)]
struct RedisPayload<'a> {
    data: &'a str,
    cached_at: u64,
}

#[derive(Debug, Deserialize)]
struct StoredPayload {
    data: String,
    #[serde(default, alias = "cachedAt")]
    cached_at: Option<f64>,
}

#[async_trait]
trait L2CacheBackend: Send + Sync {
    async fn get(&self, key: &str) -> LlmixResult<Option<String>>;
    async fn setex(&self, key: &str, ttl_seconds: u64, value: String) -> LlmixResult<()>;
    async fn close(&self) -> LlmixResult<()> {
        Ok(())
    }
}

#[cfg(feature = "redis")]
struct RedisBackend {
    client: Client,
    connection: Mutex<Option<MultiplexedConnection>>,
}

#[cfg(feature = "redis")]
impl RedisBackend {
    fn new(redis_url: &str) -> LlmixResult<Self> {
        let client = Client::open(redis_url)
            .map_err(|error| LlmixError::Redis(format!("invalid redis url: {error}")))?;
        Ok(Self {
            client,
            connection: Mutex::new(None),
        })
    }

    async fn connection(&self) -> LlmixResult<MultiplexedConnection> {
        let mut cached = self.connection.lock().await;
        if let Some(connection) = cached.as_ref() {
            return Ok(connection.clone());
        }

        let connection = self
            .client
            .get_multiplexed_async_connection()
            .await
            .map_err(map_redis_error)?;
        *cached = Some(connection.clone());
        Ok(connection)
    }

    async fn reset_connection(&self) {
        *self.connection.lock().await = None;
    }
}

#[cfg(feature = "redis")]
#[async_trait]
impl L2CacheBackend for RedisBackend {
    async fn get(&self, key: &str) -> LlmixResult<Option<String>> {
        let mut connection = self.connection().await?;
        match connection.get(key).await {
            Ok(value) => Ok(value),
            Err(error) => {
                self.reset_connection().await;
                Err(map_redis_error(error))
            }
        }
    }

    async fn setex(&self, key: &str, ttl_seconds: u64, value: String) -> LlmixResult<()> {
        let mut connection = self.connection().await?;
        match connection.set_ex(key, value, ttl_seconds).await {
            Ok(()) => Ok(()),
            Err(error) => {
                self.reset_connection().await;
                Err(map_redis_error(error))
            }
        }
    }

    async fn close(&self) -> LlmixResult<()> {
        self.reset_connection().await;
        Ok(())
    }
}

pub struct TwoTierCache {
    strategy: ResponseCacheStrategy,
    ttl_seconds: u64,
    l1_max: usize,
    l1: Mutex<LruCache<String, CachedValue>>,
    l2_enabled: bool,
    l2_healthy: Mutex<bool>,
    l2_consecutive_write_failures: Mutex<u32>,
    backend: Option<Arc<dyn L2CacheBackend>>,
}

impl TwoTierCache {
    pub fn new(strategy: ResponseCacheStrategy, config: TwoTierCacheConfig) -> LlmixResult<Self> {
        let redis_requested = has_redis_url(config.redis_url.as_deref());
        if strategy == ResponseCacheStrategy::Redis && !redis_requested {
            return Err(LlmixError::InvalidResponseCacheConfig(
                "TwoTierCache strategy \"redis\" requires config.redis_url to be set.".to_owned(),
            ));
        }

        let max_items = NonZeroUsize::new(config.max_items.max(1)).expect("max(1) is non-zero");
        #[allow(unused_mut)]
        let mut backend: Option<Arc<dyn L2CacheBackend>> = None;

        #[cfg(feature = "redis")]
        if strategy != ResponseCacheStrategy::Memory && redis_requested {
            let redis_url = config
                .redis_url
                .as_deref()
                .expect("redis_requested implies redis_url is present");
            backend = Some(Arc::new(RedisBackend::new(redis_url)?));
        }

        #[cfg(not(feature = "redis"))]
        if strategy == ResponseCacheStrategy::Redis && redis_requested {
            return Err(LlmixError::InvalidResponseCacheConfig(
                "TwoTierCache strategy \"redis\" requires llmix-rs to be built with the `redis` feature.".to_owned(),
            ));
        }

        let l2_enabled = strategy != ResponseCacheStrategy::Memory && backend.is_some();

        Ok(Self {
            strategy,
            ttl_seconds: config.ttl_seconds.max(1),
            l1_max: max_items.get(),
            l1: Mutex::new(LruCache::new(max_items)),
            l2_enabled,
            l2_healthy: Mutex::new(true),
            l2_consecutive_write_failures: Mutex::new(0),
            backend,
        })
    }

    #[cfg(test)]
    fn new_with_backend(
        strategy: ResponseCacheStrategy,
        config: TwoTierCacheConfig,
        backend: Arc<dyn L2CacheBackend>,
    ) -> LlmixResult<Self> {
        let max_items = NonZeroUsize::new(config.max_items.max(1)).expect("max(1) is non-zero");
        Ok(Self {
            strategy,
            ttl_seconds: config.ttl_seconds.max(1),
            l1_max: max_items.get(),
            l1: Mutex::new(LruCache::new(max_items)),
            l2_enabled: strategy != ResponseCacheStrategy::Memory,
            l2_healthy: Mutex::new(true),
            l2_consecutive_write_failures: Mutex::new(0),
            backend: Some(backend),
        })
    }

    pub async fn get(&self, key: &str) -> Option<CacheResult> {
        if let Some(hit) = self.get_l1(key).await {
            return Some(CacheResult {
                value: hit.data,
                tier: CacheHitTier::L1,
            });
        }

        if !self.l2_enabled {
            return None;
        }

        let backend = self.backend.as_ref()?;
        let raw = match backend.get(key).await {
            Ok(value) => value?,
            Err(_) => return None,
        };
        let parsed = serde_json::from_str::<StoredPayload>(&raw).ok()?;
        let cached_at = normalize_cached_at_seconds(parsed.cached_at);
        let age_seconds = now_seconds().saturating_sub(cached_at);
        if age_seconds >= self.ttl_seconds {
            return None;
        }

        let entry = CachedValue {
            data: parsed.data,
            cached_at,
        };
        self.put_l1(key, entry.clone()).await;

        Some(CacheResult {
            value: entry.data,
            tier: CacheHitTier::L2,
        })
    }

    pub async fn set(&self, key: &str, value: &str) {
        let entry = CachedValue {
            data: value.to_owned(),
            cached_at: now_seconds(),
        };
        self.put_l1(key, entry.clone()).await;

        if !self.l2_enabled {
            return;
        }

        let Some(backend) = self.backend.as_ref() else {
            return;
        };

        let ttl = self.ttl_seconds.max(DEFAULT_L2_TTL_SECONDS);
        let payload = serde_json::to_string(&RedisPayload {
            data: &entry.data,
            cached_at: entry.cached_at,
        });
        let Ok(payload) = payload else {
            return;
        };

        match backend.setex(key, ttl, payload).await {
            Ok(()) => {
                *self.l2_consecutive_write_failures.lock().await = 0;
                *self.l2_healthy.lock().await = true;
            }
            Err(_) => {
                let mut failures = self.l2_consecutive_write_failures.lock().await;
                *failures += 1;
                if *failures >= 3 {
                    *self.l2_healthy.lock().await = false;
                }
            }
        }
    }

    pub async fn clear(&self) {
        self.l1.lock().await.clear();
    }

    pub async fn close(&self) {
        if let Some(backend) = self.backend.as_ref() {
            let _ = backend.close().await;
        }
    }

    pub async fn get_stats(&self) -> ResponseCacheStats {
        let l1_size = self.l1.lock().await.len();
        ResponseCacheStats {
            l1_size,
            l1_max: self.l1_max,
            l2_enabled: self.l2_enabled,
            l2_healthy: *self.l2_healthy.lock().await,
            strategy: self.strategy,
        }
    }

    async fn get_l1(&self, key: &str) -> Option<CachedValue> {
        let mut l1 = self.l1.lock().await;
        let cached = l1.get(key).cloned()?;
        let age_seconds = now_seconds().saturating_sub(cached.cached_at);
        if age_seconds >= self.ttl_seconds {
            l1.pop(key);
            return None;
        }
        Some(cached)
    }

    async fn put_l1(&self, key: &str, value: CachedValue) {
        self.l1.lock().await.put(key.to_owned(), value);
    }
}

pub fn is_response_cache_strategy(strategy: CachingStrategy) -> bool {
    matches!(
        strategy,
        CachingStrategy::Redis | CachingStrategy::RedisOrMemory | CachingStrategy::Memory
    )
}

pub fn should_skip_cache(strategy: CachingStrategy) -> bool {
    matches!(
        strategy,
        CachingStrategy::Native | CachingStrategy::Gateway | CachingStrategy::Disabled
    )
}

pub fn resolve_response_cache_strategy(
    strategy: CachingStrategy,
    redis_url: Option<&str>,
) -> LlmixResult<Option<ResponseCacheStrategy>> {
    if !is_response_cache_strategy(strategy) {
        return Ok(None);
    }

    match strategy {
        CachingStrategy::Redis => {
            if !has_redis_url(redis_url) {
                return Err(LlmixError::InvalidResponseCacheConfig(
                    "Response cache strategy \"redis\" requires REDIS_URL to be set.".to_owned(),
                ));
            }
            Ok(Some(ResponseCacheStrategy::Redis))
        }
        CachingStrategy::RedisOrMemory => {
            if !has_redis_url(redis_url) {
                return Ok(Some(ResponseCacheStrategy::Memory));
            }
            Ok(Some(ResponseCacheStrategy::RedisOrMemory))
        }
        CachingStrategy::Memory => Ok(Some(ResponseCacheStrategy::Memory)),
        _ => Ok(None),
    }
}

pub fn generate_cache_key(params: &CacheKeyParams) -> LlmixResult<String> {
    let canonical = params.to_canonical_value();
    let json = canonical_json::to_string(&canonical)?;
    let mut hasher = Sha256::new();
    hasher.update(json.as_bytes());
    let digest = format!("{:x}", hasher.finalize());
    Ok(format!("{CACHE_KEY_PREFIX}{digest}"))
}

fn finite_number(value: Option<f64>) -> Option<Number> {
    let value = value?;
    canonical_number_from_f64(value)
}

fn canonical_number_from_f64(value: f64) -> Option<Number> {
    if !value.is_finite() {
        return None;
    }

    if value == 0.0 {
        return Some(Number::from(0));
    }

    if value.fract() == 0.0 {
        if value.is_sign_positive() && value <= u64::MAX as f64 {
            let integer = value as u64;
            if integer as f64 == value {
                return Some(Number::from(integer));
            }
        }

        if value >= i64::MIN as f64 && value <= i64::MAX as f64 {
            let integer = value as i64;
            if integer as f64 == value {
                return Some(Number::from(integer));
            }
        }
    }

    Number::from_f64(value)
}

fn normalize_cached_at_seconds(raw: Option<f64>) -> u64 {
    let Some(raw) = raw else {
        return now_seconds();
    };
    if !raw.is_finite() || raw <= 0.0 {
        return now_seconds();
    }
    if raw > 1_000_000_000_000.0 {
        return (raw / 1000.0).floor() as u64;
    }
    raw.floor() as u64
}

fn now_seconds() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("system clock should be after unix epoch")
        .as_secs()
}

#[cfg(feature = "redis")]
fn map_redis_error(error: redis::RedisError) -> LlmixError {
    LlmixError::Redis(error.to_string())
}

#[cfg(test)]
mod tests {
    use super::{
        generate_cache_key, CacheKeyParams, L2CacheBackend, ResponseCacheStrategy, TwoTierCache,
        TwoTierCacheConfig, CACHE_KEY_PREFIX,
    };
    use crate::types::{CacheHitTier, CachingStrategy};
    use async_trait::async_trait;
    use serde_json::{json, Value};
    use std::collections::HashMap;
    use std::sync::Arc;
    use tokio::sync::Mutex;

    #[derive(Default)]
    struct MockBackend {
        store: Mutex<HashMap<String, String>>,
        fail_writes: Mutex<u32>,
    }

    #[async_trait]
    impl L2CacheBackend for MockBackend {
        async fn get(&self, key: &str) -> crate::error::LlmixResult<Option<String>> {
            Ok(self.store.lock().await.get(key).cloned())
        }

        async fn setex(
            &self,
            key: &str,
            _ttl_seconds: u64,
            value: String,
        ) -> crate::error::LlmixResult<()> {
            let mut remaining_failures = self.fail_writes.lock().await;
            if *remaining_failures > 0 {
                *remaining_failures -= 1;
                return Err(crate::error::LlmixError::InvalidResponseCacheConfig(
                    "simulated write failure".to_owned(),
                ));
            }
            self.store.lock().await.insert(key.to_owned(), value);
            Ok(())
        }
    }

    #[tokio::test]
    async fn l2_write_payload_uses_seconds_and_snake_case() {
        let backend = Arc::new(MockBackend::default());
        let cache = TwoTierCache::new_with_backend(
            ResponseCacheStrategy::Redis,
            TwoTierCacheConfig {
                max_items: 10,
                ttl_seconds: 60,
                redis_url: Some("redis://localhost:6379".to_owned()),
            },
            backend.clone(),
        )
        .expect("cache should construct");

        cache.set("redis-seconds", "value").await;

        let raw = backend
            .store
            .lock()
            .await
            .get("redis-seconds")
            .cloned()
            .expect("mock backend should have stored payload");
        let payload: Value = serde_json::from_str(&raw).expect("payload should be valid json");
        assert_eq!(payload["data"], "value");
        let cached_at = payload["cached_at"]
            .as_u64()
            .expect("cached_at should be seconds");
        assert!(cached_at > 1_000_000_000);
        assert!(cached_at < 1_000_000_000_000);
    }

    #[tokio::test]
    async fn l2_get_accepts_legacy_millisecond_timestamps() {
        let backend = Arc::new(MockBackend::default());
        let fresh_legacy_ms = super::now_seconds().saturating_sub(1) * 1000;
        backend.store.lock().await.insert(
            "legacy-ms".to_owned(),
            format!(
                r#"{{"data":"legacy-value","cached_at":{}}}"#,
                fresh_legacy_ms
            ),
        );
        let cache = TwoTierCache::new_with_backend(
            ResponseCacheStrategy::RedisOrMemory,
            TwoTierCacheConfig {
                max_items: 10,
                ttl_seconds: 60,
                redis_url: Some("redis://localhost:6379".to_owned()),
            },
            backend,
        )
        .expect("cache should construct");

        let hit = cache.get("legacy-ms").await.expect("l2 should hit");
        assert_eq!(hit.value, "legacy-value");
        assert_eq!(hit.tier, CacheHitTier::L2);
    }

    #[tokio::test]
    async fn third_consecutive_l2_write_failure_marks_backend_unhealthy() {
        let backend = Arc::new(MockBackend::default());
        *backend.fail_writes.lock().await = 3;

        let cache = TwoTierCache::new_with_backend(
            ResponseCacheStrategy::RedisOrMemory,
            TwoTierCacheConfig {
                max_items: 10,
                ttl_seconds: 60,
                redis_url: Some("redis://localhost:6379".to_owned()),
            },
            backend,
        )
        .expect("cache should construct");

        cache.set("key1", "value1").await;
        assert!(cache.get_stats().await.l2_healthy);
        cache.set("key2", "value2").await;
        assert!(cache.get_stats().await.l2_healthy);
        cache.set("key3", "value3").await;
        assert!(!cache.get_stats().await.l2_healthy);
    }

    #[test]
    fn non_finite_temperatures_collapse_to_same_cache_key_as_absent() {
        let absent = CacheKeyParams {
            provider: "openai".to_owned(),
            model: "gpt-4.1".to_owned(),
            messages: vec![json!({"role": "user", "content": "Hello"})],
            base_url: None,
            enable_thinking: None,
            temperature: None,
            max_output_tokens: None,
            response_format: None,
            provider_options: None,
            seed: None,
            top_p: None,
        };
        let nan = CacheKeyParams {
            temperature: Some(f64::NAN),
            ..absent.clone()
        };
        let inf = CacheKeyParams {
            temperature: Some(f64::INFINITY),
            ..absent.clone()
        };

        let absent_key = generate_cache_key(&absent).expect("key should serialize");
        let nan_key = generate_cache_key(&nan).expect("nan should be omitted");
        let inf_key = generate_cache_key(&inf).expect("inf should be omitted");

        assert_eq!(absent_key, nan_key);
        assert_eq!(absent_key, inf_key);
        assert!(absent_key.starts_with(CACHE_KEY_PREFIX));
    }

    #[test]
    fn strategy_helpers_match_contract() {
        assert!(super::is_response_cache_strategy(CachingStrategy::Redis));
        assert!(super::is_response_cache_strategy(
            CachingStrategy::RedisOrMemory
        ));
        assert!(super::is_response_cache_strategy(CachingStrategy::Memory));
        assert!(!super::is_response_cache_strategy(
            CachingStrategy::Disabled
        ));

        assert!(super::should_skip_cache(CachingStrategy::Native));
        assert!(super::should_skip_cache(CachingStrategy::Gateway));
        assert!(super::should_skip_cache(CachingStrategy::Disabled));
        assert!(!super::should_skip_cache(CachingStrategy::Redis));
    }
}
