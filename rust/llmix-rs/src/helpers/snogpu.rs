use crate::dispatch::DispatchFn;
use crate::error::{InvalidConfigError, LlmixResult};
use crate::helpers::openai::OpenAiChatHelper;
use crate::types::{DispatchContext, ProviderResult};
use async_trait::async_trait;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue};
use serde_json::{Map, Value};

const INTERNAL_TOKEN_HEADER: &str = "x-internal-token";

#[derive(Clone)]
pub struct SnoGpuChatHelper {
    inner: OpenAiChatHelper,
    internal_token: Option<String>,
}

impl Default for SnoGpuChatHelper {
    fn default() -> Self {
        Self::new()
    }
}

impl SnoGpuChatHelper {
    pub fn new() -> Self {
        Self {
            inner: OpenAiChatHelper::new().without_base_url(),
            internal_token: None,
        }
    }

    pub fn with_base_url(mut self, base_url: impl AsRef<str>) -> LlmixResult<Self> {
        self.inner = self.inner.clone().with_base_url(base_url)?;
        Ok(self)
    }

    pub fn with_client(mut self, client: reqwest::Client) -> Self {
        self.inner = self.inner.clone().with_client(client);
        self
    }

    pub fn with_internal_token(mut self, internal_token: impl Into<String>) -> Self {
        self.internal_token = Some(internal_token.into());
        self
    }
}

#[async_trait]
impl DispatchFn for SnoGpuChatHelper {
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult> {
        let internal_token = self.internal_token.clone();
        self.inner
            .dispatch_with(ctx, move |ctx, headers, body| {
                insert_internal_token_header(headers, internal_token.as_deref())?;
                inject_snogpu_extra_body(ctx, body);
                Ok(())
            })
            .await
    }
}

fn insert_internal_token_header(
    headers: &mut HeaderMap,
    internal_token: Option<&str>,
) -> LlmixResult<()> {
    let Some(internal_token) = internal_token
        .map(str::trim)
        .filter(|value| !value.is_empty())
    else {
        return Ok(());
    };

    let header_name = HeaderName::from_static(INTERNAL_TOKEN_HEADER);
    let header_value =
        HeaderValue::from_str(internal_token).map_err(|error| InvalidConfigError {
            message: format!("invalid x-internal-token header: {error}"),
        })?;
    headers.insert(header_name, header_value);
    Ok(())
}

fn inject_snogpu_extra_body(ctx: &DispatchContext, body: &mut Map<String, Value>) {
    let enable_thinking = body
        .get("enable_thinking")
        .or_else(|| body.get("enableThinking"))
        .and_then(Value::as_bool)
        .or_else(|| common_bool(&ctx.config, &["enable_thinking", "enableThinking"]))
        .or_else(|| provider_option_bool(&ctx.config, &["enable_thinking", "enableThinking"]));

    body.remove("enable_thinking");
    body.remove("enableThinking");

    let Some(enable_thinking) = enable_thinking else {
        return;
    };

    let mut extra_body = remove_object_alias(body, &["extra_body", "extraBody"]);
    extra_body.insert("enable_thinking".to_string(), Value::Bool(enable_thinking));

    let mut chat_template_kwargs = remove_object_alias(
        &mut extra_body,
        &["chat_template_kwargs", "chatTemplateKwargs"],
    );
    chat_template_kwargs.insert("enable_thinking".to_string(), Value::Bool(enable_thinking));
    extra_body.insert(
        "chat_template_kwargs".to_string(),
        Value::Object(chat_template_kwargs),
    );
    body.insert("extra_body".to_string(), Value::Object(extra_body));
}

fn remove_object_alias(map: &mut Map<String, Value>, keys: &[&str]) -> Map<String, Value> {
    for key in keys {
        if let Some(Value::Object(object)) = map.remove(*key) {
            return object;
        }
    }
    Map::new()
}

fn common_bool(config: &Value, keys: &[&str]) -> Option<bool> {
    config
        .as_object()
        .and_then(|config| config.get("common"))
        .and_then(Value::as_object)
        .and_then(|common| get_bool_alias(common, keys))
}

fn provider_option_bool(config: &Value, keys: &[&str]) -> Option<bool> {
    config
        .as_object()
        .and_then(|config| {
            config
                .get("provider_options")
                .or_else(|| config.get("providerOptions"))
        })
        .and_then(Value::as_object)
        .and_then(|provider_options| {
            provider_options
                .get("sno-gpu")
                .or_else(|| provider_options.get("snogpu"))
        })
        .and_then(Value::as_object)
        .and_then(|provider_options| get_bool_alias(provider_options, keys))
}

fn get_bool_alias(map: &Map<String, Value>, keys: &[&str]) -> Option<bool> {
    keys.iter()
        .find_map(|key| map.get(*key))
        .and_then(Value::as_bool)
}
