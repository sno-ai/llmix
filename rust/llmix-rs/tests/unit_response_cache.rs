use llmix_rs::{
    generate_cache_key, is_response_cache_strategy, resolve_response_cache_strategy,
    should_skip_cache, CacheHitTier, CacheKeyParams, CachingStrategy, ResponseCacheStrategy,
    TwoTierCache, TwoTierCacheConfig, CACHE_KEY_PREFIX,
};
use serde_json::json;

fn sample_params() -> CacheKeyParams {
    CacheKeyParams {
        provider: "openai".to_owned(),
        model: "gpt-4.1".to_owned(),
        messages: vec![json!({"role": "user", "content": "Hello"})],
        base_url: None,
        enable_thinking: None,
        temperature: Some(0.7),
        max_output_tokens: Some(1000),
        response_format: None,
        provider_options: None,
        seed: None,
        top_p: None,
    }
}

#[tokio::test]
async fn memory_cache_hits_evicts_and_clears() {
    let cache = TwoTierCache::new(
        ResponseCacheStrategy::Memory,
        TwoTierCacheConfig {
            max_items: 3,
            ttl_seconds: 60,
            redis_url: None,
        },
    )
    .expect("memory cache should construct");

    assert_eq!(cache.get("key1").await, None);

    cache.set("key1", "value1").await;
    let hit = cache.get("key1").await.expect("key1 should be cached");
    assert_eq!(hit.value, "value1");
    assert_eq!(hit.tier, CacheHitTier::L1);

    cache.set("key2", "value2").await;
    cache.set("key3", "value3").await;
    cache.set("key4", "value4").await;

    let stats = cache.get_stats().await;
    assert_eq!(stats.l1_size, 3);
    assert_eq!(stats.l1_max, 3);
    assert!(!stats.l2_enabled);
    assert_eq!(stats.strategy, ResponseCacheStrategy::Memory);

    assert_eq!(cache.get("key1").await, None);
    assert_eq!(
        cache
            .get("key4")
            .await
            .expect("key4 should still be cached")
            .value,
        "value4"
    );

    cache.clear().await;
    assert_eq!(cache.get("key4").await, None);
    assert_eq!(cache.get_stats().await.l1_size, 0);
}

#[test]
fn strategy_resolution_matches_python_and_typescript_contract() {
    assert!(is_response_cache_strategy(CachingStrategy::Redis));
    assert!(is_response_cache_strategy(CachingStrategy::RedisOrMemory));
    assert!(is_response_cache_strategy(CachingStrategy::Memory));
    assert!(!is_response_cache_strategy(CachingStrategy::Native));
    assert!(!is_response_cache_strategy(CachingStrategy::Gateway));
    assert!(!is_response_cache_strategy(CachingStrategy::Disabled));

    assert!(should_skip_cache(CachingStrategy::Native));
    assert!(should_skip_cache(CachingStrategy::Gateway));
    assert!(should_skip_cache(CachingStrategy::Disabled));
    assert!(!should_skip_cache(CachingStrategy::Redis));

    assert_eq!(
        resolve_response_cache_strategy(CachingStrategy::Redis, Some("redis://localhost:6379"))
            .expect("redis strategy with a URL should resolve"),
        Some(ResponseCacheStrategy::Redis)
    );
    assert!(resolve_response_cache_strategy(CachingStrategy::Redis, None).is_err());
    assert!(resolve_response_cache_strategy(CachingStrategy::Redis, Some("   ")).is_err());
    assert_eq!(
        resolve_response_cache_strategy(CachingStrategy::RedisOrMemory, None)
            .expect("redis-or-memory should degrade"),
        Some(ResponseCacheStrategy::Memory)
    );
    assert_eq!(
        resolve_response_cache_strategy(CachingStrategy::RedisOrMemory, Some("   "))
            .expect("blank redis url should degrade"),
        Some(ResponseCacheStrategy::Memory)
    );
    assert_eq!(
        resolve_response_cache_strategy(CachingStrategy::Native, None)
            .expect("native should not use response cache"),
        None
    );
}

#[test]
fn cache_keys_use_final_prefix_and_change_on_input_differences() {
    let base = generate_cache_key(&sample_params()).expect("key should serialize");
    let mut changed = sample_params();
    changed.temperature = Some(0.9);
    let changed_key = generate_cache_key(&changed).expect("key should serialize");

    assert!(base.starts_with(CACHE_KEY_PREFIX));
    assert!(changed_key.starts_with(CACHE_KEY_PREFIX));
    assert_ne!(base, changed_key);
}
