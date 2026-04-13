use crate::dispatch::DispatchFn;
use crate::error::{InvalidConfigError, LlmixError, LlmixResult, ProviderError};
use crate::types::{DispatchContext, LlmUsage, ProviderResult};
use async_trait::async_trait;
use reqwest::header::{HeaderMap, HeaderName, HeaderValue, ACCEPT, CONTENT_TYPE};
use serde_json::{Map, Value};
use std::collections::HashMap;
use url::Url;

const DEFAULT_GEMINI_BASE_URL: &str = "https://generativelanguage.googleapis.com/v1beta";
const DEFAULT_EMPTY_USER_PROMPT: &str = "Please respond.";
const CONTINUATION_PROMPT: &str = "Continue.";

#[derive(Clone)]
pub struct GeminiChatHelper {
    client: reqwest::Client,
    base_url: Option<String>,
    default_headers: HeaderMap,
}

impl Default for GeminiChatHelper {
    fn default() -> Self {
        Self::new()
    }
}

impl GeminiChatHelper {
    pub fn new() -> Self {
        Self {
            client: reqwest::Client::new(),
            base_url: Some(DEFAULT_GEMINI_BASE_URL.to_string()),
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

    fn request_headers(&self) -> HeaderMap {
        let mut headers = self.default_headers.clone();
        headers.insert(ACCEPT, HeaderValue::from_static("application/json"));
        headers.insert(CONTENT_TYPE, HeaderValue::from_static("application/json"));
        headers
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
                "Gemini helper requires a non-empty base_url".to_string(),
            )),
        }
    }
}

#[async_trait]
impl DispatchFn for GeminiChatHelper {
    async fn dispatch(&self, ctx: DispatchContext) -> LlmixResult<ProviderResult> {
        let mut body = build_request_body(&ctx);
        let base_url = self.resolve_base_url(&body)?;
        body.remove("base_url");
        body.remove("baseUrl");

        let endpoint = format!(
            "{}/models/{}:generateContent",
            base_url.trim_end_matches('/'),
            ctx.model
        );
        let mut request = self
            .client
            .post(endpoint)
            .headers(self.request_headers())
            .json(&Value::Object(body));
        if !ctx.api_key.trim().is_empty() {
            request = request.query(&[("key", ctx.api_key.trim())]);
        }

        let response = request.send().await.map_err(|error| {
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
    let mut generation_config = take_object(&mut body, "generationConfig")
        .or_else(|| take_object(&mut body, "generation_config"))
        .unwrap_or_default();

    if let Some(value) = body.remove("temperature") {
        generation_config.insert("temperature".to_string(), value);
    }
    if let Some(value) = body
        .remove("max_tokens")
        .or_else(|| body.remove("maxTokens"))
    {
        generation_config.insert("maxOutputTokens".to_string(), value);
    }
    if let Some(value) = body.remove("top_p").or_else(|| body.remove("topP")) {
        generation_config.insert("topP".to_string(), value);
    }
    if let Some(value) = body
        .remove("thinking_config")
        .or_else(|| body.remove("thinkingConfig"))
    {
        generation_config.insert(
            "thinkingConfig".to_string(),
            normalize_thinking_config(value),
        );
    }
    if !generation_config.is_empty() {
        body.insert(
            "generationConfig".to_string(),
            Value::Object(generation_config),
        );
    }

    let (system_instruction, mut contents) = convert_messages(&ctx.messages);
    if let Some(system_instruction) = system_instruction {
        body.insert("systemInstruction".to_string(), system_instruction);
    }
    if contents
        .last()
        .and_then(|value| value.get("role"))
        .and_then(Value::as_str)
        == Some("model")
    {
        contents.push(text_message("user", CONTINUATION_PROMPT));
    }
    if contents.is_empty() {
        contents.push(text_message("user", DEFAULT_EMPTY_USER_PROMPT));
    }
    body.insert("contents".to_string(), Value::Array(contents));

    body
}

fn convert_messages(messages: &[Value]) -> (Option<Value>, Vec<Value>) {
    let mut system_parts = Vec::new();
    let mut contents = Vec::new();

    for message in messages {
        let role = message
            .get("role")
            .and_then(Value::as_str)
            .unwrap_or("user");
        let text = extract_text_value(message.get("content").unwrap_or(&Value::Null));
        match role {
            "system" => {
                if !text.is_empty() {
                    system_parts.push(Value::Object(
                        [("text".to_string(), Value::String(text))]
                            .into_iter()
                            .collect(),
                    ));
                }
            }
            "assistant" | "model" => contents.push(text_message("model", &text)),
            _ => contents.push(text_message("user", &text)),
        }
    }

    let system_instruction = (!system_parts.is_empty()).then(|| {
        Value::Object(
            [("parts".to_string(), Value::Array(system_parts))]
                .into_iter()
                .collect(),
        )
    });

    (system_instruction, contents)
}

fn text_message(role: &str, text: &str) -> Value {
    Value::Object(
        [
            ("role".to_string(), Value::String(role.to_string())),
            (
                "parts".to_string(),
                Value::Array(vec![Value::Object(
                    [("text".to_string(), Value::String(text.to_string()))]
                        .into_iter()
                        .collect(),
                )]),
            ),
        ]
        .into_iter()
        .collect(),
    )
}

fn normalize_thinking_config(value: Value) -> Value {
    match value {
        Value::Object(mut map) => {
            if let Some(budget) = map.remove("thinking_budget") {
                map.insert("thinkingBudget".to_string(), budget);
            }
            Value::Object(map)
        }
        other => other,
    }
}

fn take_object(body: &mut Map<String, Value>, key: &str) -> Option<Map<String, Value>> {
    match body.remove(key) {
        Some(Value::Object(map)) => Some(map),
        Some(value) => {
            body.insert(key.to_string(), value);
            None
        }
        None => None,
    }
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
        .pointer("/candidates/0/content/parts")
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
        .get("modelVersion")
        .and_then(Value::as_str)
        .unwrap_or(&ctx.model)
        .to_string();
    let input_tokens = payload
        .pointer("/usageMetadata/promptTokenCount")
        .and_then(value_as_u32)
        .unwrap_or(0);
    let output_tokens = payload
        .pointer("/usageMetadata/candidatesTokenCount")
        .and_then(value_as_u32)
        .unwrap_or(0);
    let total_tokens = payload
        .pointer("/usageMetadata/totalTokenCount")
        .and_then(value_as_u32)
        .unwrap_or_else(|| input_tokens.saturating_add(output_tokens));

    Ok(ProviderResult {
        content,
        model,
        usage: LlmUsage {
            input_tokens,
            output_tokens,
            total_tokens,
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
