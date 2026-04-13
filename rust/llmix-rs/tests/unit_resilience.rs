use httpdate::fmt_http_date;
use llmix_rs::{
    calculate_delay, is_retryable, parse_retry_after, CircuitBreaker, CircuitState, FileLock,
    KillSwitch, LlmixError, RetryPolicy, RetryPolicyOptions, Singleflight,
};
use serde::Deserialize;
use std::collections::HashMap;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

#[derive(Debug, Deserialize)]
struct CircuitFixtureFile {
    #[serde(rename = "retryableStatusCodes")]
    retryable_status_codes: Vec<u16>,
    #[serde(rename = "nonRetryableStatusCodes")]
    non_retryable_status_codes: Vec<u16>,
    scenarios: HashMap<String, CircuitScenario>,
    #[serde(rename = "retryDelayScenarios")]
    retry_delay_scenarios: RetryDelayScenarios,
}

#[derive(Debug, Deserialize)]
struct CircuitScenario {
    #[serde(rename = "initialState")]
    initial_state: String,
    actions: Vec<CircuitAction>,
    #[serde(rename = "expectedState")]
    expected_state: String,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
enum CircuitAction {
    #[serde(rename = "failure")]
    Failure {
        #[serde(rename = "statusCode")]
        status_code: Option<u16>,
        #[serde(rename = "networkError")]
        network_error: Option<bool>,
    },
    #[serde(rename = "success")]
    Success,
    #[serde(rename = "check")]
    Check {
        #[serde(rename = "expectError")]
        expect_error: bool,
    },
}

#[derive(Debug, Deserialize)]
struct RetryDelayScenarios {
    attempts: Vec<RetryDelayAttempt>,
}

#[derive(Debug, Deserialize)]
struct RetryDelayAttempt {
    attempt: u32,
    #[serde(rename = "minMs")]
    min_ms: u64,
    #[serde(rename = "maxMs")]
    max_ms: u64,
}

struct TestTempDir {
    path: PathBuf,
}

impl TestTempDir {
    fn new(prefix: &str) -> Self {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("system time should be after epoch")
            .as_nanos();
        let path = env::temp_dir().join(format!("llmix-rs-{prefix}-{}-{unique}", process::id()));
        fs::create_dir_all(&path).expect("temp dir should be created");
        Self { path }
    }

    fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for TestTempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}

fn env_lock() -> std::sync::MutexGuard<'static, ()> {
    static ENV_LOCK: OnceLock<Mutex<()>> = OnceLock::new();
    ENV_LOCK
        .get_or_init(|| Mutex::new(()))
        .lock()
        .expect("env mutex poisoned")
}

fn restore_var(name: &str, original: Option<String>) {
    match original {
        Some(value) => env::set_var(name, value),
        None => env::remove_var(name),
    }
}

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../tests/fixtures/circuit-breaker-scenarios.json")
}

fn load_fixture() -> CircuitFixtureFile {
    let raw = fs::read_to_string(fixture_path()).expect("shared resilience fixture should load");
    serde_json::from_str(&raw).expect("shared resilience fixture should parse")
}

fn state_name(state: CircuitState) -> &'static str {
    match state {
        CircuitState::Closed => "CLOSED",
        CircuitState::Open => "OPEN",
        CircuitState::HalfOpen => "HALF_OPEN",
    }
}

fn breaker_for_initial_state(initial_state: &str) -> CircuitBreaker {
    match initial_state {
        "CLOSED" => CircuitBreaker::new("openai", "https://api.openai.com"),
        "HALF_OPEN" => {
            let breaker = CircuitBreaker::with_options(
                "openai",
                "https://api.openai.com",
                3,
                Duration::from_millis(5),
                1,
            );
            breaker.on_failure(Some(500), false);
            breaker.on_failure(Some(500), false);
            breaker.on_failure(Some(500), false);
            std::thread::sleep(Duration::from_millis(10));
            assert_eq!(breaker.state(), CircuitState::HalfOpen);
            breaker
        }
        other => panic!("unsupported fixture initial state: {other}"),
    }
}

#[test]
fn circuit_breaker_scenarios_match_shared_fixture_contract() {
    let fixture = load_fixture();

    for (name, scenario) in fixture.scenarios {
        let breaker = breaker_for_initial_state(&scenario.initial_state);

        for action in scenario.actions {
            match action {
                CircuitAction::Failure {
                    status_code,
                    network_error,
                } => breaker.on_failure(status_code, network_error.unwrap_or(false)),
                CircuitAction::Success => breaker.on_success(),
                CircuitAction::Check { expect_error } => {
                    assert_eq!(
                        breaker.check().is_err(),
                        expect_error,
                        "scenario {name} check result"
                    );
                }
            }
        }

        assert_eq!(
            state_name(breaker.state()),
            scenario.expected_state,
            "scenario {name}"
        );
    }
}

#[test]
fn retryable_status_sets_match_shared_fixture_contract() {
    let fixture = load_fixture();

    for status in fixture.retryable_status_codes {
        assert!(is_retryable(status), "status {status} should be retryable");
    }

    for status in fixture.non_retryable_status_codes {
        assert!(
            !is_retryable(status),
            "status {status} should not be retryable"
        );
    }
}

#[test]
fn retry_delay_ranges_match_shared_fixture_contract() {
    let fixture = load_fixture();

    for attempt in fixture.retry_delay_scenarios.attempts {
        let delay = calculate_delay(attempt.attempt, 1_000, 30_000, 1_000);
        assert!(
            (attempt.min_ms..=attempt.max_ms).contains(&delay),
            "attempt {} delay {} should be within {}..={}",
            attempt.attempt,
            delay,
            attempt.min_ms,
            attempt.max_ms
        );
    }
}

#[test]
fn circuit_breaker_multi_probe_majority_success_closes() {
    let breaker = CircuitBreaker::with_options(
        "sno-gpu",
        "http://gpu:8080",
        3,
        Duration::from_millis(10),
        3,
    );

    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    std::thread::sleep(Duration::from_millis(15));

    assert_eq!(breaker.state(), CircuitState::HalfOpen);
    breaker.check().unwrap();
    breaker.check().unwrap();
    breaker.check().unwrap();

    breaker.on_success();
    breaker.on_success();
    breaker.on_failure(Some(500), false);

    assert_eq!(breaker.state(), CircuitState::Closed);
}

#[test]
fn cancel_probe_after_failure_does_not_double_count() {
    let breaker = CircuitBreaker::with_options(
        "sno-gpu",
        "http://gpu:8080",
        3,
        Duration::from_millis(10),
        3,
    );

    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    std::thread::sleep(Duration::from_millis(15));

    breaker.check().unwrap();
    breaker.check().unwrap();
    breaker.check().unwrap();

    breaker.on_failure(Some(500), false);
    breaker.cancel_probe();
    breaker.on_success();
    breaker.on_success();

    assert_eq!(breaker.state(), CircuitState::Closed);
}

#[test]
fn reopen_doubles_cooldown_and_success_resets_it() {
    let base_cooldown = Duration::from_millis(10);
    let breaker =
        CircuitBreaker::with_options("openai", "https://api.openai.com", 3, base_cooldown, 1);

    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    breaker.on_failure(Some(500), false);
    std::thread::sleep(Duration::from_millis(15));

    breaker.check().unwrap();
    breaker.on_failure(Some(500), false);
    assert_eq!(breaker.state(), CircuitState::Open);
    assert_eq!(breaker.cooldown(), Duration::from_millis(20));

    std::thread::sleep(Duration::from_millis(25));
    breaker.check().unwrap();
    breaker.on_success();
    assert_eq!(breaker.state(), CircuitState::Closed);
    assert_eq!(breaker.cooldown(), base_cooldown);
}

#[test]
fn kill_switch_inactive_active_env_and_migration_paths_work() {
    let temp = TestTempDir::new("killswitch");
    let state_dir = temp.path().join("llmix");

    let inactive = KillSwitch::with_state_dir(&state_dir).unwrap();
    assert!(!inactive.is_active().unwrap());
    inactive.check().unwrap();

    fs::create_dir_all(&state_dir).unwrap();
    let active_path = state_dir.join("killswitch");
    fs::write(&active_path, "").unwrap();

    let active = KillSwitch::with_state_dir(&state_dir).unwrap();
    assert!(active.is_active().unwrap());
    assert!(matches!(
        active.check().unwrap_err(),
        LlmixError::KillSwitchActive(_)
    ));

    let _guard = env_lock();
    let old_state_dir = env::var("LLMIX_STATE_DIR").ok();
    env::set_var("LLMIX_STATE_DIR", state_dir.display().to_string());
    let resolved = KillSwitch::new().unwrap();
    assert_eq!(resolved.path(), active_path.as_path());
    restore_var("LLMIX_STATE_DIR", old_state_dir);

    let migration_root = TestTempDir::new("killswitch-migrate");
    let legacy_dir = migration_root.path().join("llmix2");
    let current_dir = migration_root.path().join("llmix");
    fs::create_dir_all(&legacy_dir).unwrap();
    let legacy_path = legacy_dir.join("killswitch");
    fs::write(&legacy_path, "").unwrap();

    let migrated = KillSwitch::with_state_dir(&current_dir).unwrap();
    assert_eq!(migrated.path(), current_dir.join("killswitch").as_path());
    assert!(migrated.path().exists());
    assert!(!legacy_path.exists());
}

#[tokio::test]
async fn kill_switch_async_checks_match_sync_checks() {
    let temp = TestTempDir::new("killswitch-async");
    let state_dir = temp.path().join("llmix");
    fs::create_dir_all(&state_dir).unwrap();
    fs::write(state_dir.join("killswitch"), "").unwrap();

    let kill_switch = KillSwitch::with_state_dir(&state_dir).unwrap();
    assert!(kill_switch.is_active_async().await.unwrap());
    assert!(matches!(
        kill_switch.check_async().await.unwrap_err(),
        LlmixError::KillSwitchActive(_)
    ));
}

#[tokio::test]
async fn singleflight_deduplicates_results_and_cleans_up() {
    let singleflight = Singleflight::<String, String>::new();
    let call_count = Arc::new(AtomicUsize::new(0));
    let key = Singleflight::<String, String>::make_key("same-request");

    let fut1 = singleflight.do_call(key.clone(), {
        let call_count = call_count.clone();
        move || async move {
            call_count.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(25)).await;
            Ok("result".to_owned())
        }
    });
    let fut2 = singleflight.do_call(key.clone(), {
        let call_count = call_count.clone();
        move || async move {
            call_count.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(25)).await;
            Ok("result".to_owned())
        }
    });
    let fut3 = singleflight.do_call(key, {
        let call_count = call_count.clone();
        move || async move {
            call_count.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(25)).await;
            Ok("result".to_owned())
        }
    });

    let (first, second, third) = tokio::join!(fut1, fut2, fut3);
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
    assert_eq!(first.unwrap().as_ref(), "result");
    assert_eq!(second.unwrap().as_ref(), "result");
    assert_eq!(third.unwrap().as_ref(), "result");
    assert_eq!(singleflight.in_flight_count(), 0);
}

#[tokio::test]
async fn singleflight_propagates_errors_to_all_waiters() {
    let singleflight = Singleflight::<String, String>::new();
    let call_count = Arc::new(AtomicUsize::new(0));
    let key = Singleflight::<String, String>::make_key("same-error");

    let fut1 = singleflight.do_call(key.clone(), {
        let call_count = call_count.clone();
        move || async move {
            call_count.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(10)).await;
            Err("boom".to_owned())
        }
    });
    let fut2 = singleflight.do_call(key, {
        let call_count = call_count.clone();
        move || async move {
            call_count.fetch_add(1, Ordering::SeqCst);
            tokio::time::sleep(Duration::from_millis(10)).await;
            Err("boom".to_owned())
        }
    });

    let (first, second) = tokio::join!(fut1, fut2);
    assert_eq!(call_count.load(Ordering::SeqCst), 1);
    assert_eq!(first.unwrap_err().as_ref(), "boom");
    assert_eq!(second.unwrap_err().as_ref(), "boom");
    assert_eq!(singleflight.in_flight_count(), 0);
}

#[test]
fn parse_retry_after_supports_seconds_http_dates_and_caps() {
    assert_eq!(parse_retry_after(Some("3"), 60_000), Some(3_000));
    assert_eq!(parse_retry_after(Some("-1"), 60_000), None);

    let future = SystemTime::now() + Duration::from_secs(120);
    assert_eq!(
        parse_retry_after(Some(&fmt_http_date(future)), 60_000),
        Some(60_000)
    );
}

#[tokio::test]
async fn retry_policy_retries_until_success() {
    let policy = RetryPolicy::new(RetryPolicyOptions {
        max_retries: 3,
        base_ms: 0,
        max_delay_ms: 0,
        jitter_ms: 0,
        max_retry_after_ms: 0,
    })
    .unwrap();
    let attempts = Arc::new(AtomicUsize::new(0));

    let result = policy
        .execute({
            let attempts = attempts.clone();
            move || {
                let attempts = attempts.clone();
                async move {
                    let current = attempts.fetch_add(1, Ordering::SeqCst);
                    if current < 2 {
                        Err("retryable".to_owned())
                    } else {
                        Ok("done".to_owned())
                    }
                }
            }
        })
        .await;

    assert_eq!(result.unwrap(), "done");
    assert_eq!(attempts.load(Ordering::SeqCst), 3);
}

#[tokio::test]
async fn retry_policy_stops_when_predicate_marks_error_non_retryable() {
    let policy = RetryPolicy::new(RetryPolicyOptions {
        max_retries: 3,
        base_ms: 0,
        max_delay_ms: 0,
        jitter_ms: 0,
        max_retry_after_ms: 0,
    })
    .unwrap();
    let attempts = Arc::new(AtomicUsize::new(0));

    let result: Result<String, String> = policy
        .execute_with_hooks(
            {
                let attempts = attempts.clone();
                move || {
                    let attempts = attempts.clone();
                    async move {
                        attempts.fetch_add(1, Ordering::SeqCst);
                        Err("fatal".to_owned())
                    }
                }
            },
            Some(|err: &String| err == "retryable"),
            None::<fn(&String) -> Option<String>>,
        )
        .await;

    assert_eq!(result.unwrap_err(), "fatal");
    assert_eq!(attempts.load(Ordering::SeqCst), 1);
}

#[test]
fn retry_policy_validates_delay_bounds() {
    let err = RetryPolicy::new(RetryPolicyOptions {
        max_retries: 1,
        base_ms: 10,
        max_delay_ms: 5,
        jitter_ms: 0,
        max_retry_after_ms: 0,
    })
    .unwrap_err();

    assert!(matches!(err, LlmixError::InvalidRetryPolicyConfig(_)));
}

#[test]
fn file_lock_is_disabled_without_env_and_rejects_invalid_values() {
    let _guard = env_lock();
    let old = env::var("LLM_GLOBAL_CONCURRENCY").ok();
    env::remove_var("LLM_GLOBAL_CONCURRENCY");

    let disabled = FileLock::with_path("ignored.lock").unwrap();
    assert!(!disabled.enabled());
    assert!(disabled.lock_path().is_none());

    env::set_var("LLM_GLOBAL_CONCURRENCY", "0");
    let err = FileLock::with_path("ignored.lock").unwrap_err();
    assert!(matches!(err, LlmixError::InvalidFileLockConfig(_)));

    restore_var("LLM_GLOBAL_CONCURRENCY", old);
}

#[test]
fn file_lock_acquires_and_releases_when_enabled() {
    let _guard = env_lock();
    let temp = TestTempDir::new("file-lock");
    let lock_path = temp.path().join("llmix.lock");
    let old = env::var("LLM_GLOBAL_CONCURRENCY").ok();

    env::set_var("LLM_GLOBAL_CONCURRENCY", "4");
    let lock = FileLock::with_path(lock_path.clone()).unwrap();

    assert!(lock.enabled());
    assert_eq!(lock.lock_path(), Some(lock_path.as_path()));
    lock.acquire().unwrap();
    assert!(lock_path.exists());
    lock.release().unwrap();

    restore_var("LLM_GLOBAL_CONCURRENCY", old);
}
