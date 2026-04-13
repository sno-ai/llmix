use crate::error::{LlmixError, LlmixResult};
use serde_json::{Map, Value};

pub type TransformKwargsCallback =
    fn(&TransformKwargsContext, Map<String, Value>) -> LlmixResult<Map<String, Value>>;

#[derive(Debug, Clone, PartialEq, Default)]
pub struct TransformKwargsContext {
    pub model: String,
    pub provider: String,
    pub messages: Option<Vec<Value>>,
    pub temperature: Option<f64>,
    pub top_p: Option<f64>,
    pub enable_thinking: Option<bool>,
    pub provider_options: Option<Map<String, Value>>,
    pub base_url: Option<String>,
}

pub const PROVIDER_KWARGS_REGISTRY: [(&str, TransformKwargsCallback); 6] = [
    ("openai", openai_transform_kwargs),
    ("deepseek", openrouter_transform_kwargs),
    ("google", gemini_transform_kwargs),
    ("gemini", gemini_transform_kwargs),
    ("sno-gpu", sno_gpu_transform_kwargs),
    ("snogpu", sno_gpu_transform_kwargs),
];

pub fn apply_transform_kwargs(
    ctx: &TransformKwargsContext,
    kwargs: Map<String, Value>,
    callback: Option<TransformKwargsCallback>,
) -> LlmixResult<Map<String, Value>> {
    match callback {
        Some(transform) => transform(ctx, kwargs),
        None => Ok(kwargs),
    }
}

pub fn provider_kwargs_callback(provider: &str) -> Option<TransformKwargsCallback> {
    PROVIDER_KWARGS_REGISTRY
        .iter()
        .find_map(|(name, callback)| (*name == provider).then_some(*callback))
}

pub fn is_reasoning_model(model_id: &str) -> bool {
    let lower = model_id.to_ascii_lowercase();
    starts_with_o_digit(&lower)
        || lower.starts_with("gpt-5")
        || lower.starts_with("codex-")
        || lower.starts_with("computer-use")
}

pub fn openai_transform_kwargs(
    ctx: &TransformKwargsContext,
    mut kwargs: Map<String, Value>,
) -> LlmixResult<Map<String, Value>> {
    if !is_reasoning_model(&ctx.model) {
        return Ok(kwargs);
    }

    kwargs.remove("temperature");
    kwargs.remove("top_p");
    kwargs.remove("topP");

    let target_key = if kwargs.contains_key("maxCompletionTokens") {
        "maxCompletionTokens"
    } else {
        "max_completion_tokens"
    };

    if !kwargs.contains_key("max_completion_tokens") && !kwargs.contains_key("maxCompletionTokens")
    {
        if let Some(value) = kwargs.remove("max_tokens") {
            kwargs.insert(target_key.to_string(), value);
        } else if let Some(value) = kwargs.remove("maxTokens") {
            kwargs.insert(target_key.to_string(), value);
        }
    }

    Ok(kwargs)
}

pub fn openrouter_transform_kwargs(
    _ctx: &TransformKwargsContext,
    mut kwargs: Map<String, Value>,
) -> LlmixResult<Map<String, Value>> {
    let mut extra_body = kwargs
        .get("extra_body")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    if !extra_body.contains_key("provider") {
        let mut provider = Map::new();
        provider.insert("sort".to_string(), Value::String("price".to_string()));
        extra_body.insert("provider".to_string(), Value::Object(provider));
        kwargs.insert("extra_body".to_string(), Value::Object(extra_body));
    }

    Ok(kwargs)
}

pub fn gemini_transform_kwargs(
    ctx: &TransformKwargsContext,
    mut kwargs: Map<String, Value>,
) -> LlmixResult<Map<String, Value>> {
    let google_opts = ctx
        .provider_options
        .as_ref()
        .and_then(|options| get_object_alias(options, &["google"]));

    let explicit_budget = google_opts
        .and_then(|google| {
            get_object_alias(google, &["thinking_config", "thinkingConfig"])
                .and_then(|thinking| {
                    get_value_alias(thinking, &["thinking_budget", "thinkingBudget"])
                })
                .cloned()
        })
        .or_else(|| {
            google_opts
                .and_then(|google| get_value_alias(google, &["thinking_budget", "thinkingBudget"]))
                .cloned()
        });

    let existing_key = if kwargs.contains_key("thinkingConfig") {
        Some("thinkingConfig")
    } else if kwargs.contains_key("thinking_config") {
        Some("thinking_config")
    } else {
        None
    };
    let existing_object = existing_key
        .and_then(|key| kwargs.get(key))
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();

    if existing_object.contains_key("thinking_budget")
        || existing_object.contains_key("thinkingBudget")
    {
        return Ok(kwargs);
    }

    let thinking_budget = match explicit_budget {
        Some(value) => value,
        None if ctx.enable_thinking == Some(true) => return Ok(kwargs),
        None => Value::from(0),
    };

    let prefer_camel = existing_key == Some("thinkingConfig")
        || google_opts
            .map(|google| {
                google.contains_key("thinkingConfig")
                    || google.contains_key("thinkingBudget")
                    || get_object_alias(google, &["thinkingConfig"])
                        .map(|thinking| thinking.contains_key("thinkingBudget"))
                        .unwrap_or(false)
            })
            .unwrap_or(false);

    let container_key = if prefer_camel {
        "thinkingConfig"
    } else {
        "thinking_config"
    };
    let budget_key = if prefer_camel {
        "thinkingBudget"
    } else {
        "thinking_budget"
    };

    let mut next_config = existing_object;
    next_config.insert(budget_key.to_string(), thinking_budget);
    kwargs.insert(container_key.to_string(), Value::Object(next_config));

    Ok(kwargs)
}

pub fn sno_gpu_transform_kwargs(
    ctx: &TransformKwargsContext,
    mut kwargs: Map<String, Value>,
) -> LlmixResult<Map<String, Value>> {
    let sno_gpu_opts = ctx
        .provider_options
        .as_ref()
        .and_then(|options| get_object_alias(options, &["sno-gpu", "snogpu"]));
    let gpu_path = sno_gpu_opts
        .and_then(|options| get_value_alias(options, &["gpu_path", "gpuPath"]))
        .and_then(Value::as_str);
    let enable_thinking = sno_gpu_opts
        .and_then(|options| get_value_alias(options, &["enable_thinking", "enableThinking"]))
        .and_then(Value::as_bool)
        .or(ctx.enable_thinking);

    let base_url = ctx.base_url.as_deref().unwrap_or_default();
    let mut base = base_url.trim_end_matches('/').to_string();
    if base.ends_with("/v1") {
        base.truncate(base.len() - 3);
    }

    if base.trim().is_empty() {
        return Err(LlmixError::InvalidProviderKwargsConfig(
            "sno-gpu provider requires a non-empty base_url".to_string(),
        ));
    }

    let output_key = if kwargs.contains_key("baseUrl") {
        "baseUrl"
    } else {
        "base_url"
    };
    let enable_key = if kwargs.contains_key("enableThinking")
        || sno_gpu_opts
            .map(|options| options.contains_key("enableThinking"))
            .unwrap_or(false)
    {
        "enableThinking"
    } else {
        "enable_thinking"
    };

    let rebuilt = match gpu_path {
        Some(path) if !path.is_empty() => {
            if path.contains("..") || !is_valid_gpu_path(path) {
                return Err(LlmixError::InvalidProviderKwargsConfig(format!(
                    "Invalid gpu_path: {path:?}"
                )));
            }
            format!("{base}/{path}/v1")
        }
        _ => format!("{base}/v1"),
    };

    kwargs.insert(output_key.to_string(), Value::String(rebuilt));
    if !kwargs.contains_key("enable_thinking") && !kwargs.contains_key("enableThinking") {
        if let Some(enable_thinking) = enable_thinking {
            kwargs.insert(enable_key.to_string(), Value::Bool(enable_thinking));
        }
    }
    Ok(kwargs)
}

fn starts_with_o_digit(model_id: &str) -> bool {
    let mut chars = model_id.chars();
    matches!(
        (chars.next(), chars.next()),
        (Some('o'), Some(second)) if second.is_ascii_digit()
    )
}

fn get_object_alias<'a>(
    map: &'a Map<String, Value>,
    keys: &[&str],
) -> Option<&'a Map<String, Value>> {
    get_value_alias(map, keys).and_then(Value::as_object)
}

fn get_value_alias<'a>(map: &'a Map<String, Value>, keys: &[&str]) -> Option<&'a Value> {
    keys.iter().find_map(|key| map.get(*key))
}

fn is_valid_gpu_path(path: &str) -> bool {
    path.chars()
        .all(|ch| ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' || ch == '/')
}
