use crate::dispatch::DispatchFn;
use crate::error::{InvalidConfigError, LlmixError, LlmixResult, ProviderError};
use crate::types::{DispatchContext, LlmUsage, ProviderResult};
use async_trait::async_trait;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, ACCEPT, AUTHORIZATION, CONTENT_TYPE};
use serde_json::{Map, Value};
use std::collections::HashMap;
use url::Url;

const DEFAULT_OPENAI_BASE_URL: &str = "https://api.openai.com/v1";

#[derive(Clone)]
pub struct OpenAiChatHelper {
    client: reqwest::Client,
    base_url: Option<String>,
    default_headers: HeaderMap,
}

impl Default for OpenAiChatHelper {
    fn default() -> Self {
        Self::new()
    }
}

impl OpenAiChatHelper {
    pub fn new() -> Self {
        Self {
            client: reqwest::Client::new(),
            base_url: Some(DEFAULT_OPENAI_BASE_URL.to_string()),
            default_headers: HeaderMap::new(),
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

    pub(crate) async fn dispatch_with<F>(
        &self,
        ctx: DispatchContext,
        customize: F,
    ) -> LlmixResult<ProviderResult>
    where
        F: FnOnce(&DispatchContext, &mut HeaderMap, &mut Map<String, Value>) -> LlmixResult<()>,
    {
        let mut headers = self.request_headers(&ctx)?;
        let mut body = build_request_body(&ctx);
        customize(&ctx, &mut headers, &mut body)?;

        let base_url = self.resolve_base_url(&ctx.kwargs)?;
        let endpoint = format!("{}/chat/completions", base_url.trim_end_matches('/'));
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

    fn request_headers(&self, ctx: &DispatchContext) -> LlmixResult<HeaderMap> {
        let mut headers = self.default_headers.clone();
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));

        if !ctx.api_key.trim().is_empty() {
            let bearer = format!("Bearer {}", ctx.api_key.trim());
            let value = HeaderValue::from_str(&bearer)
                .map_err(|error| invalid_config(format!("invalid api key header: {error}")))?;
            headers.insert(AUTHORIZATION, value);
        }

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
                "OpenAI-compatible helper requires a non-empty base_url".to_string(),
            )),
        }
    }
}

#[async_trait]
impl DispatchFn for OpenAiChatHelper {
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult> {
        self.dispatch_with(ctx, |_ctx, _headers, _body| Ok(()))
            .await
    }
}

fn build_request_body(ctx: &DispatchContext) -> Map<String, Value> {
    let mut body = ctx.kwargs.clone();
    body.remove("base_url");
    body.remove("baseUrl");
    body.remove("top_k");
    body.remove("topK");
    body.insert("model".to_string(), Value::String(ctx.model.clone()));
    body.insert("messages".to_string(), Value::Array(ctx.messages.clone()));
    body
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
        .pointer("/choices/0/message/content")
        .map(extract_content)
        .unwrap_or_default();
    let model = payload
        .get("model")
        .and_then(Value::as_str)
        .unwrap_or(&ctx.model)
        .to_string();
    let tool_calls = payload
        .pointer("/choices/0/message/tool_calls")
        .or_else(|| payload.pointer("/choices/0/message/toolCalls"))
        .and_then(Value::as_array)
        .cloned();

    Ok(ProviderResult {
        content,
        model,
        usage: extract_usage(&payload),
        headers: (!headers.is_empty()).then(|| headers.clone()),
        tool_calls,
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
        .unwrap_or_else(|| {
            let text = String::from_utf8_lossy(body).trim().to_string();
            if text.is_empty() {
                format!("provider request failed with status {status_code}")
            } else {
                text
            }
        });

    ProviderError {
        message,
        status_code: Some(status_code),
        headers: Some(headers),
    }
    .into()
}

fn extract_usage(payload: &Value) -> LlmUsage {
    let prompt_tokens = payload
        .pointer("/usage/prompt_tokens")
        .and_then(value_as_u32)
        .unwrap_or(0);
    let completion_tokens = payload
        .pointer("/usage/completion_tokens")
        .and_then(value_as_u32)
        .unwrap_or(0);
    let total_tokens = payload
        .pointer("/usage/total_tokens")
        .and_then(value_as_u32)
        .unwrap_or_else(|| prompt_tokens.saturating_add(completion_tokens));

    LlmUsage {
        input_tokens: prompt_tokens,
        output_tokens: completion_tokens,
        total_tokens,
    }
}

fn extract_content(value: &Value) -> String {
    match value {
        Value::String(text) => text.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(extract_text_part)
            .collect::<Vec<_>>()
            .join(""),
        _ => String::new(),
    }
}

fn extract_text_part(value: &Value) -> Option<&str> {
    match value {
        Value::String(text) => Some(text.as_str()),
        Value::Object(map) => map
            .get("text")
            .and_then(Value::as_str)
            .or_else(|| map.get("content").and_then(Value::as_str)),
        _ => None,
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
