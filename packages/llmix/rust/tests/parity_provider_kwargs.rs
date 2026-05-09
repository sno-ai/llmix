use llmix_rs::{
    apply_transform_kwargs, gemini_transform_kwargs, is_reasoning_model, openai_transform_kwargs,
    openrouter_transform_kwargs, provider_kwargs_callback, sno_gpu_transform_kwargs,
    TransformKwargsContext, PROVIDER_KWARGS_REGISTRY,
};
use serde_json::{json, Map, Value};

fn object(value: Value) -> Map<String, Value> {
    value.as_object().expect("object").clone()
}

fn ctx(model: &str, provider: &str) -> TransformKwargsContext {
    TransformKwargsContext {
        model: model.to_string(),
        provider: provider.to_string(),
        ..Default::default()
    }
}

#[test]
fn apply_transform_kwargs_without_callback_returns_input_unchanged() {
    let kwargs = object(json!({ "temperature": 0.7, "top_p": 0.9 }));
    let result = apply_transform_kwargs(&ctx("gpt-4o", "openai"), kwargs.clone(), None).unwrap();
    assert_eq!(result, kwargs);
}

#[test]
fn reasoning_model_detection_matches_current_contract() {
    assert!(is_reasoning_model("o3-mini"));
    assert!(is_reasoning_model("o2"));
    assert!(is_reasoning_model("gpt-5-chat-latest"));
    assert!(is_reasoning_model("codex-mini"));
    assert!(is_reasoning_model("computer-use-preview"));
    assert!(!is_reasoning_model("gpt-4o"));
}

#[test]
fn openai_standard_models_keep_sampling_parameters() {
    let kwargs = object(json!({
        "temperature": 0.5,
        "top_p": 0.8,
        "max_tokens": 100
    }));

    let result = openai_transform_kwargs(&ctx("gpt-4o", "openai"), kwargs.clone()).unwrap();
    assert_eq!(result, kwargs);
}

#[test]
fn openai_reasoning_models_strip_sampling_and_rename_max_tokens() {
    let kwargs = object(json!({
        "temperature": 0.5,
        "top_p": 0.8,
        "max_tokens": 100
    }));

    let result = openai_transform_kwargs(&ctx("o3-mini", "openai"), kwargs).unwrap();
    assert!(!result.contains_key("temperature"));
    assert!(!result.contains_key("top_p"));
    assert!(!result.contains_key("max_tokens"));
    assert_eq!(result.get("max_completion_tokens"), Some(&json!(100)));
}

#[test]
fn openai_reasoning_models_respect_existing_camel_completion_target() {
    let kwargs = object(json!({
        "temperature": 0.5,
        "topP": 0.8,
        "maxTokens": 256
    }));

    let result = openai_transform_kwargs(&ctx("o5-mini", "openai"), kwargs).unwrap();
    assert!(!result.contains_key("temperature"));
    assert!(!result.contains_key("topP"));
    assert!(!result.contains_key("maxTokens"));
    assert_eq!(result.get("max_completion_tokens"), Some(&json!(256)));
}

#[test]
fn openai_reasoning_models_do_not_override_existing_completion_cap() {
    let kwargs = object(json!({
        "max_tokens": 100,
        "maxCompletionTokens": 200
    }));

    let result = openai_transform_kwargs(&ctx("gpt-5", "openai"), kwargs).unwrap();
    assert_eq!(result.get("maxCompletionTokens"), Some(&json!(200)));
    assert!(!result.contains_key("max_tokens"));
}

#[test]
fn openai_reasoning_models_remove_legacy_aliases_when_completion_cap_exists() {
    let kwargs = object(json!({
        "max_tokens": 100,
        "maxTokens": 150,
        "max_completion_tokens": 200
    }));

    let result = openai_transform_kwargs(&ctx("gpt-5-mini", "openai"), kwargs).unwrap();
    assert_eq!(result.get("max_completion_tokens"), Some(&json!(200)));
    assert!(!result.contains_key("max_tokens"));
    assert!(!result.contains_key("maxTokens"));
}

#[test]
fn openrouter_injects_default_provider_sort_without_overwriting_existing_provider() {
    let result = openrouter_transform_kwargs(
        &ctx("deepseek/deepseek-chat", "deepseek"),
        object(json!({ "max_tokens": 100 })),
    )
    .unwrap();
    assert_eq!(
        result.get("extra_body"),
        Some(&json!({ "provider": { "sort": "price" } }))
    );
    assert_eq!(result.get("max_tokens"), Some(&json!(100)));

    let result_with_extra = openrouter_transform_kwargs(
        &ctx("deepseek/deepseek-chat", "deepseek"),
        object(json!({ "extra_body": { "custom": "value" } })),
    )
    .unwrap();
    assert_eq!(
        result_with_extra.get("extra_body"),
        Some(&json!({ "custom": "value", "provider": { "sort": "price" } }))
    );

    let result_existing_provider = openrouter_transform_kwargs(
        &ctx("deepseek/deepseek-chat", "deepseek"),
        object(json!({ "extra_body": { "provider": { "sort": "latency" } } })),
    )
    .unwrap();
    assert_eq!(
        result_existing_provider.get("extra_body"),
        Some(&json!({ "provider": { "sort": "latency" } }))
    );
}

#[test]
fn gemini_defaults_thinking_budget_to_zero() {
    let result = gemini_transform_kwargs(
        &ctx("gemini-2.5-pro", "google"),
        object(json!({ "max_tokens": 100 })),
    )
    .unwrap();
    assert_eq!(
        result.get("thinking_config"),
        Some(&json!({ "thinking_budget": 0 }))
    );
}

#[test]
fn gemini_skips_default_injection_when_enable_thinking_is_true_without_explicit_budget() {
    let mut context = ctx("gemini-2.5-pro", "google");
    context.enable_thinking = Some(true);

    let result = gemini_transform_kwargs(&context, Map::new()).unwrap();
    assert!(!result.contains_key("thinking_config"));
    assert!(!result.contains_key("thinkingConfig"));
}

#[test]
fn gemini_uses_nested_or_legacy_budget_from_provider_options() {
    let mut nested_ctx = ctx("gemini-2.5-pro", "google");
    nested_ctx.provider_options = Some(object(json!({
        "google": { "thinking_config": { "thinking_budget": 4096 } }
    })));
    let nested_result = gemini_transform_kwargs(&nested_ctx, Map::new()).unwrap();
    assert_eq!(
        nested_result.get("thinking_config"),
        Some(&json!({ "thinking_budget": 4096 }))
    );

    let mut legacy_ctx = ctx("gemini-2.5-pro", "google");
    legacy_ctx.provider_options = Some(object(json!({
        "google": { "thinking_budget": 1024 }
    })));
    let legacy_result = gemini_transform_kwargs(&legacy_ctx, Map::new()).unwrap();
    assert_eq!(
        legacy_result.get("thinking_config"),
        Some(&json!({ "thinking_budget": 1024 }))
    );
}

#[test]
fn gemini_explicit_zero_budget_wins_over_enable_thinking() {
    let mut context = ctx("gemini-2.5-pro", "google");
    context.enable_thinking = Some(true);
    context.provider_options = Some(object(json!({
        "google": { "thinking_config": { "thinking_budget": 0 } }
    })));

    let result = gemini_transform_kwargs(&context, Map::new()).unwrap();
    assert_eq!(
        result.get("thinking_config"),
        Some(&json!({ "thinking_budget": 0 }))
    );
}

#[test]
fn gemini_preserves_existing_budget_and_supports_camel_case_aliases() {
    let mut context = ctx("gemini-2.5-pro", "google");
    context.provider_options = Some(object(json!({
        "google": { "thinkingConfig": { "thinkingBudget": 2048 } }
    })));

    let existing = object(json!({
        "thinkingConfig": { "thinkingBudget": 999 }
    }));
    let result_existing = gemini_transform_kwargs(&context, existing.clone()).unwrap();
    assert_eq!(result_existing, existing);

    let result_injected = gemini_transform_kwargs(&context, Map::new()).unwrap();
    assert_eq!(
        result_injected.get("thinkingConfig"),
        Some(&json!({ "thinkingBudget": 2048 }))
    );
}

#[test]
fn gemini_merges_provider_budget_into_existing_snake_case_config() {
    let mut context = ctx("gemini-2.5-pro", "google");
    context.provider_options = Some(object(json!({
        "google": { "thinkingConfig": { "thinkingBudget": 2048 } }
    })));
    let kwargs = object(json!({
        "thinking_config": { "include_thoughts": true }
    }));

    let result = gemini_transform_kwargs(&context, kwargs).unwrap();

    assert_eq!(
        result.get("thinking_config"),
        Some(&json!({ "include_thoughts": true, "thinking_budget": 2048 }))
    );
    assert!(!result.contains_key("thinkingConfig"));
}

#[test]
fn sno_gpu_constructs_base_url_and_validates_inputs() {
    let mut context = ctx("qwen3.6-27b-extract", "sno-gpu");
    context.base_url = Some("https://gpu.example.com".to_string());
    context.provider_options = Some(object(json!({
        "sno-gpu": { "gpu_path": "extract" }
    })));

    let result = sno_gpu_transform_kwargs(&context, Map::new()).unwrap();
    assert_eq!(
        result.get("base_url"),
        Some(&json!("https://gpu.example.com/extract/v1"))
    );

    let mut no_path_context = context.clone();
    no_path_context.provider_options = None;
    let no_path_result = sno_gpu_transform_kwargs(&no_path_context, Map::new()).unwrap();
    assert_eq!(
        no_path_result.get("base_url"),
        Some(&json!("https://gpu.example.com/v1"))
    );

    let mut with_v1_context = context.clone();
    with_v1_context.model = "qwen3.6-27b-reason".to_string();
    with_v1_context.base_url = Some("https://gpu.example.com/v1".to_string());
    with_v1_context.provider_options = Some(object(json!({
        "sno-gpu": { "gpuPath": "reason" }
    })));
    let with_v1_result = sno_gpu_transform_kwargs(&with_v1_context, Map::new()).unwrap();
    assert_eq!(
        with_v1_result.get("base_url"),
        Some(&json!("https://gpu.example.com/reason/v1"))
    );
}

#[test]
fn sno_gpu_uses_camel_output_key_when_input_kwargs_do() {
    let mut context = ctx("qwen3.6-27b-extract", "sno-gpu");
    context.base_url = Some("https://gpu.example.com".to_string());
    context.provider_options = Some(object(json!({
        "sno-gpu": { "gpuPath": "extract" }
    })));

    let result =
        sno_gpu_transform_kwargs(&context, object(json!({ "baseUrl": "ignored" }))).unwrap();
    assert_eq!(
        result.get("baseUrl"),
        Some(&json!("https://gpu.example.com/extract/v1"))
    );
}

#[test]
fn sno_gpu_errors_on_missing_base_url_and_invalid_paths() {
    let error =
        sno_gpu_transform_kwargs(&ctx("qwen3.6-27b-reason", "sno-gpu"), Map::new()).unwrap_err();
    assert_eq!(
        error.to_string(),
        "sno-gpu provider requires a non-empty base_url"
    );

    let mut context = ctx("qwen3.6-27b-reason", "sno-gpu");
    context.base_url = Some("https://gpu.example.com".to_string());
    context.provider_options = Some(object(json!({
        "sno-gpu": { "gpu_path": "../escape" }
    })));
    let invalid = sno_gpu_transform_kwargs(&context, Map::new()).unwrap_err();
    assert_eq!(invalid.to_string(), "Invalid gpu_path: \"../escape\"");
}

#[test]
fn registry_contains_expected_entries() {
    assert_eq!(PROVIDER_KWARGS_REGISTRY.len(), 5);
    assert!(provider_kwargs_callback("openai").is_some());
    assert!(provider_kwargs_callback("deepseek").is_some());
    assert!(provider_kwargs_callback("google").is_some());
    assert!(provider_kwargs_callback("gemini").is_some());
    assert!(provider_kwargs_callback("sno-gpu").is_some());
    assert!(provider_kwargs_callback("unknown").is_none());
}
