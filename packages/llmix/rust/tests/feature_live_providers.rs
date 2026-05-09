#![cfg(any(
    feature = "providers-openai",
    feature = "providers-sno-gpu",
    feature = "providers-anthropic",
    feature = "providers-gemini"
))]

use llmix_rs::{CallInput, CallPipeline, CallResponse, DispatchFn, KeyPool, PipelineConfig};
use serde_json::json;
use std::env;

#[cfg(feature = "providers-anthropic")]
use llmix_rs::AnthropicChatHelper;
#[cfg(feature = "providers-gemini")]
use llmix_rs::GeminiChatHelper;
#[cfg(feature = "providers-openai")]
use llmix_rs::OpenAiChatHelper;
#[cfg(feature = "providers-sno-gpu")]
use llmix_rs::SnoGpuChatHelper;

const LIVE_TESTS_FLAG: &str = "LLMIX_RUN_LIVE_TESTS";

fn live_tests_enabled() -> bool {
    env::var(LIVE_TESTS_FLAG)
        .ok()
        .map(|value| value.trim().to_ascii_lowercase())
        .map(|value| matches!(value.as_str(), "1" | "true" | "yes"))
        .unwrap_or(false)
}

fn require_live_env(names: &[&str]) -> Option<Vec<String>> {
    if !live_tests_enabled() {
        println!("skipping live provider test; set {LIVE_TESTS_FLAG}=1 to enable external calls");
        return None;
    }

    let mut values = Vec::with_capacity(names.len());
    for name in names {
        match env::var(name)
            .ok()
            .map(|value| value.trim().to_string())
            .filter(|value| !value.is_empty())
        {
            Some(value) => values.push(value),
            None => {
                println!("skipping live provider test; missing required env var {name}");
                return None;
            }
        }
    }

    Some(values)
}

fn require_live_env_groups(groups: &[&[&str]]) -> Option<Vec<String>> {
    if !live_tests_enabled() {
        println!("skipping live provider test; set {LIVE_TESTS_FLAG}=1 to enable external calls");
        return None;
    }

    let mut values = Vec::with_capacity(groups.len());
    for group in groups {
        let maybe_value = group.iter().find_map(|name| {
            env::var(name)
                .ok()
                .map(|value| value.trim().to_string())
                .filter(|value| !value.is_empty())
                .map(|value| ((*name).to_string(), value))
        });

        match maybe_value {
            Some((name, value)) => {
                println!("using live env {name}");
                values.push(value);
            }
            None => {
                println!(
                    "skipping live provider test; missing one of required env vars: {}",
                    group.join(", ")
                );
                return None;
            }
        }
    }

    Some(values)
}

fn env_or(name: &str, fallback: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| fallback.to_string())
}

fn live_pipeline<D>(dispatch: D) -> CallPipeline
where
    D: DispatchFn + 'static,
{
    let mut config = PipelineConfig::new(dispatch);
    config.max_retries = 0;
    config.retry_base_ms = 0;
    config.retry_max_delay_ms = 0;
    config.retry_jitter_ms = 0;
    config.retry_max_retry_after_ms = 0;
    config.semaphore_initial = 4;
    config.semaphore_min = 1;
    CallPipeline::new(config).expect("live pipeline should construct")
}

fn assert_live_success(label: &str, response: &CallResponse) {
    assert!(response.success, "{label} failed: {:?}", response.error);
    assert!(
        response.error.is_none(),
        "{label} returned unexpected error: {:?}",
        response.error
    );
    assert!(
        !response.content.trim().is_empty(),
        "{label} returned empty content"
    );
    assert!(
        !response.model.trim().is_empty(),
        "{label} returned empty model"
    );
    assert!(
        response.usage.total_tokens > 0,
        "{label} total tokens should be > 0"
    );
    assert_eq!(
        response.usage.total_tokens,
        response
            .usage
            .input_tokens
            .saturating_add(response.usage.output_tokens),
        "{label} usage totals should be self-consistent"
    );
}

#[cfg(feature = "providers-openai")]
#[tokio::test]
async fn openai_live_simple_completion_via_pipeline() {
    let Some(envs) = require_live_env(&["OPENAI_API_KEY"]) else {
        return;
    };
    let api_key = envs[0].clone();
    let model = env_or("OPENAI_MODEL", "gpt-4o-mini");

    let pipeline = live_pipeline(OpenAiChatHelper::new());
    pipeline.set_key_pool(
        "openai",
        KeyPool::new(vec![api_key]).expect("openai key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "openai",
                "model": model,
                "common": {
                    "temperature": 0,
                    "maxOutputTokens": 32
                }
            }),
            messages: vec![json!({
                "role": "user",
                "content": "What is 2 + 2? Reply with just the number."
            })],
            singleflight_key: None,
        })
        .await;

    assert_live_success("openai smoke", &response);
    assert!(
        response.content.contains('4'),
        "openai smoke should mention 4, got {:?}",
        response.content
    );
}

#[cfg(feature = "providers-anthropic")]
#[tokio::test]
async fn anthropic_live_system_message_via_pipeline() {
    let Some(envs) = require_live_env(&["ANTHROPIC_API_KEY"]) else {
        return;
    };
    let api_key = envs[0].clone();
    let model = env_or("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001");

    let pipeline = live_pipeline(AnthropicChatHelper::new());
    pipeline.set_key_pool(
        "anthropic",
        KeyPool::new(vec![api_key]).expect("anthropic key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "anthropic",
                "model": model,
                "common": {
                    "temperature": 0,
                    "maxOutputTokens": 48
                }
            }),
            messages: vec![
                json!({
                    "role": "system",
                    "content": "Respond in exactly three lowercase words."
                }),
                json!({
                    "role": "user",
                    "content": "Describe the sky."
                }),
            ],
            singleflight_key: None,
        })
        .await;

    assert_live_success("anthropic system extraction", &response);
    let word_count = response.content.split_whitespace().count();
    assert!(
        (2..=4).contains(&word_count),
        "anthropic response should stay close to three words, got {:?}",
        response.content
    );
}

#[cfg(feature = "providers-gemini")]
#[tokio::test]
async fn gemini_live_continuation_via_pipeline() {
    let Some(envs) = require_live_env(&["GEMINI_API_KEY"]) else {
        return;
    };
    let api_key = envs[0].clone();
    let model = env_or("GEMINI_MODEL", "gemini-2.5-flash");

    let pipeline = live_pipeline(GeminiChatHelper::new());
    pipeline.set_key_pool(
        "gemini",
        KeyPool::new(vec![api_key]).expect("gemini key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "gemini",
                "model": model,
                "common": {
                    "temperature": 0,
                    "maxOutputTokens": 64
                },
                "providerOptions": {
                    "google": {
                        "thinkingBudget": 0
                    }
                }
            }),
            messages: vec![
                json!({
                    "role": "system",
                    "content": "When the prior assistant message ends at 3, continue the sequence with digits and commas only."
                }),
                json!({
                    "role": "user",
                    "content": "Continue counting."
                }),
                json!({
                    "role": "assistant",
                    "content": "1, 2, 3"
                }),
            ],
            singleflight_key: None,
        })
        .await;

    assert_live_success("gemini continuation", &response);
    assert!(
        response.content.contains('4'),
        "gemini continuation should include 4, got {:?}",
        response.content
    );
}

#[cfg(feature = "providers-sno-gpu")]
#[tokio::test]
async fn sno_gpu_live_extract_path_via_pipeline() {
    let Some(envs) = require_live_env_groups(&[
        &["GPU_BASE_URL"],
        &["SNO_LLM_API_KEY", "INTERNAL_SERVICE_SECRET"],
    ]) else {
        return;
    };
    let base_url = envs[0].clone();
    let internal_secret = envs[1].clone();
    let model = env_or("SNO_GPU_MODEL", "qwen3.6-27b-extract");

    let pipeline = live_pipeline(SnoGpuChatHelper::new().with_internal_token(internal_secret));
    pipeline.set_key_pool(
        "sno-gpu",
        KeyPool::new(vec!["not-needed".to_string()]).expect("sno-gpu key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "sno-gpu",
                "model": model,
                "baseUrl": base_url,
                "common": {
                    "temperature": 0,
                    "maxOutputTokens": 48
                },
                "providerOptions": {
                    "sno-gpu": {
                        "gpuPath": "extract",
                        "enableThinking": false
                    }
                }
            }),
            messages: vec![json!({
                "role": "user",
                "content": "What is 2 + 2? Reply with just the number."
            })],
            singleflight_key: None,
        })
        .await;

    assert_live_success("sno-gpu extract", &response);
    assert!(
        response.content.contains('4'),
        "sno-gpu extract should mention 4, got {:?}",
        response.content
    );
    assert!(
        response.thinking_content.is_none(),
        "sno-gpu extract should not surface thinking content when disabled"
    );
}
