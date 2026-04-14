use llmix_rs::{
    generate_cache_key, CacheHitTier, CacheKeyParams, CallInput, CallPipeline, CircuitState,
    DispatchContext, DispatchFn, InvalidConfigError, KeyPool, LlmUsage, LlmixError, PipelineConfig,
    ProviderError, ProviderResult, ResponseCacheStrategy, TwoTierCache, TwoTierCacheConfig,
};
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn sample_messages() -> Vec<Value> {
    vec![json!({
        "role": "user",
        "content": "Hello from Rust"
    })]
}

fn base_input() -> CallInput {
    CallInput {
        config: json!({
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "caching": { "strategy": "memory" },
            "common": {
                "enableThinking": true,
                "temperature": 0.2,
                "maxOutputTokens": 128
            }
        }),
        messages: sample_messages(),
        singleflight_key: None,
    }
}

fn cache_key_for(input: &CallInput) -> String {
    generate_cache_key(&CacheKeyParams {
        provider: "openai".to_owned(),
        model: "gpt-4.1-mini".to_owned(),
        messages: input.messages.clone(),
        base_url: None,
        enable_thinking: Some(true),
        temperature: Some(0.2),
        max_output_tokens: Some(128),
        response_format: None,
        provider_options: None,
        seed: None,
        top_p: None,
    })
    .expect("cache key should serialize")
}

fn memory_cache() -> Arc<TwoTierCache> {
    Arc::new(
        TwoTierCache::new(
            ResponseCacheStrategy::Memory,
            TwoTierCacheConfig {
                max_items: 32,
                ttl_seconds: 60,
                redis_url: None,
            },
        )
        .expect("memory cache should construct"),
    )
}

fn fast_pipeline_config<D>(dispatch: D, cache: Option<Arc<TwoTierCache>>) -> PipelineConfig
where
    D: DispatchFn + 'static,
{
    let mut config = PipelineConfig::new(dispatch);
    config.max_retries = 1;
    config.retry_base_ms = 0;
    config.retry_max_delay_ms = 0;
    config.retry_jitter_ms = 0;
    config.retry_max_retry_after_ms = 0;
    config.response_cache = cache;
    config.semaphore_initial = 8;
    config.semaphore_min = 1;
    config
}

fn success_result(content: &str) -> ProviderResult {
    ProviderResult {
        content: content.to_owned(),
        model: "gpt-4.1-mini".to_owned(),
        usage: LlmUsage {
            input_tokens: 11,
            output_tokens: 7,
            total_tokens: 18,
        },
        headers: None,
        tool_calls: None,
    }
}

fn provider_error(message: &str, status_code: u16) -> llmix_rs::LlmixError {
    ProviderError {
        message: message.to_owned(),
        status_code: Some(status_code),
        headers: None,
    }
    .into()
}

fn temp_state_dir(prefix: &str) -> std::path::PathBuf {
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("time should move forward")
        .as_nanos();
    std::env::temp_dir().join(format!(
        "llmix-rs-pipeline-{prefix}-{}-{nanos}",
        std::process::id()
    ))
}

#[tokio::test]
async fn cache_hit_avoids_dispatch_and_strips_thinking() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let cache = memory_cache();
    let input = base_input();
    let cache_key = cache_key_for(&input);
    cache
        .set(
            &cache_key,
            "visible <think>draft reasoning</think>\n final answer",
        )
        .await;

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Ok(success_result("should never run"))
            }
        },
        Some(cache),
    ))
    .expect("pipeline should construct");

    let response = pipeline.call(input).await;

    assert!(response.success);
    assert_eq!(response.content, "visible final answer");
    assert_eq!(
        response.thinking_content.as_deref(),
        Some("draft reasoning")
    );
    assert_eq!(response.cache_hit, Some(CacheHitTier::L1));
    assert_eq!(response.usage, LlmUsage::default());
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn successful_dispatch_caches_raw_content_and_preserves_tool_calls() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let cache = memory_cache();
    let input = base_input();
    let cache_key = cache_key_for(&input);

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                assert_eq!(ctx.api_key, "key-a");
                assert_eq!(ctx.provider, "openai");
                assert_eq!(ctx.model, "gpt-4.1-mini");
                assert_eq!(ctx.kwargs.get("max_tokens"), Some(&json!(128)),);

                Ok(ProviderResult {
                    content: "<think>hidden plan</think>\nfinal answer".to_owned(),
                    model: "gpt-4.1-mini".to_owned(),
                    usage: LlmUsage {
                        input_tokens: 21,
                        output_tokens: 8,
                        total_tokens: 29,
                    },
                    headers: None,
                    tool_calls: Some(vec![json!({
                        "id": "call_1",
                        "type": "function",
                        "function": { "name": "lookup" }
                    })]),
                })
            }
        },
        Some(cache.clone()),
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let response = pipeline.call(input).await;

    assert!(response.success);
    assert_eq!(response.content, "final answer");
    assert_eq!(response.thinking_content.as_deref(), Some("hidden plan"));
    assert_eq!(response.cache_hit, None);
    assert_eq!(
        response.tool_calls,
        Some(vec![json!({
            "id": "call_1",
            "type": "function",
            "function": { "name": "lookup" }
        })])
    );
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 1);

    let cached = cache
        .get(&cache_key)
        .await
        .expect("response should be written to cache");
    assert_eq!(cached.value, "<think>hidden plan</think>\nfinal answer");
    assert_eq!(cached.tier, CacheHitTier::L1);
}

#[tokio::test]
async fn rate_limit_rotates_to_next_key_before_retrying() {
    let seen_keys = Arc::new(Mutex::new(Vec::<String>::new()));
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let key_log = seen_keys.clone();
    let seen_dispatch_calls = dispatch_calls.clone();
    let input = base_input();

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |ctx: DispatchContext| {
            let key_log = key_log.clone();
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                key_log
                    .lock()
                    .expect("key log mutex poisoned")
                    .push(ctx.api_key.clone());

                if ctx.api_key == "key-a" {
                    Err(provider_error("rate limited", 429))
                } else {
                    Ok(success_result("retry succeeded"))
                }
            }
        },
        None,
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned(), "key-b".to_owned()])
            .expect("key pool should construct"),
    );

    let response = pipeline.call(input).await;

    assert!(response.success);
    assert_eq!(response.content, "retry succeeded");
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        seen_keys.lock().expect("key log mutex poisoned").as_slice(),
        ["key-a", "key-b"]
    );
}

#[tokio::test]
async fn snogpu_alias_applies_transform_kwargs_end_to_end() {
    let seen_kwargs = Arc::new(Mutex::new(None::<serde_json::Map<String, Value>>));
    let kwargs_slot = seen_kwargs.clone();

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |ctx: DispatchContext| {
            let kwargs_slot = kwargs_slot.clone();
            async move {
                *kwargs_slot
                    .lock()
                    .expect("kwargs mutex should remain available") = Some(ctx.kwargs.clone());
                Ok(success_result("ok"))
            }
        },
        None,
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "snogpu",
        KeyPool::new(vec!["not-needed".to_owned()]).expect("key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "snogpu",
                "model": "qwen3.5-27b-reason",
                "baseUrl": "https://rt3-llm.sno.ai",
                "providerOptions": {
                    "snogpu": {
                        "gpuPath": "reason",
                        "enableThinking": true
                    }
                },
                "common": {
                    "enableThinking": false,
                    "maxOutputTokens": 64
                }
            }),
            messages: sample_messages(),
            singleflight_key: None,
        })
        .await;

    assert!(response.success);

    let seen_kwargs = seen_kwargs
        .lock()
        .expect("kwargs mutex should remain available")
        .clone()
        .expect("dispatch should have run once");
    assert_eq!(
        seen_kwargs.get("base_url"),
        Some(&json!("https://rt3-llm.sno.ai/reason/v1"))
    );
    assert_eq!(seen_kwargs.get("enableThinking"), Some(&json!(true)));
    assert_eq!(seen_kwargs.get("max_tokens"), Some(&json!(64)));
}

#[tokio::test]
async fn unauthorized_response_marks_key_dead_and_next_call_exhausts_pool() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let input = base_input();

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Err(provider_error("unauthorized", 401))
            }
        },
        None,
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["dead-key".to_owned()]).expect("key pool should construct"),
    );

    let first = pipeline.call(input.clone()).await;
    let second = pipeline.call(input).await;

    assert!(!first.success);
    assert_eq!(first.error.as_deref(), Some("unauthorized"));

    assert!(!second.success);
    assert!(second
        .error
        .as_deref()
        .is_some_and(|error| error.contains("all 1 keys are dead")));
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn identical_concurrent_calls_share_a_single_dispatch() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let input = base_input();

    let pipeline = Arc::new(
        CallPipeline::new(fast_pipeline_config(
            move |_ctx: DispatchContext| {
                let seen_dispatch_calls = seen_dispatch_calls.clone();
                async move {
                    seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                    tokio::time::sleep(Duration::from_millis(50)).await;
                    Ok(success_result("shared result"))
                }
            },
            None,
        ))
        .expect("pipeline should construct"),
    );
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let first_pipeline = pipeline.clone();
    let second_pipeline = pipeline.clone();
    let first_input = input.clone();
    let second_input = input;

    let first = tokio::spawn(async move { first_pipeline.call(first_input).await });
    let second = tokio::spawn(async move { second_pipeline.call(second_input).await });

    let first_response = first.await.expect("first task should join");
    let second_response = second.await.expect("second task should join");

    assert!(first_response.success);
    assert!(second_response.success);
    assert_eq!(first_response.content, "shared result");
    assert_eq!(second_response.content, "shared result");
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 1);
    assert_eq!(pipeline.singleflight_count(), 0);
}

#[tokio::test]
async fn keep_thinking_output_preserves_visible_response() {
    let input = CallInput {
        config: json!({
            "provider": "openai",
            "model": "gpt-4.1-mini",
            "common": {
                "enableThinking": true,
                "keepThinkingOutput": true
            }
        }),
        messages: sample_messages(),
        singleflight_key: None,
    };

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |_ctx: DispatchContext| async move {
            Ok(success_result("<think>do not strip</think>visible"))
        },
        None,
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let response = pipeline.call(input).await;

    assert!(response.success);
    assert_eq!(response.content, "<think>do not strip</think>visible");
    assert_eq!(response.thinking_content, None);
}

#[tokio::test]
async fn semaphore_release_on_dispatch_failure_allows_next_call() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                let attempt = seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                if attempt == 0 {
                    Err(provider_error("server error", 500))
                } else {
                    Ok(success_result("recovered"))
                }
            }
        },
        None,
    );
    config.semaphore_initial = 1;
    config.semaphore_min = 1;
    config.max_retries = 0;

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let first = tokio::time::timeout(Duration::from_millis(200), pipeline.call(base_input()))
        .await
        .expect("first call should not hang");
    let second = tokio::time::timeout(Duration::from_millis(200), pipeline.call(base_input()))
        .await
        .expect("second call should not hang");

    assert!(!first.success);
    assert!(second.success);
    assert_eq!(second.content, "recovered");
    assert_eq!(pipeline.get_semaphore_window("openai"), Some(1));
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test]
async fn semaphore_release_on_key_selection_failure_restores_permit() {
    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| async move { Ok(success_result("unused")) },
        None,
    );
    config.semaphore_initial = 1;
    config.semaphore_min = 1;

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    let exhausted_pool = KeyPool::new(vec!["dead-key".to_owned()]).expect("pool should build");
    exhausted_pool.mark_dead("dead-key").unwrap();
    pipeline.set_key_pool("openai", exhausted_pool);

    let first = tokio::time::timeout(
        Duration::from_millis(200),
        pipeline.call(CallInput {
            messages: vec![json!({"role": "user", "content": "no-key-1"})],
            singleflight_key: Some("no-key-1".to_owned()),
            ..base_input()
        }),
    )
    .await
    .expect("first call should not hang");
    let second = tokio::time::timeout(
        Duration::from_millis(200),
        pipeline.call(CallInput {
            messages: vec![json!({"role": "user", "content": "no-key-2"})],
            singleflight_key: Some("no-key-2".to_owned()),
            ..base_input()
        }),
    )
    .await
    .expect("second call should not hang");

    assert!(!first.success);
    assert!(!second.success);
    assert_eq!(pipeline.get_semaphore_window("openai"), Some(1));
}

#[test]
fn semaphore_release_on_401_cleanup_failure_allows_next_call() {
    let pipeline_slot = Arc::new(OnceLock::<Arc<CallPipeline>>::new());
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let seen_pipeline_slot = pipeline_slot.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            let seen_pipeline_slot = seen_pipeline_slot.clone();
            async move {
                let attempt = seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                if attempt == 0 {
                    let pipeline = seen_pipeline_slot
                        .get()
                        .expect("pipeline should be available before dispatch");
                    pipeline.set_key_pool(
                        "openai",
                        KeyPool::new(vec!["key-b".to_owned()])
                            .expect("replacement key pool should construct"),
                    );
                    Err(provider_error("unauthorized", 401))
                } else {
                    Ok(success_result("recovered"))
                }
            }
        },
        None,
    );
    config.semaphore_initial = 1;
    config.semaphore_min = 1;
    config.max_retries = 0;

    let pipeline = Arc::new(CallPipeline::new(config).expect("pipeline should construct"));
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );
    assert!(
        pipeline_slot.set(pipeline.clone()).is_ok(),
        "pipeline should only be set once"
    );

    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .build()
        .expect("runtime should build");

    runtime.block_on(async {
        let first = tokio::time::timeout(Duration::from_millis(200), pipeline.call(base_input()))
            .await
            .expect("first call should not hang");
        let second = tokio::time::timeout(Duration::from_millis(200), pipeline.call(base_input()))
            .await
            .expect("second call should not hang");

        assert!(!first.success);
        assert!(second.success);
        assert_eq!(second.content, "recovered");
        assert_eq!(pipeline.get_semaphore_window("openai"), Some(1));
        assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
    });
}

#[tokio::test]
async fn circuit_breaker_trips_after_retryable_failures() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Err(provider_error("server error", 500))
            }
        },
        None,
    );
    config.max_retries = 0;
    config.circuit_breaker_threshold = 2;
    config.circuit_breaker_cooldown = Duration::from_secs(60);

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let first = pipeline
        .call(CallInput {
            singleflight_key: Some("t1".to_owned()),
            ..base_input()
        })
        .await;
    let second = pipeline
        .call(CallInput {
            singleflight_key: Some("t2".to_owned()),
            ..base_input()
        })
        .await;
    let third = pipeline
        .call(CallInput {
            singleflight_key: Some("t3".to_owned()),
            ..base_input()
        })
        .await;

    assert!(!first.success);
    assert!(!second.success);
    assert!(!third.success);
    assert!(third
        .error
        .as_deref()
        .is_some_and(|error| error.contains("circuit breaker OPEN")));
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", ""),
        Some(CircuitState::Open)
    );
}

#[tokio::test]
async fn circuit_breaker_ignores_non_retryable_provider_errors() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Err(provider_error("bad request", 400))
            }
        },
        None,
    );
    config.max_retries = 0;
    config.circuit_breaker_threshold = 2;

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    for key in ["a1", "a2", "a3"] {
        let response = pipeline
            .call(CallInput {
                messages: vec![json!({"role": "user", "content": "400"})],
                singleflight_key: Some(key.to_owned()),
                ..base_input()
            })
            .await;
        assert!(!response.success);
    }

    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 3);
    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", ""),
        Some(CircuitState::Closed)
    );
}

#[tokio::test]
async fn circuit_breaker_ignores_local_validation_errors() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Err(LlmixError::InvalidConfig(InvalidConfigError {
                    message: "invalid local request".to_owned(),
                }))
            }
        },
        None,
    );
    config.max_retries = 0;
    config.circuit_breaker_threshold = 1;
    config.circuit_breaker_cooldown = Duration::from_secs(60);

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let first = pipeline
        .call(CallInput {
            messages: vec![json!({"role": "user", "content": "local-1"})],
            singleflight_key: Some("local-1".to_owned()),
            ..base_input()
        })
        .await;
    let second = pipeline
        .call(CallInput {
            messages: vec![json!({"role": "user", "content": "local-2"})],
            singleflight_key: Some("local-2".to_owned()),
            ..base_input()
        })
        .await;

    assert!(!first.success);
    assert!(!second.success);
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", ""),
        Some(CircuitState::Closed)
    );
}

#[tokio::test]
async fn kill_switch_blocks_call_before_dispatch() {
    let state_dir = temp_state_dir("killswitch");
    fs::create_dir_all(&state_dir).expect("state dir should exist");
    fs::write(state_dir.join("killswitch"), "").expect("killswitch file should exist");

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| async move { Ok(success_result("unused")) },
        None,
    );
    config.kill_switch_state_dir = Some(state_dir.clone());

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    let response = pipeline.call(base_input()).await;

    let _ = fs::remove_file(state_dir.join("killswitch"));
    let _ = fs::remove_dir_all(state_dir);

    assert!(!response.success);
    assert!(response
        .error
        .as_deref()
        .is_some_and(|error| error.contains("kill switch active")));
}

#[tokio::test]
async fn retry_on_transient_error_eventually_succeeds() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |_ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                let attempt = seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                if attempt < 2 {
                    Err(provider_error("transient", 503))
                } else {
                    Ok(success_result("recovered"))
                }
            }
        },
        None,
    );
    config.max_retries = 3;

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let response = pipeline.call(base_input()).await;

    assert!(response.success);
    assert_eq!(response.content, "recovered");
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn half_open_recovery_counts_each_retry_sequence_once() {
    let attempts = Arc::new(Mutex::new(HashMap::<String, usize>::new()));
    let opening_call = Arc::new(Mutex::new(true));

    let mut config = fast_pipeline_config(
        move |ctx: DispatchContext| {
            let attempts = attempts.clone();
            let opening_call = opening_call.clone();
            async move {
                let request_id = ctx.messages[0]["content"]
                    .as_str()
                    .expect("message content should be a string")
                    .to_owned();

                let mut is_opening_call = opening_call.lock().expect("opening flag mutex poisoned");
                if *is_opening_call {
                    *is_opening_call = false;
                    return Err(provider_error("open the breaker", 503));
                }
                drop(is_opening_call);

                let mut attempts = attempts.lock().expect("attempts mutex poisoned");
                let attempt = attempts.entry(request_id).or_insert(0);
                *attempt += 1;
                if *attempt == 1 {
                    Err(provider_error("transient", 503))
                } else {
                    Ok(success_result("recovered"))
                }
            }
        },
        None,
    );
    config.max_retries = 1;
    config.circuit_breaker_threshold = 1;
    config.circuit_breaker_cooldown = Duration::from_millis(10);

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let opened = pipeline
        .call(CallInput {
            messages: vec![json!({"role": "user", "content": "open-breaker"})],
            singleflight_key: Some("open-breaker".to_owned()),
            ..base_input()
        })
        .await;
    assert!(!opened.success);
    tokio::time::sleep(Duration::from_millis(20)).await;

    for index in 0..5 {
        let response = pipeline
            .call(CallInput {
                messages: vec![json!({"role": "user", "content": format!("recover-{index}")})],
                singleflight_key: Some(format!("recover-{index}")),
                ..base_input()
            })
            .await;
        assert!(response.success, "recovery call {index} should succeed");
    }

    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", ""),
        Some(CircuitState::HalfOpen)
    );
}

#[tokio::test]
async fn half_open_failed_sequences_still_count_once_per_admitted_execution() {
    let attempts = Arc::new(Mutex::new(HashMap::<String, usize>::new()));
    let opening_call = Arc::new(Mutex::new(true));

    let mut config = fast_pipeline_config(
        move |ctx: DispatchContext| {
            let attempts = attempts.clone();
            let opening_call = opening_call.clone();
            async move {
                let request_id = ctx.messages[0]["content"]
                    .as_str()
                    .expect("message content should be a string")
                    .to_owned();

                let mut is_opening_call = opening_call.lock().expect("opening flag mutex poisoned");
                if *is_opening_call {
                    *is_opening_call = false;
                    return Err(provider_error("open the breaker", 503));
                }
                drop(is_opening_call);

                let mut attempts = attempts.lock().expect("attempts mutex poisoned");
                let attempt = attempts.entry(request_id).or_insert(0);
                *attempt += 1;
                Err(provider_error(
                    &format!("still failing attempt {}", *attempt),
                    503,
                ))
            }
        },
        None,
    );
    config.max_retries = 1;
    config.circuit_breaker_threshold = 1;
    config.circuit_breaker_cooldown = Duration::from_millis(10);

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let opened = pipeline
        .call(CallInput {
            messages: vec![json!({"role": "user", "content": "open-breaker"})],
            singleflight_key: Some("open-breaker".to_owned()),
            ..base_input()
        })
        .await;
    assert!(!opened.success);
    tokio::time::sleep(Duration::from_millis(20)).await;

    for index in 0..5 {
        let response = pipeline
            .call(CallInput {
                messages: vec![
                    json!({"role": "user", "content": format!("still-failing-{index}")}),
                ],
                singleflight_key: Some(format!("still-failing-{index}")),
                ..base_input()
            })
            .await;
        assert!(
            !response.success,
            "failed half-open call {index} should fail"
        );
    }

    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", ""),
        Some(CircuitState::HalfOpen)
    );
}

#[tokio::test]
async fn circuit_breaker_state_is_scoped_by_effective_base_url() {
    let mut config = fast_pipeline_config(
        move |ctx: DispatchContext| async move {
            let base_url = ctx.kwargs.get("base_url").cloned().unwrap_or(Value::Null);
            if base_url == json!("https://bad.example/v1") {
                Err(provider_error("server error", 500))
            } else {
                Ok(success_result(
                    base_url.as_str().expect("base_url should be a string"),
                ))
            }
        },
        None,
    );
    config.max_retries = 0;
    config.circuit_breaker_threshold = 1;
    config
        .transform_kwargs_overrides
        .insert("openai".to_owned(), |ctx, mut kwargs| {
            kwargs.insert(
                "base_url".to_owned(),
                json!(format!("{}/v1", ctx.base_url.as_deref().unwrap_or(""))),
            );
            Ok(kwargs)
        });

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let bad = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "baseUrl": "https://bad.example",
                "common": { "enableThinking": true }
            }),
            messages: vec![json!({"role": "user", "content": "bad"})],
            singleflight_key: Some("bad-endpoint".to_owned()),
        })
        .await;
    let good = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "baseUrl": "https://good.example",
                "common": { "enableThinking": true }
            }),
            messages: vec![json!({"role": "user", "content": "good"})],
            singleflight_key: Some("good-endpoint".to_owned()),
        })
        .await;

    assert!(!bad.success);
    assert!(good.success);
    assert_eq!(good.content, "https://good.example/v1");
    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", "https://bad.example/v1"),
        Some(CircuitState::Open)
    );
    assert_eq!(
        pipeline.get_circuit_breaker_state("openai", "https://good.example/v1"),
        Some(CircuitState::Closed)
    );
}

#[tokio::test]
async fn response_cache_key_uses_effective_base_url() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();
    let cache = memory_cache();

    let mut config = fast_pipeline_config(
        move |ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                Ok(success_result(
                    ctx.kwargs["base_url"]
                        .as_str()
                        .expect("base_url should be present"),
                ))
            }
        },
        Some(cache),
    );
    config
        .transform_kwargs_overrides
        .insert("openai".to_owned(), |ctx, mut kwargs| {
            kwargs.insert(
                "base_url".to_owned(),
                json!(format!("{}/v1", ctx.base_url.as_deref().unwrap_or(""))),
            );
            Ok(kwargs)
        });

    let pipeline = CallPipeline::new(config).expect("pipeline should construct");
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let messages = vec![json!({"role": "user", "content": "cache"})];
    let a1 = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "baseUrl": "https://a.example",
                "caching": { "strategy": "memory" },
                "common": { "enableThinking": true }
            }),
            messages: messages.clone(),
            singleflight_key: None,
        })
        .await;
    let b1 = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "baseUrl": "https://b.example",
                "caching": { "strategy": "memory" },
                "common": { "enableThinking": true }
            }),
            messages: messages.clone(),
            singleflight_key: None,
        })
        .await;
    let a2 = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": "gpt-4.1-mini",
                "baseUrl": "https://a.example",
                "caching": { "strategy": "memory" },
                "common": { "enableThinking": true }
            }),
            messages,
            singleflight_key: None,
        })
        .await;

    assert!(a1.success);
    assert!(b1.success);
    assert!(a2.success);
    assert_eq!(a1.content, "https://a.example/v1");
    assert_eq!(b1.content, "https://b.example/v1");
    assert_eq!(a2.cache_hit, Some(CacheHitTier::L1));
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn singleflight_fallback_key_includes_effective_base_url() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let mut config = fast_pipeline_config(
        move |ctx: DispatchContext| {
            let seen_dispatch_calls = seen_dispatch_calls.clone();
            async move {
                seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                tokio::time::sleep(Duration::from_millis(50)).await;
                Ok(success_result(
                    ctx.kwargs["base_url"]
                        .as_str()
                        .expect("base_url should be present"),
                ))
            }
        },
        None,
    );
    config
        .transform_kwargs_overrides
        .insert("openai".to_owned(), |ctx, mut kwargs| {
            kwargs.insert(
                "base_url".to_owned(),
                json!(ctx.base_url.as_deref().unwrap_or("")),
            );
            Ok(kwargs)
        });

    let pipeline = Arc::new(CallPipeline::new(config).expect("pipeline should construct"));
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let messages = vec![json!({"role": "user", "content": "same"})];
    let first_pipeline = pipeline.clone();
    let second_pipeline = pipeline.clone();
    let first = tokio::spawn(async move {
        first_pipeline
            .call(CallInput {
                config: json!({
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "baseUrl": "https://a.example",
                    "common": { "enableThinking": true }
                }),
                messages: messages.clone(),
                singleflight_key: None,
            })
            .await
    });
    let second = tokio::spawn(async move {
        second_pipeline
            .call(CallInput {
                config: json!({
                    "provider": "openai",
                    "model": "gpt-4.1-mini",
                    "baseUrl": "https://b.example",
                    "common": { "enableThinking": true }
                }),
                messages: vec![json!({"role": "user", "content": "same"})],
                singleflight_key: None,
            })
            .await
    });

    let first_response = first.await.expect("first task should join");
    let second_response = second.await.expect("second task should join");

    assert!(first_response.success);
    assert!(second_response.success);
    assert_eq!(first_response.content, "https://a.example");
    assert_eq!(second_response.content, "https://b.example");
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 2);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn singleflight_fallback_key_canonicalizes_provider_options() {
    let dispatch_calls = Arc::new(AtomicUsize::new(0));
    let seen_dispatch_calls = dispatch_calls.clone();

    let pipeline = Arc::new(
        CallPipeline::new(fast_pipeline_config(
            move |_ctx: DispatchContext| {
                let seen_dispatch_calls = seen_dispatch_calls.clone();
                async move {
                    seen_dispatch_calls.fetch_add(1, Ordering::SeqCst);
                    tokio::time::sleep(Duration::from_millis(50)).await;
                    Ok(success_result("ok"))
                }
            },
            None,
        ))
        .expect("pipeline should construct"),
    );
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec!["key-a".to_owned()]).expect("key pool should construct"),
    );

    let config_a = json!({
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "providerOptions": { "alpha": 1, "nested": { "x": 1, "y": 2 } },
        "common": { "enableThinking": true }
    });
    let config_b = json!({
        "provider": "openai",
        "model": "gpt-4.1-mini",
        "providerOptions": { "nested": { "y": 2, "x": 1 }, "alpha": 1 },
        "common": { "enableThinking": true }
    });

    let first_pipeline = pipeline.clone();
    let second_pipeline = pipeline.clone();
    let first = tokio::spawn(async move {
        first_pipeline
            .call(CallInput {
                config: config_a,
                messages: vec![json!({"role": "user", "content": "same"})],
                singleflight_key: None,
            })
            .await
    });
    let second = tokio::spawn(async move {
        second_pipeline
            .call(CallInput {
                config: config_b,
                messages: vec![json!({"role": "user", "content": "same"})],
                singleflight_key: None,
            })
            .await
    });

    let first_response = first.await.expect("first task should join");
    let second_response = second.await.expect("second task should join");

    assert!(first_response.success);
    assert!(second_response.success);
    assert_eq!(dispatch_calls.load(Ordering::SeqCst), 1);
}

#[tokio::test]
async fn google_enable_thinking_keeps_provider_default_budget() {
    let seen_kwargs = Arc::new(Mutex::new(None::<serde_json::Map<String, Value>>));
    let kwargs_slot = seen_kwargs.clone();

    let pipeline = CallPipeline::new(fast_pipeline_config(
        move |ctx: DispatchContext| {
            let kwargs_slot = kwargs_slot.clone();
            async move {
                *kwargs_slot
                    .lock()
                    .expect("kwargs mutex should remain available") = Some(ctx.kwargs.clone());
                Ok(ProviderResult {
                    content: "ok".to_owned(),
                    model: "gemini-2.5-pro".to_owned(),
                    usage: LlmUsage {
                        input_tokens: 11,
                        output_tokens: 7,
                        total_tokens: 18,
                    },
                    headers: None,
                    tool_calls: None,
                })
            }
        },
        None,
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "google",
        KeyPool::new(vec!["google-key".to_owned()]).expect("key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "google",
                "model": "gemini-2.5-pro",
                "common": { "enableThinking": true }
            }),
            messages: vec![json!({"role": "user", "content": "think"})],
            singleflight_key: Some("google-thinking".to_owned()),
        })
        .await;

    assert!(response.success);
    let seen_kwargs = seen_kwargs
        .lock()
        .expect("kwargs mutex should remain available")
        .clone()
        .expect("dispatch should have run once");
    assert!(!seen_kwargs.contains_key("thinking_config"));
    assert!(!seen_kwargs.contains_key("thinkingConfig"));
}
