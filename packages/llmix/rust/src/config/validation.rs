use super::*;

pub(super) fn validate_runtime_config(
    file_path: &Path,
    config: &Map<String, Value>,
) -> LlmixResult<()> {
    let provider = require_non_empty_string(config.get("provider"), "provider", file_path)?;
    if !VALID_PROVIDERS.contains(&provider) {
        return Err(InvalidConfigError {
            message: format!(
                "Invalid provider {provider:?} in {}. Expected one of: {}",
                file_path.display(),
                VALID_PROVIDERS.join(", ")
            ),
        }
        .into());
    }
    require_non_empty_string(config.get("model"), "model", file_path)?;

    if let Some(common) = config.get("common") {
        let common = expect_object(common, "common", file_path)?;
        validate_optional_number_range(common, "temperature", 0.0, 2.0, file_path)?;
        validate_optional_number_range(common, "top_p", 0.0, 1.0, file_path)?;
        validate_optional_positive_integer(common, "max_output_tokens", file_path)?;
        validate_optional_positive_integer(common, "top_k", file_path)?;
        validate_optional_nonnegative_integer(common, "max_retries", file_path)?;
    }

    if let Some(caching) = config.get("caching") {
        let caching = expect_object(caching, "caching", file_path)?;
        if let Some(strategy) = caching.get("strategy") {
            let strategy = expect_non_empty_string(strategy, "caching.strategy", file_path)?;
            if !VALID_CACHE_STRATEGIES.contains(&strategy) {
                return Err(InvalidConfigError {
                    message: format!(
                        "Invalid caching.strategy {strategy:?} in {}. Expected one of: {}",
                        file_path.display(),
                        VALID_CACHE_STRATEGIES.join(", ")
                    ),
                }
                .into());
            }
        }
        validate_optional_positive_integer(caching, "ttl", file_path)?;
        validate_optional_positive_integer(caching, "max_items", file_path)?;
    }

    if let Some(timeout) = config.get("timeout") {
        let timeout = expect_object(timeout, "timeout", file_path)?;
        validate_optional_positive_number(timeout, "total_time", file_path)?;
        validate_optional_positive_number(timeout, "stream_first_chunk_time", file_path)?;
    }

    if let Some(provider_options) = config.get("provider_options") {
        let provider_options = expect_object(provider_options, "provider_options", file_path)?;
        if let Some(openai) = provider_options.get("openai") {
            let openai = expect_object(openai, "provider_options.openai", file_path)?;
            if let Some(reasoning_effort) = openai.get("reasoning_effort") {
                let reasoning_effort = expect_non_empty_string(
                    reasoning_effort,
                    "provider_options.openai.reasoning_effort",
                    file_path,
                )?;
                if !OPENAI_REASONING_EFFORTS.contains(&reasoning_effort) {
                    return Err(InvalidConfigError {
                        message: format!(
                            "Invalid provider_options.openai.reasoning_effort {reasoning_effort:?} in {}",
                            file_path.display()
                        ),
                    }
                    .into());
                }
            }
        }
    }

    Ok(())
}

pub(super) fn normalize_config_keys(value: Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .into_iter()
                .map(|(key, value)| {
                    (
                        camel_to_snake_key(&key).to_string(),
                        normalize_config_keys(value),
                    )
                })
                .collect(),
        ),
        Value::Array(values) => {
            Value::Array(values.into_iter().map(normalize_config_keys).collect())
        }
        other => other,
    }
}

pub(super) fn require_object<'a>(
    value: Option<&'a Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a Map<String, Value>> {
    let value = value.ok_or_else(|| InvalidConfigError {
        message: format!(
            "Missing required field '{field}' in {}",
            file_path.display()
        ),
    })?;
    expect_object(value, field, file_path)
}

fn expect_object<'a>(
    value: &'a Value,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a Map<String, Value>> {
    match value {
        Value::Object(object) => Ok(object),
        other => Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be an object, got {}",
                file_path.display(),
                json_type_name(other)
            ),
        }
        .into()),
    }
}

pub(super) fn require_non_empty_string<'a>(
    value: Option<&'a Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a str> {
    let value = value.ok_or_else(|| InvalidConfigError {
        message: format!(
            "Missing required field '{field}' in {}",
            file_path.display()
        ),
    })?;
    expect_non_empty_string(value, field, file_path)
}

fn expect_non_empty_string<'a>(
    value: &'a Value,
    field: &str,
    file_path: &Path,
) -> LlmixResult<&'a str> {
    match value.as_str() {
        Some(value) if !value.is_empty() => Ok(value),
        _ => Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a non-empty string",
                file_path.display()
            ),
        }
        .into()),
    }
}

fn validate_optional_number_range(
    object: &Map<String, Value>,
    field: &str,
    min: f64,
    max: f64,
    file_path: &Path,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_f64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a number",
                file_path.display()
            ),
        }
        .into());
    };
    if !(min..=max).contains(&number) {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be between {min} and {max}",
                file_path.display()
            ),
        }
        .into());
    }
    Ok(())
}

fn validate_optional_positive_number(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_f64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be a number",
                file_path.display()
            ),
        }
        .into());
    };
    if number <= 0.0 {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be positive",
                file_path.display()
            ),
        }
        .into());
    }
    Ok(())
}

fn validate_optional_positive_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    validate_optional_integer(object, field, file_path, |number| number > 0, "positive")
}

fn validate_optional_nonnegative_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
) -> LlmixResult<()> {
    validate_optional_integer(
        object,
        field,
        file_path,
        |number| number >= 0,
        "non-negative",
    )
}

fn validate_optional_integer(
    object: &Map<String, Value>,
    field: &str,
    file_path: &Path,
    predicate: impl FnOnce(i64) -> bool,
    label: &str,
) -> LlmixResult<()> {
    let Some(value) = object.get(field) else {
        return Ok(());
    };
    let Some(number) = value.as_i64() else {
        return Err(InvalidConfigError {
            message: format!(
                "Field '{field}' in {} must be an integer",
                file_path.display()
            ),
        }
        .into());
    };
    if !predicate(number) {
        return Err(InvalidConfigError {
            message: format!("Field '{field}' in {} must be {label}", file_path.display()),
        }
        .into());
    }
    Ok(())
}

pub(super) fn camel_to_snake_key(key: &str) -> &str {
    match key {
        "maxOutputTokens" => "max_output_tokens",
        "maxRetries" => "max_retries",
        "topP" => "top_p",
        "topK" => "top_k",
        "presencePenalty" => "presence_penalty",
        "frequencyPenalty" => "frequency_penalty",
        "stopSequences" => "stop_sequences",
        "totalTime" => "total_time",
        "streamFirstChunkTime" => "stream_first_chunk_time",
        "providerOptions" => "provider_options",
        "bypassGateway" => "bypass_gateway",
        "configId" => "config_id",
        "enableThinking" => "enable_thinking",
        "keepThinkingOutput" => "keep_thinking_output",
        "thinkingBudget" => "thinking_budget",
        "reasoningEffort" => "reasoning_effort",
        "textVerbosity" => "text_verbosity",
        "structuredOutputs" => "structured_outputs",
        "parallelToolCalls" => "parallel_tool_calls",
        "logitBias" => "logit_bias",
        "strictJsonSchema" => "strict_json_schema",
        "maxCompletionTokens" => "max_completion_tokens",
        "serviceTier" => "service_tier",
        "promptCacheKey" => "prompt_cache_key",
        "promptCacheRetention" => "prompt_cache_retention",
        "gpuPath" => "gpu_path",
        "maxItems" => "max_items",
        "safetyIdentifier" => "safety_identifier",
        "budgetTokens" => "budget_tokens",
        "disableParallelToolUse" => "disable_parallel_tool_use",
        "sendReasoning" => "send_reasoning",
        "toolStreaming" => "tool_streaming",
        "structuredOutputMode" => "structured_output_mode",
        "thinkingLevel" => "thinking_level",
        "thinkingConfig" => "thinking_config",
        "includeThoughts" => "include_thoughts",
        "cachedContent" => "cached_content",
        "safetySettings" => "safety_settings",
        "responseModalities" => "response_modalities",
        "cacheControl" => "cache_control",
        other => other,
    }
}

pub(super) fn json_type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "null",
        Value::Bool(_) => "bool",
        Value::Number(_) => "number",
        Value::String(_) => "str",
        Value::Array(_) => "array",
        Value::Object(_) => "dict",
    }
}
