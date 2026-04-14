#![cfg(all(feature = "redis", feature = "helpers-openai"))]

use llmix_rs::{
    generate_cache_key, CacheHitTier, CacheKeyParams, CallInput, CallPipeline, KeyPool,
    OpenAiChatHelper, PipelineConfig, ResponseCacheStrategy, TwoTierCache, TwoTierCacheConfig,
};
use redis::AsyncCommands;
use serde_json::json;
use std::env;
use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

const LIVE_TESTS_FLAG: &str = "LLMIX_RUN_LIVE_TESTS";
const OPENAI_BASE_URL: &str = "https://api.openai.com/v1";

fn live_tests_enabled() -> bool {
    env::var(LIVE_TESTS_FLAG)
        .ok()
        .map(|value| value.trim().to_ascii_lowercase())
        .map(|value| matches!(value.as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
}

fn require_live_openai_and_redis_env() -> Option<(String, String)> {
    if !live_tests_enabled() {
        println!("skipping live redis pipeline test; set {LIVE_TESTS_FLAG}=1 to enable it");
        return None;
    }

    let openai_api_key = env::var("OPENAI_API_KEY")
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())?;
    let redis_url = env::var("LLMIX_REDIS_TEST_URL")
        .ok()
        .or_else(|| env::var("REDIS_URL").ok())
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())?;

    Some((openai_api_key, redis_url))
}

fn env_or(name: &str, fallback: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| fallback.to_string())
}

fn unique_nonce() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock should be after unix epoch")
        .as_nanos();
    format!("live-redis-{nanos}")
}

fn live_pipeline(api_key: String, redis_url: String) -> CallPipeline {
    let cache = Arc::new(
        TwoTierCache::new(
            ResponseCacheStrategy::Redis,
            TwoTierCacheConfig {
                max_items: 16,
                ttl_seconds: 300,
                redis_url: Some(redis_url),
            },
        )
        .expect("redis response cache should construct"),
    );

    let mut config = PipelineConfig::new(OpenAiChatHelper::new());
    config.max_retries = 0;
    config.retry_base_ms = 0;
    config.retry_max_delay_ms = 0;
    config.retry_jitter_ms = 0;
    config.retry_max_retry_after_ms = 0;
    config.semaphore_initial = 4;
    config.semaphore_min = 1;
    config.response_cache = Some(cache);

    let pipeline = CallPipeline::new(config).expect("live pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec![api_key]).expect("openai key pool should construct"),
    );
    pipeline
}

#[tokio::test]
async fn openai_live_response_round_trip_uses_real_redis_l2() {
    let Some((openai_api_key, redis_url)) = require_live_openai_and_redis_env() else {
        println!(
            "skipping live redis pipeline test; requires OPENAI_API_KEY and LLMIX_REDIS_TEST_URL or REDIS_URL"
        );
        return;
    };
    let model = env_or("OPENAI_MODEL", "gpt-4o-mini");
    let nonce = unique_nonce();
    let messages = vec![json!({
        "role": "user",
        "content": format!("Reply with the exact string 'cache-check-{nonce}' and nothing else.")
    })];

    let input = CallInput {
        config: json!({
            "provider": "openai",
            "model": model,
            "baseUrl": OPENAI_BASE_URL,
            "common": {
                "temperature": 0,
                "maxOutputTokens": 32
            },
            "caching": {
                "strategy": "redis"
            }
        }),
        messages: messages.clone(),
        singleflight_key: None,
    };

    let cache_key = generate_cache_key(&CacheKeyParams {
        provider: "openai".to_owned(),
        model: input.config["model"]
            .as_str()
            .expect("model should be a string")
            .to_owned(),
        messages: messages.clone(),
        base_url: Some(OPENAI_BASE_URL.to_owned()),
        enable_thinking: None,
        temperature: Some(0.0),
        max_output_tokens: Some(32),
        response_format: None,
        provider_options: None,
        seed: None,
        top_p: None,
        top_k: None,
        presence_penalty: None,
        frequency_penalty: None,
        stop_sequences: None,
    })
    .expect("cache key should serialize");

    let client = redis::Client::open(redis_url.as_str()).expect("redis url should parse");
    let mut connection = client
        .get_multiplexed_async_connection()
        .await
        .expect("redis should be reachable for live cache test");

    let _: () = connection
        .del(&cache_key)
        .await
        .expect("pre-test cleanup should succeed");

    let first_pipeline = live_pipeline(openai_api_key.clone(), redis_url.clone());
    let first = first_pipeline.call(input.clone()).await;
    assert!(
        first.success,
        "first live OpenAI call failed: {:?}",
        first.error
    );
    assert_eq!(first.cache_hit, None, "first call should not be cached");
    assert!(
        first.usage.total_tokens > 0,
        "first call should report real token usage"
    );

    let raw_payload: String = connection
        .get(&cache_key)
        .await
        .expect("redis should contain the cached provider response");
    let parsed_payload: serde_json::Value =
        serde_json::from_str(&raw_payload).expect("redis payload should be valid json");
    assert_eq!(parsed_payload["data"], first.content);
    assert!(parsed_payload["cached_at"].as_u64().is_some());

    first_pipeline.close().await;

    let second_pipeline = live_pipeline(openai_api_key, redis_url);
    let second = second_pipeline.call(input).await;
    assert!(
        second.success,
        "second cached call failed: {:?}",
        second.error
    );
    assert_eq!(second.cache_hit, Some(CacheHitTier::L2));
    assert_eq!(second.content, first.content);
    assert_eq!(second.usage.total_tokens, 0);

    let _: () = connection
        .del(&cache_key)
        .await
        .expect("post-test cleanup should succeed");
    second_pipeline.close().await;
}
