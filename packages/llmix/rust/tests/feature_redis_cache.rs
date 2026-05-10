#![cfg(feature = "redis")]

use llmix_rs::{ResponseCacheStrategy, TwoTierCache, TwoTierCacheConfig, CACHE_KEY_PREFIX};
use redis::AsyncCommands;
use serde_json::Value;
use std::env;
use std::time::{SystemTime, UNIX_EPOCH};

fn test_redis_url() -> Option<String> {
    env::var("LLMIX_REDIS_TEST_URL")
        .ok()
        .or_else(|| env::var("REDIS_URL").ok())
        .filter(|value| !value.trim().is_empty())
}

fn unique_key() -> String {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock should be after unix epoch")
        .as_nanos();
    format!("{CACHE_KEY_PREFIX}test:redis-cache:{nanos}")
}

#[tokio::test]
async fn redis_l2_round_trip_works_when_test_redis_is_available() {
    let Some(redis_url) = test_redis_url() else {
        eprintln!("skipping redis smoke test: set LLMIX_REDIS_TEST_URL or REDIS_URL to enable it");
        return;
    };

    let cache = TwoTierCache::new(
        ResponseCacheStrategy::Redis,
        TwoTierCacheConfig {
            max_items: 1,
            ttl_seconds: 60,
            redis_url: Some(redis_url.clone()),
        },
    )
    .expect("redis cache should construct");

    let client = redis::Client::open(redis_url.as_str()).expect("redis url should parse");
    let mut connection = client
        .get_multiplexed_async_connection()
        .await
        .expect("redis should be reachable for smoke test");

    let key = unique_key();
    let _: () = connection
        .del(&key)
        .await
        .expect("cleanup before test should succeed");

    cache.set(&key, "redis-value").await;

    let raw: String = connection
        .get(&key)
        .await
        .expect("raw payload should exist in redis");
    let payload: Value = serde_json::from_str(&raw).expect("payload should be valid json");
    assert_eq!(payload["data"], "redis-value");
    assert!(payload["cached_at"].as_u64().is_some());

    cache.clear().await.expect("redis clear should succeed");
    assert_eq!(cache.get(&key).await, None);

    let _: () = connection
        .del(&key)
        .await
        .expect("cleanup after test should succeed");
    cache.close().await;
}
