use llmix_rs::adaptive_semaphore::DEFAULT_MIN_CONCURRENCY;
use llmix_rs::{
    parse_openai_ratelimit_headers, AdaptiveSemaphore, AdaptiveSemaphoreClosedError,
    RateLimitHeaders,
};
use serde::Deserialize;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

#[derive(Debug, Deserialize)]
struct AimdFixtureFile {
    scenarios: Vec<AimdScenario>,
    #[serde(rename = "header_parsing")]
    header_parsing: Vec<HeaderParsingCase>,
}

#[derive(Debug, Deserialize)]
struct AimdScenario {
    name: String,
    initial: usize,
    actions: Vec<AimdAction>,
    #[serde(rename = "expected_window")]
    expected_window: usize,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
enum AimdAction {
    Success,
    RateLimit,
    HeaderFeedback { remaining: usize, limit: usize },
}

#[derive(Debug, Deserialize)]
struct HeaderParsingCase {
    name: String,
    headers: HashMap<String, String>,
    expected: Option<ExpectedHeaders>,
}

#[derive(Debug, Deserialize, PartialEq, Eq)]
struct ExpectedHeaders {
    remaining: usize,
    limit: usize,
}

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../../fixtures/llmix/aimd-scenarios.json")
}

fn load_fixture() -> AimdFixtureFile {
    let raw = fs::read_to_string(fixture_path()).expect("shared AIMD fixture should load");
    serde_json::from_str(&raw).expect("shared AIMD fixture should parse")
}

fn apply_action(sem: &AdaptiveSemaphore, action: &AimdAction) {
    match action {
        AimdAction::Success => sem.on_success(),
        AimdAction::RateLimit => sem.on_rate_limit(),
        AimdAction::HeaderFeedback { remaining, limit } => {
            sem.on_header_feedback(*remaining, *limit)
        }
    }
}

#[test]
fn window_evolution_matches_shared_fixture_vectors() {
    let fixture = load_fixture();

    for scenario in fixture.scenarios {
        let sem = AdaptiveSemaphore::new(scenario.initial, DEFAULT_MIN_CONCURRENCY).unwrap();
        for action in &scenario.actions {
            apply_action(&sem, action);
        }

        assert_eq!(
            sem.window(),
            scenario.expected_window,
            "scenario {}",
            scenario.name
        );
    }
}

#[test]
fn header_parsing_matches_shared_fixture_vectors() {
    let fixture = load_fixture();

    for case in fixture.header_parsing {
        let actual = parse_openai_ratelimit_headers(&case.headers).map(|headers| ExpectedHeaders {
            remaining: headers.remaining,
            limit: headers.limit,
        });

        assert_eq!(actual, case.expected, "header case {}", case.name);
    }
}

#[test]
fn default_parameters_match_python_and_typescript_contract() {
    let sem = AdaptiveSemaphore::with_defaults();
    assert_eq!(sem.window(), 32);
    assert_eq!(sem.max_concurrency(), 32);
    assert_eq!(sem.min_concurrency(), 4);
}

#[test]
fn custom_minimum_is_respected_under_repeated_rate_limits() {
    let sem = AdaptiveSemaphore::new(16, 8).unwrap();
    for _ in 0..10 {
        sem.on_rate_limit();
    }
    assert_eq!(sem.window(), 8);
}

#[test]
fn zero_limit_header_feedback_is_ignored() {
    let sem = AdaptiveSemaphore::new(16, DEFAULT_MIN_CONCURRENCY).unwrap();
    sem.on_header_feedback(100, 0);
    assert_eq!(sem.window(), 16);
}

#[test]
fn rebind_preserves_window_state() {
    let sem = AdaptiveSemaphore::with_defaults();
    sem.on_rate_limit();
    assert_eq!(sem.window(), 16);

    sem.rebind();
    assert_eq!(sem.window(), 16);
}

#[tokio::test]
async fn rebind_preserves_in_flight_permits() {
    let sem = Arc::new(AdaptiveSemaphore::new(2, 1).unwrap());
    let first = sem.acquire_guard().await.unwrap();

    sem.rebind();
    sem.acquire().await.unwrap();

    let blocked_sem = sem.clone();
    let blocked = tokio::spawn(async move { blocked_sem.acquire().await });
    tokio::task::yield_now().await;
    assert!(
        !blocked.is_finished(),
        "rebind should not reset capacity while permits are still held"
    );

    first.release();
    let blocked_result = tokio::time::timeout(Duration::from_millis(100), blocked)
        .await
        .expect("blocked acquire should wake after a permit is released")
        .expect("blocked task should join");
    assert_eq!(blocked_result, Ok(()));
}

#[tokio::test]
async fn shrink_absorbs_future_releases_until_capacity_matches_new_window() {
    let sem = Arc::new(AdaptiveSemaphore::new(4, 1).unwrap());
    for _ in 0..4 {
        sem.acquire().await.unwrap();
    }

    sem.on_rate_limit();
    assert_eq!(sem.window(), 2);

    let waiter_sem = sem.clone();
    let waiter = tokio::spawn(async move { waiter_sem.acquire().await });
    tokio::task::yield_now().await;
    assert!(
        !waiter.is_finished(),
        "waiter should block while all permits are held"
    );

    sem.release();
    sem.release();
    tokio::task::yield_now().await;
    assert!(
        !waiter.is_finished(),
        "absorbed releases should not wake waiters before restored capacity"
    );

    sem.release();
    let waiter_result = tokio::time::timeout(Duration::from_millis(100), waiter)
        .await
        .expect("waiter should wake after absorbed releases are exhausted")
        .expect("waiter task should join");
    assert_eq!(waiter_result, Ok(()));

    sem.release();
    sem.release();

    sem.acquire().await.unwrap();
    sem.acquire().await.unwrap();

    let blocked_sem = sem.clone();
    let blocked = tokio::spawn(async move { blocked_sem.acquire().await });
    tokio::task::yield_now().await;
    assert!(
        !blocked.is_finished(),
        "a third acquire should block at the shrunken window"
    );

    sem.close();
    let blocked_result = blocked.await.expect("blocked task should join");
    assert_eq!(blocked_result, Err(AdaptiveSemaphoreClosedError));
}

#[tokio::test]
async fn batched_releases_wake_all_waiting_acquires() {
    let sem = Arc::new(AdaptiveSemaphore::new(8, 1).unwrap());
    for _ in 0..8 {
        sem.acquire().await.unwrap();
    }

    let waiters = (0..8)
        .map(|_| {
            let waiter_sem = sem.clone();
            tokio::spawn(async move { waiter_sem.acquire().await })
        })
        .collect::<Vec<_>>();
    for _ in 0..4 {
        tokio::task::yield_now().await;
    }

    for _ in 0..8 {
        sem.release();
    }

    for waiter in waiters {
        let result = tokio::time::timeout(Duration::from_millis(100), waiter)
            .await
            .expect("released waiter should wake")
            .expect("waiter task should join");
        assert_eq!(result, Ok(()));
    }
}

#[tokio::test]
async fn close_rejects_waiters_and_future_acquires() {
    let sem = Arc::new(AdaptiveSemaphore::new(2, 1).unwrap());
    sem.acquire().await.unwrap();
    sem.acquire().await.unwrap();

    let waiter_sem = sem.clone();
    let waiter = tokio::spawn(async move { waiter_sem.acquire().await });
    tokio::task::yield_now().await;
    assert!(!waiter.is_finished());

    sem.close();

    let waiter_result = waiter.await.expect("waiter should join");
    assert_eq!(waiter_result, Err(AdaptiveSemaphoreClosedError));
    assert_eq!(sem.acquire().await, Err(AdaptiveSemaphoreClosedError));
}

#[test]
fn parsed_header_struct_is_exact() {
    let headers = HashMap::from([
        (
            "x-ratelimit-remaining-requests".to_owned(),
            "450".to_owned(),
        ),
        ("x-ratelimit-limit-requests".to_owned(), "500".to_owned()),
    ]);

    assert_eq!(
        parse_openai_ratelimit_headers(&headers),
        Some(RateLimitHeaders {
            remaining: 450,
            limit: 500,
        })
    );
}

#[test]
fn header_parsing_is_case_insensitive() {
    let headers = HashMap::from([
        ("X-RateLimit-Remaining-Requests".to_owned(), "12".to_owned()),
        ("x-ratelimit-LIMIT-requests".to_owned(), "40".to_owned()),
    ]);

    assert_eq!(
        parse_openai_ratelimit_headers(&headers),
        Some(RateLimitHeaders {
            remaining: 12,
            limit: 40,
        })
    );
}
