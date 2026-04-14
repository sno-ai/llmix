use crate::dispatch::DispatchFn;
use crate::error::{InvalidConfigError, LlmixError, LlmixResult, ProviderError};
use crate::types::{DispatchContext, LlmUsage, ProviderResult};
use async_trait::async_trait;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, ACCEPT, CONTENT_TYPE};
use serde_json::{Map, Value};
use std::collections::HashMap;
use url::Url;

const DEFAULT_ANTHROPIC_BASE_URL: &str = "https://api.anthropic.com/v1";
const DEFAULT_ANTHROPIC_VERSION: &str = "2023-06-01";
const DEFAULT_MAX_TOKENS: u64 = 1024;

#[derive(Clone)]
pub struct AnthropicChatHelper {
    client: reqwest::Client,
    base_url: Option<String>,
    default_headers: HeaderMap,
    anthropic_version: String,
}

impl Default for AnthropicChatHelper {
    fn default() -> Self {
        Self::new()
    }
}

impl AnthropicChatHelper {
    pub fn new() -> Self {
        Self {
            client: reqwest::Client::new(),
            base_url: Some(DEFAULT_ANTHROPIC_BASE_URL.to_string()),
            default_headers: HeaderMap::new(),
            anthropic_version: DEFAULT_ANTHROPIC_VERSION.to_string(),
        }
    }

    pub fn without_base_url(mut self) -> Self {
        self.base_url = None;
        self
    }

    pub fn with_base_url(mut self, base_url: impl AsRef<str>) -> LlmixResult<Self> {
        self.base_url = Some(normalize_base_url(base_url.as_ref())?);
        Ok(self)
    }

    pub fn with_client(mut self, client: reqwest::Client) -> Self {
        self.client = client;
        self
    }

    pub fn with_default_header(
        mut self,
        name: impl AsRef<str>,
        value: impl AsRef<str>,
    ) -> LlmixResult<Self> {
        let header_name = HeaderName::from_bytes(name.as_ref().as_bytes())
            .map_err(|error| invalid_config(format!("invalid header name: {error}")))?;
        let header_value = HeaderValue::from_str(value.as_ref())
            .map_err(|error| invalid_config(format!("invalid header value: {error}")))?;
        self.default_headers.insert(header_name, header_value);
        Ok(self)
    }

    pub fn with_anthropic_version(mut self, version: impl AsRef<str>) -> LlmixResult<Self> {
        let version = version.as_ref().trim();
        if version.is_empty() {
            return Err(invalid_config(
                "anthropic version must not be empty".to_string(),
            ));
        }
        self.anthropic_version = version.to_string();
        Ok(self)
    }

    fn request_headers(&self, ctx: &DispatchContext) -> LlmixResult<HeaderMap> {
        let mut headers = self.default_headers.clone();
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));

        if !ctx.api_key.trim().is_empty() {
            let value = HeaderValue::from_str(ctx.api_key.trim())
                .map_err(|error| invalid_config(format!("invalid api key header: {error}")))?;
            headers.insert(HeaderName::from_static("x-api-key"), value);
        }

        let version = HeaderValue::from_str(&self.anthropic_version).map_err(|error| {
            invalid_config(format!("invalid anthropic version header: {error}"))
        })?;
        headers.insert(HeaderName::from_static("anthropic-version"), version);

        Ok(headers)
    }

    fn resolve_base_url(&self, body: &Map<String, Value>) -> LlmixResult<String> {
        let override_base_url = body
            .get("base_url")
            .or_else(|| body.get("baseUrl"))
            .and_then(Value::as_str)
            .map(str::trim)
            .filter(|value| !value.is_empty());

        match override_base_url.or(self.base_url.as_deref()) {
            Some(base_url) => normalize_base_url(base_url),
            None => Err(invalid_config(
                "Anthropic helper requires a non-empty base_url".to_string(),
            )),
        }
    }
}

#[async_trait]
impl DispatchFn for AnthropicChatHelper {
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult> {
        let headers = self.request_headers(&ctx)?;
        let body = build_request_body(&ctx);
        let base_url = self.resolve_base_url(&body)?;
        let endpoint = format!("{}/messages", base_url.trim_end_matches('/'));
        let response = self
            .client
            .post(endpoint)
            .headers(headers)
            .json(&Value::Object(body))
            .send()
            .await
            .map_err(|error| {
                provider_transport_error(format!("provider request failed: {error}"))
            })?;

        let status = response.status();
        let headers = collect_headers(response.headers());
        let body = response.bytes().await.map_err(|error| {
            provider_transport_error(format!("failed reading provider response: {error}"))
        })?;

        if !status.is_success() {
            return Err(parse_provider_error(status.as_u16(), headers, &body));
        }

        parse_provider_result(&ctx, &headers, &body)
    }
}

fn build_request_body(ctx: &DispatchContext) -> Map<String, Value> {
    let mut body = ctx.kwargs.clone();
    body.remove("base_url");
    body.remove("baseUrl");
    body.insert("model".to_string(), Value::String(ctx.model.clone()));

    if let Some(max_tokens) = body.remove("maxTokens") {
        body.insert("max_tokens".to_string(), max_tokens);
    }
    if let Some(top_p) = body.remove("topP") {
        body.insert("top_p".to_string(), top_p);
    }
    if let Some(stop) = body.remove("stop") {
        body.insert("stop_sequences".to_string(), stop);
    }

    let (messages, system) = split_system_messages(&ctx.messages);
    body.insert("messages".to_string(), Value::Array(messages));
    if let Some(system) = system {
        body.insert("system".to_string(), Value::String(system));
    }
    if !body.contains_key("max_tokens") {
        body.insert(
            "max_tokens".to_string(),
            Value::Number(DEFAULT_MAX_TOKENS.into()),
        );
    }

    body
}

fn split_system_messages(messages: &[Value]) -> (Vec<Value>, Option<String>) {
    let mut filtered = Vec::new();
    let mut system_parts = Vec::new();

    for message in messages {
        match message.get("role").and_then(Value::as_str) {
            Some("system") => {
                let text = extract_text_value(message.get("content").unwrap_or(&Value::Null));
                if !text.is_empty() {
                    system_parts.push(text);
                }
            }
            _ => filtered.push(message.clone()),
        }
    }

    let system = (!system_parts.is_empty()).then(|| system_parts.join("\n"));
    (filtered, system)
}

fn parse_provider_result(
    ctx: &DispatchContext,
    headers: &HashMap<String, String>,
    body: &[u8],
) -> LlmixResult<ProviderResult> {
    let payload: Value = serde_json::from_slice(body).map_err(|error| ProviderError {
        message: format!("invalid provider response: {error}"),
        status_code: None,
        headers: Some(headers.clone()),
    })?;

    let content = payload
        .pointer("/content")
        .and_then(Value::as_array)
        .map(|parts| {
            parts
                .iter()
                .map(|part| extract_text_value(part.get("text").unwrap_or(part)))
                .collect::<Vec<_>>()
                .join("")
        })
        .unwrap_or_default();
    let model = payload
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or(&ctx.model)
        .to_string();
    let input_tokens = payload
        .pointer("/usage/input_tokens")
        .and_then(value_as_u32)
        .unwrap_or(0);
    let output_tokens = payload
        .pointer("/usage/output_tokens")
        .and_then(value_as_u32)
        .unwrap_or(0);

    Ok(ProviderResult {
        content,
        model,
        usage: LlmUsage {
            input_tokens,
            output_tokens,
            total_tokens: input_tokens.saturating_add(output_tokens),
        },
        headers: (!headers.is_empty()).then(|| headers.clone()),
        tool_calls: None,
    })
}

fn parse_provider_error(
    status_code: u16,
    headers: HashMap<String, String>,
    body: &[u8],
) -> LlmixError {
    let message = serde_json::from_slice::<Value>(body)
        .ok()
        .and_then(|payload| {
            payload
                .pointer("/error/message")
                .and_then(Value::as_str)
                .or_else(|| payload.get("message").and_then(Value::as_str))
                .map(ToOwned::to_owned)
        })
        .unwrap_or_else(|| fallback_error_message(status_code, body));

    ProviderError {
        message,
        status_code: Some(status_code),
        headers: Some(headers),
    }
    .into()
}

fn extract_text_value(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(values) => values
            .iter()
            .map(extract_text_value)
            .filter(|value| !value.is_empty())
            .collect::<Vec<_>>()
            .join(""),
        Value::Object(map) => map
            .get("text")
            .or_else(|| map.get("content"))
            .map(extract_text_value)
            .unwrap_or_default(),
        _ => String::new(),
    }
}

fn collect_headers(headers: &reqwest::header::HeaderMap) -> HashMap<String, String> {
    headers
        .iter()
        .filter_map(|(name, value)| {
            value
                .to_str()
                .ok()
                .map(|value| (name.as_str().to_string(), value.to_string()))
        })
        .collect()
}

fn normalize_base_url(base_url: &str) -> LlmixResult<String> {
    let trimmed = base_url.trim();
    if trimmed.is_empty() {
        return Err(invalid_config("base_url must not be empty".to_string()));
    }

    Url::parse(trimmed).map_err(|error| invalid_config(format!("invalid base_url: {error}")))?;
    Ok(trimmed.trim_end_matches('/').to_string())
}

fn value_as_u32(value: &Value) -> Option<u32> {
    value
        .as_u64()
        .and_then(|candidate| u32::try_from(candidate).ok())
        .or_else(|| {
            value
                .as_i64()
                .filter(|candidate| *candidate >= 0)
                .and_then(|candidate| u32::try_from(candidate).ok())
        })
}

fn invalid_config(message: String) -> LlmixError {
    InvalidConfigError { message }.into()
}

fn provider_transport_error(message: String) -> LlmixError {
    ProviderError {
        message,
        status_code: None,
        headers: None,
    }
    .into()
}

fn fallback_error_message(status_code: u16, body: &[u8]) -> String {
    let text = String::from_utf8_lossy(body).trim().to_string();
    if text.is_empty() {
        format!("provider request failed with status {status_code}")
    } else {
        text
    }
}
