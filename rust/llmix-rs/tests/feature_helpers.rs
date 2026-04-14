#![cfg(any(
    feature = "helpers-openai",
    feature = "helpers-sno-gpu",
    feature = "helpers-anthropic",
    feature = "helpers-gemini"
))]

use serde_json::{json, Value};
use std::collections::HashMap;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

#[cfg(feature = "helpers-anthropic")]
use llmix_rs::AnthropicChatHelper;
#[cfg(feature = "helpers-gemini")]
use llmix_rs::GeminiChatHelper;
#[cfg(feature = "helpers-openai")]
use llmix_rs::OpenAiChatHelper;
#[cfg(feature = "helpers-sno-gpu")]
use llmix_rs::SnoGpuChatHelper;
use llmix_rs::{DispatchContext, DispatchFn, LlmixError};

#[cfg(feature = "helpers-sno-gpu")]
use llmix_rs::{CallInput, CallPipeline, KeyPool, PipelineConfig};

#[derive(Debug)]
struct CapturedRequest {
    method: String,
    path: String,
    headers: HashMap<String, String>,
    body: Value,
}

async fn spawn_json_server(
    status: &str,
    extra_headers: &[(&str, &str)],
    response_body: Value,
) -> (String, oneshot::Receiver<CapturedRequest>, JoinHandle<()>) {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener should bind");
    let addr = listener
        .local_addr()
        .expect("test listener should expose local address");
    let status = status.to_string();
    let extra_headers = extra_headers
        .iter()
        .map(|(name, value)| ((*name).to_string(), (*value).to_string()))
        .collect::<Vec<_>>();
    let response_body = serde_json::to_vec(&response_body).expect("response json should serialize");
    let (tx, rx) = oneshot::channel();

    let task = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("test server should accept");
        let mut buffer = Vec::new();
        let header_end = loop {
            let mut chunk = [0_u8; 1024];
            let read = stream
                .read(&mut chunk)
                .await
                .expect("request should be readable");
            assert!(read > 0, "client closed before sending request headers");
            buffer.extend_from_slice(&chunk[..read]);
            if let Some(position) = find_header_end(&buffer) {
                break position;
            }
        };

        let request_head = &buffer[..header_end];
        let mut request_body = buffer[header_end..].to_vec();
        let (method, path, headers) = parse_request_head(request_head);
        let content_length = headers
            .get("content-length")
            .and_then(|value| value.parse::<usize>().ok())
            .unwrap_or(0);

        while request_body.len() < content_length {
            let mut chunk = vec![0_u8; content_length - request_body.len()];
            let read = stream
                .read(&mut chunk)
                .await
                .expect("request body should be readable");
            assert!(read > 0, "client closed before sending request body");
            request_body.extend_from_slice(&chunk[..read]);
        }

        let captured = CapturedRequest {
            method,
            path,
            headers,
            body: serde_json::from_slice(&request_body).expect("request body should be valid json"),
        };
        let _ = tx.send(captured);

        let mut response = format!(
            "HTTP/1.1 {status}\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n",
            response_body.len()
        );
        for (name, value) in extra_headers {
            response.push_str(&format!("{name}: {value}\r\n"));
        }
        response.push_str("\r\n");

        stream
            .write_all(response.as_bytes())
            .await
            .expect("response head should be writable");
        stream
            .write_all(&response_body)
            .await
            .expect("response body should be writable");
    });

    (format!("http://{addr}"), rx, task)
}

fn find_header_end(buffer: &[u8]) -> Option<usize> {
    buffer
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|position| position + 4)
}

fn parse_request_head(head: &[u8]) -> (String, String, HashMap<String, String>) {
    let head = String::from_utf8_lossy(head);
    let mut lines = head.split("\r\n").filter(|line| !line.is_empty());
    let request_line = lines.next().expect("request line should exist");
    let mut request_parts = request_line.split_whitespace();
    let method = request_parts
        .next()
        .expect("request method should exist")
        .to_string();
    let path = request_parts
        .next()
        .expect("request path should exist")
        .to_string();

    let mut headers = HashMap::new();
    for line in lines {
        if let Some((name, value)) = line.split_once(':') {
            headers.insert(name.trim().to_ascii_lowercase(), value.trim().to_string());
        }
    }

    (method, path, headers)
}

#[cfg(feature = "helpers-openai")]
#[tokio::test]
async fn openai_helper_builds_chat_request_and_parses_response() {
    let (base_url, request_rx, server) = spawn_json_server(
        "200 OK",
        &[
            ("x-ratelimit-limit-requests", "20"),
            ("x-ratelimit-remaining-requests", "19"),
        ],
        json!({
            "model": "gpt-4o-mini-2026-04-13",
            "choices": [{
                "message": {
                    "content": "4",
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": { "name": "lookup" }
                    }]
                }
            }],
            "usage": {
                "prompt_tokens": 11,
                "completion_tokens": 7,
                "total_tokens": 18
            }
        }),
    )
    .await;

    let helper = OpenAiChatHelper::new()
        .with_base_url(format!("{base_url}/v1"))
        .expect("base url should validate");
    let result = helper
        .dispatch(DispatchContext {
            provider: "openai".to_string(),
            model: "gpt-4o-mini".to_string(),
            api_key: "sk-test".to_string(),
            messages: vec![json!({ "role": "user", "content": "What is 2+2?" })],
            kwargs: serde_json::from_value(json!({
                "temperature": 0,
                "max_tokens": 32,
                "seed": 7,
                "response_format": { "type": "json_object" }
            }))
            .expect("request kwargs should deserialize"),
            config: json!({}),
        })
        .await
        .expect("helper request should succeed");

    let request = request_rx.await.expect("request should be captured");
    server.await.expect("test server should complete");

    assert_eq!(request.method, "POST");
    assert_eq!(request.path, "/v1/chat/completions");
    assert_eq!(
        request.headers.get("authorization"),
        Some(&"Bearer sk-test".to_string())
    );
    assert_eq!(request.body["model"], json!("gpt-4o-mini"));
    assert_eq!(
        request.body["messages"][0]["content"],
        json!("What is 2+2?")
    );
    assert_eq!(request.body["temperature"], json!(0));
    assert_eq!(request.body["max_tokens"], json!(32));
    assert_eq!(request.body["seed"], json!(7));
    assert_eq!(
        request.body["response_format"],
        json!({ "type": "json_object" })
    );

    assert_eq!(result.content, "4");
    assert_eq!(result.model, "gpt-4o-mini-2026-04-13");
    assert_eq!(result.usage.input_tokens, 11);
    assert_eq!(result.usage.output_tokens, 7);
    assert_eq!(result.usage.total_tokens, 18);
    assert_eq!(
        result.tool_calls,
        Some(vec![json!({
            "id": "call_1",
            "type": "function",
            "function": { "name": "lookup" }
        })])
    );
    assert_eq!(
        result
            .headers
            .as_ref()
            .and_then(|headers| headers.get("x-ratelimit-remaining-requests")),
        Some(&"19".to_string())
    );
}

#[cfg(feature = "helpers-openai")]
#[tokio::test]
async fn openai_helper_surfaces_provider_error_headers() {
    let (base_url, _request_rx, server) = spawn_json_server(
        "429 Too Many Requests",
        &[("retry-after", "5")],
        json!({
            "error": { "message": "rate limited" }
        }),
    )
    .await;

    let helper = OpenAiChatHelper::new()
        .with_base_url(format!("{base_url}/v1"))
        .expect("base url should validate");
    let error = helper
        .dispatch(DispatchContext {
            provider: "openai".to_string(),
            model: "gpt-4o-mini".to_string(),
            api_key: "sk-test".to_string(),
            messages: vec![json!({ "role": "user", "content": "Hello" })],
            kwargs: serde_json::Map::new(),
            config: json!({}),
        })
        .await
        .expect_err("helper should return provider error");

    server.await.expect("test server should complete");

    match error {
        LlmixError::Provider(provider_error) => {
            assert_eq!(provider_error.message, "rate limited");
            assert_eq!(provider_error.status_code, Some(429));
            assert_eq!(
                provider_error
                    .headers
                    .as_ref()
                    .and_then(|headers| headers.get("retry-after")),
                Some(&"5".to_string())
            );
        }
        other => panic!("expected provider error, got {other}"),
    }
}

#[cfg(feature = "helpers-anthropic")]
#[tokio::test]
async fn anthropic_helper_extracts_system_messages_and_parses_response() {
    let (base_url, request_rx, server) = spawn_json_server(
        "200 OK",
        &[("anthropic-ratelimit-remaining-requests", "7")],
        json!({
            "model": "claude-sonnet-4-20250514",
            "content": [
                { "type": "text", "text": "Ahoy" },
                { "type": "text", "text": " there" }
            ],
            "usage": {
                "input_tokens": 12,
                "output_tokens": 8
            }
        }),
    )
    .await;

    let helper = AnthropicChatHelper::new()
        .with_base_url(format!("{base_url}/v1"))
        .expect("base url should validate");
    let result = helper
        .dispatch(DispatchContext {
            provider: "anthropic".to_string(),
            model: "claude-sonnet-4".to_string(),
            api_key: "test-key".to_string(),
            messages: vec![
                json!({ "role": "system", "content": "Talk like a pirate." }),
                json!({ "role": "user", "content": "Say hello." }),
            ],
            kwargs: serde_json::from_value(json!({
                "temperature": 0.2,
                "stop": ["END"]
            }))
            .expect("request kwargs should deserialize"),
            config: json!({}),
        })
        .await
        .expect("helper request should succeed");

    let request = request_rx.await.expect("request should be captured");
    server.await.expect("test server should complete");

    assert_eq!(request.method, "POST");
    assert_eq!(request.path, "/v1/messages");
    assert_eq!(
        request.headers.get("x-api-key"),
        Some(&"test-key".to_string())
    );
    assert_eq!(
        request.headers.get("anthropic-version"),
        Some(&"2023-06-01".to_string())
    );
    assert_eq!(request.body["model"], json!("claude-sonnet-4"));
    assert_eq!(request.body["system"], json!("Talk like a pirate."));
    assert_eq!(
        request.body["messages"],
        json!([{ "role": "user", "content": "Say hello." }])
    );
    assert_eq!(request.body["temperature"], json!(0.2));
    assert_eq!(request.body["stop_sequences"], json!(["END"]));
    assert_eq!(request.body["max_tokens"], json!(1024));

    assert_eq!(result.content, "Ahoy there");
    assert_eq!(result.model, "claude-sonnet-4-20250514");
    assert_eq!(result.usage.input_tokens, 12);
    assert_eq!(result.usage.output_tokens, 8);
    assert_eq!(result.usage.total_tokens, 20);
    assert_eq!(
        result
            .headers
            .as_ref()
            .and_then(|headers| headers.get("anthropic-ratelimit-remaining-requests")),
        Some(&"7".to_string())
    );
}

#[cfg(feature = "helpers-anthropic")]
#[tokio::test]
async fn anthropic_helper_surfaces_provider_error_headers() {
    let (base_url, _request_rx, server) = spawn_json_server(
        "529 Service Unavailable",
        &[("retry-after", "3")],
        json!({
            "error": { "message": "overloaded" }
        }),
    )
    .await;

    let helper = AnthropicChatHelper::new()
        .with_base_url(format!("{base_url}/v1"))
        .expect("base url should validate");
    let error = helper
        .dispatch(DispatchContext {
            provider: "anthropic".to_string(),
            model: "claude-sonnet-4".to_string(),
            api_key: "test-key".to_string(),
            messages: vec![json!({ "role": "user", "content": "Hello" })],
            kwargs: serde_json::Map::new(),
            config: json!({}),
        })
        .await
        .expect_err("helper should return provider error");

    server.await.expect("test server should complete");

    match error {
        LlmixError::Provider(provider_error) => {
            assert_eq!(provider_error.message, "overloaded");
            assert_eq!(provider_error.status_code, Some(529));
            assert_eq!(
                provider_error
                    .headers
                    .as_ref()
                    .and_then(|headers| headers.get("retry-after")),
                Some(&"3".to_string())
            );
        }
        other => panic!("expected provider error, got {other}"),
    }
}

#[cfg(feature = "helpers-gemini")]
#[tokio::test]
async fn gemini_helper_formats_system_instruction_and_continuation() {
    let (base_url, request_rx, server) = spawn_json_server(
        "200 OK",
        &[("x-request-id", "gemini-1")],
        json!({
            "modelVersion": "gemini-2.5-flash-002",
            "candidates": [{
                "content": {
                    "parts": [
                        { "text": "7" },
                        { "text": "8" }
                    ]
                }
            }],
            "usageMetadata": {
                "promptTokenCount": 10,
                "candidatesTokenCount": 4,
                "totalTokenCount": 14
            }
        }),
    )
    .await;

    let helper = GeminiChatHelper::new()
        .with_base_url(format!("{base_url}/v1beta"))
        .expect("base url should validate");
    let result = helper
        .dispatch(DispatchContext {
            provider: "gemini".to_string(),
            model: "gemini-2.5-flash".to_string(),
            api_key: "test-key".to_string(),
            messages: vec![
                json!({ "role": "system", "content": "Answer as digits only." }),
                json!({ "role": "user", "content": "Start counting." }),
                json!({ "role": "assistant", "content": "1, 2, 3" }),
            ],
            kwargs: serde_json::from_value(json!({
                "temperature": 0,
                "max_tokens": 32,
                "top_p": 0.9,
                "thinking_config": {
                    "thinking_budget": 256
                }
            }))
            .expect("request kwargs should deserialize"),
            config: json!({}),
        })
        .await
        .expect("helper request should succeed");

    let request = request_rx.await.expect("request should be captured");
    server.await.expect("test server should complete");

    assert_eq!(request.method, "POST");
    assert_eq!(
        request.path,
        "/v1beta/models/gemini-2.5-flash:generateContent?key=test-key"
    );
    assert_eq!(
        request.headers.get("content-type"),
        Some(&"application/json".to_string())
    );
    assert_eq!(
        request.body["systemInstruction"]["parts"][0]["text"],
        json!("Answer as digits only.")
    );
    assert_eq!(
        request.body["contents"],
        json!([
            {
                "role": "user",
                "parts": [{ "text": "Start counting." }]
            },
            {
                "role": "model",
                "parts": [{ "text": "1, 2, 3" }]
            },
            {
                "role": "user",
                "parts": [{ "text": "Continue." }]
            }
        ])
    );
    assert_eq!(request.body["generationConfig"]["temperature"], json!(0));
    assert_eq!(
        request.body["generationConfig"]["maxOutputTokens"],
        json!(32)
    );
    assert_eq!(request.body["generationConfig"]["topP"], json!(0.9));
    assert_eq!(
        request.body["generationConfig"]["thinkingConfig"]["thinkingBudget"],
        json!(256)
    );

    assert_eq!(result.content, "78");
    assert_eq!(result.model, "gemini-2.5-flash-002");
    assert_eq!(result.usage.input_tokens, 10);
    assert_eq!(result.usage.output_tokens, 4);
    assert_eq!(result.usage.total_tokens, 14);
    assert_eq!(
        result
            .headers
            .as_ref()
            .and_then(|headers| headers.get("x-request-id")),
        Some(&"gemini-1".to_string())
    );
}

#[cfg(feature = "helpers-gemini")]
#[tokio::test]
async fn gemini_helper_surfaces_provider_error_headers() {
    let (base_url, _request_rx, server) = spawn_json_server(
        "429 Too Many Requests",
        &[("retry-after", "4")],
        json!({
            "error": { "message": "quota exceeded" }
        }),
    )
    .await;

    let helper = GeminiChatHelper::new()
        .with_base_url(format!("{base_url}/v1beta"))
        .expect("base url should validate");
    let error = helper
        .dispatch(DispatchContext {
            provider: "gemini".to_string(),
            model: "gemini-2.5-flash".to_string(),
            api_key: "test-key".to_string(),
            messages: vec![json!({ "role": "user", "content": "Hello" })],
            kwargs: serde_json::Map::new(),
            config: json!({}),
        })
        .await
        .expect_err("helper should return provider error");

    server.await.expect("test server should complete");

    match error {
        LlmixError::Provider(provider_error) => {
            assert_eq!(provider_error.message, "quota exceeded");
            assert_eq!(provider_error.status_code, Some(429));
            assert_eq!(
                provider_error
                    .headers
                    .as_ref()
                    .and_then(|headers| headers.get("retry-after")),
                Some(&"4".to_string())
            );
        }
        other => panic!("expected provider error, got {other}"),
    }
}

#[cfg(feature = "helpers-sno-gpu")]
fn fast_helper_pipeline<D>(dispatch: D) -> PipelineConfig
where
    D: DispatchFn + 'static,
{
    let mut config = PipelineConfig::new(dispatch);
    config.max_retries = 1;
    config.retry_base_ms = 0;
    config.retry_max_delay_ms = 0;
    config.retry_jitter_ms = 0;
    config.retry_max_retry_after_ms = 0;
    config.semaphore_initial = 4;
    config.semaphore_min = 1;
    config
}

#[cfg(feature = "helpers-sno-gpu")]
#[tokio::test]
async fn sno_gpu_helper_injects_internal_token_and_thinking_payload() {
    let (base_url, request_rx, server) = spawn_json_server(
        "200 OK",
        &[],
        json!({
            "model": "qwen3.5-27b-reason",
            "choices": [{
                "message": { "content": "final answer" }
            }],
            "usage": {
                "prompt_tokens": 13,
                "completion_tokens": 9,
                "total_tokens": 22
            }
        }),
    )
    .await;

    let pipeline = CallPipeline::new(fast_helper_pipeline(
        SnoGpuChatHelper::new().with_internal_token("internal-secret"),
    ))
    .expect("pipeline should construct");
    pipeline.set_key_pool(
        "sno-gpu",
        KeyPool::new(vec!["not-needed".to_string()]).expect("key pool should construct"),
    );

    let response = pipeline
        .call(CallInput {
            config: json!({
                "provider": "sno-gpu",
                "model": "qwen3.5-27b-reason",
                "baseUrl": base_url,
                "providerOptions": {
                    "sno-gpu": {
                        "gpuPath": "reason",
                        "enableThinking": true
                    }
                },
                "common": {
                    "maxOutputTokens": 128
                }
            }),
            messages: vec![json!({ "role": "user", "content": "Think step by step." })],
            singleflight_key: None,
        })
        .await;

    let request = request_rx.await.expect("request should be captured");
    server.await.expect("test server should complete");

    assert!(response.success);
    assert_eq!(response.content, "final answer");
    assert_eq!(request.method, "POST");
    assert_eq!(request.path, "/reason/v1/chat/completions");
    assert_eq!(
        request.headers.get("x-internal-token"),
        Some(&"internal-secret".to_string())
    );
    assert_eq!(request.body["model"], json!("qwen3.5-27b-reason"));
    assert_eq!(request.body["max_tokens"], json!(128));
    assert_eq!(request.body["extra_body"]["enable_thinking"], json!(true));
    assert_eq!(
        request.body["extra_body"]["chat_template_kwargs"]["enable_thinking"],
        json!(true)
    );
    assert!(request.body.get("enable_thinking").is_none());
}
