use crate::error::{CircuitOpenError, KillSwitchActiveError};
use crate::{LlmixError, LlmixResult};
use fs2::FileExt;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::fs::{self, File};
use std::future::Future;
use std::io;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime};
use tokio::sync::Notify;

const DEFAULT_FAILURE_THRESHOLD: u32 = 3;
const DEFAULT_COOLDOWN: Duration = Duration::from_secs(30);
const DEFAULT_PERMITTED_HALF_OPEN_CALLS: u32 = 10;
const DEFAULT_BASE_DELAY_MS: u64 = 1_000;
const DEFAULT_MAX_DELAY_MS: u64 = 30_000;
const DEFAULT_JITTER_MS: u64 = 1_000;
const DEFAULT_MAX_RETRY_AFTER_MS: u64 = 60_000;
const MAX_COOLDOWN: Duration = Duration::from_secs(300);
const KILLSWITCH_FILENAME: &str = "killswitch";
const STATE_SUBDIR: &str = "llmix";
const LEGACY_STATE_SUBDIR: &str = "llmix2";

pub fn is_retryable(status_code: u16) -> bool {
    status_code == 408 || status_code == 429 || (500..=599).contains(&status_code)
}

pub fn resolve_state_dir() -> PathBuf {
    if let Ok(value) = std::env::var("LLMIX_STATE_DIR") {
        return PathBuf::from(value);
    }
    if let Ok(xdg) = std::env::var("XDG_STATE_HOME") {
        return PathBuf::from(xdg).join(STATE_SUBDIR);
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_owned());
    PathBuf::from(home).join(".local/state").join(STATE_SUBDIR)
}

fn resolve_legacy_state_dir(current_state_dir: &Path) -> Option<PathBuf> {
    (current_state_dir.file_name().and_then(|name| name.to_str()) == Some(STATE_SUBDIR))
        .then(|| {
            current_state_dir
                .parent()
                .map(|parent| parent.join(LEGACY_STATE_SUBDIR))
        })
        .flatten()
}

fn migrate_legacy_killswitch(current_state_dir: &Path) -> io::Result<PathBuf> {
    let current_path = current_state_dir.join(KILLSWITCH_FILENAME);
    let Some(legacy_state_dir) = resolve_legacy_state_dir(current_state_dir) else {
        return Ok(current_path);
    };

    if current_path.exists() {
        return Ok(current_path);
    }

    let legacy_path = legacy_state_dir.join(KILLSWITCH_FILENAME);
    if !legacy_path.exists() {
        return Ok(current_path);
    }

    fs::create_dir_all(current_state_dir)?;
    fs::rename(&legacy_path, &current_path)?;
    Ok(current_path)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CircuitState {
    Closed,
    Open,
    HalfOpen,
}

#[derive(Debug)]
struct CircuitInner {
    state: CircuitState,
    consecutive_failures: u32,
    opened_at: Option<Instant>,
    cooldown: Duration,
    half_open_active: u32,
    half_open_successes: u32,
    half_open_failures: u32,
}

#[derive(Debug)]
pub struct CircuitBreaker {
    provider: String,
    base_url: String,
    failure_threshold: u32,
    permitted_half_open_calls: u32,
    base_cooldown: Duration,
    inner: Mutex<CircuitInner>,
}

impl CircuitBreaker {
    pub fn new(provider: impl Into<String>, base_url: impl Into<String>) -> Self {
        Self::with_options(
            provider,
            base_url,
            DEFAULT_FAILURE_THRESHOLD,
            DEFAULT_COOLDOWN,
            DEFAULT_PERMITTED_HALF_OPEN_CALLS,
        )
    }

    pub fn with_options(
        provider: impl Into<String>,
        base_url: impl Into<String>,
        failure_threshold: u32,
        cooldown: Duration,
        permitted_half_open_calls: u32,
    ) -> Self {
        Self {
            provider: provider.into(),
            base_url: base_url.into(),
            failure_threshold,
            permitted_half_open_calls,
            base_cooldown: cooldown,
            inner: Mutex::new(CircuitInner {
                state: CircuitState::Closed,
                consecutive_failures: 0,
                opened_at: None,
                cooldown,
                half_open_active: 0,
                half_open_successes: 0,
                half_open_failures: 0,
            }),
        }
    }

    pub fn state(&self) -> CircuitState {
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");
        transition_open_to_half_open(&mut inner);
        inner.state
    }

    pub fn cooldown(&self) -> Duration {
        self.inner
            .lock()
            .expect("circuit breaker mutex poisoned")
            .cooldown
    }

    pub fn check(&self) -> Result<(), CircuitOpenError> {
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");
        transition_open_to_half_open(&mut inner);

        match inner.state {
            CircuitState::Closed => Ok(()),
            CircuitState::HalfOpen => {
                if inner.half_open_active >= self.permitted_half_open_calls {
                    Err(CircuitOpenError {
                        provider: self.provider.clone(),
                        base_url: self.base_url.clone(),
                    })
                } else {
                    inner.half_open_active += 1;
                    Ok(())
                }
            }
            CircuitState::Open => Err(CircuitOpenError {
                provider: self.provider.clone(),
                base_url: self.base_url.clone(),
            }),
        }
    }

    pub fn on_success(&self) {
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");
        if inner.state == CircuitState::HalfOpen {
            inner.half_open_successes += 1;
            evaluate_half_open(
                &mut inner,
                self.base_cooldown,
                self.permitted_half_open_calls,
            );
            return;
        }
        inner.state = CircuitState::Closed;
        inner.consecutive_failures = 0;
        inner.opened_at = None;
    }

    pub fn on_failure(&self, status_code: Option<u16>, network_error: bool) {
        let retryable = network_error || status_code.is_some_and(is_retryable);
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");

        if inner.state == CircuitState::HalfOpen {
            if retryable {
                inner.half_open_failures += 1;
            } else {
                inner.half_open_successes += 1;
            }
            evaluate_half_open(
                &mut inner,
                self.base_cooldown,
                self.permitted_half_open_calls,
            );
            return;
        }

        if matches!(status_code, Some(401 | 403)) {
            inner.consecutive_failures = 0;
            return;
        }

        if !retryable {
            inner.consecutive_failures = 0;
            return;
        }

        inner.consecutive_failures += 1;
        if inner.consecutive_failures >= self.failure_threshold {
            inner.state = CircuitState::Open;
            inner.opened_at = Some(Instant::now());
        }
    }

    pub fn cancel_probe(&self) {
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");
        if inner.state != CircuitState::HalfOpen {
            return;
        }
        let total_finalized = inner.half_open_successes + inner.half_open_failures;
        if total_finalized >= inner.half_open_active {
            return;
        }
        inner.half_open_failures += 1;
        evaluate_half_open(
            &mut inner,
            self.base_cooldown,
            self.permitted_half_open_calls,
        );
    }

    pub fn reset(&self) {
        let mut inner = self.inner.lock().expect("circuit breaker mutex poisoned");
        inner.state = CircuitState::Closed;
        inner.consecutive_failures = 0;
        inner.opened_at = None;
        inner.cooldown = self.base_cooldown;
        inner.half_open_active = 0;
        inner.half_open_successes = 0;
        inner.half_open_failures = 0;
    }
}

fn transition_open_to_half_open(inner: &mut CircuitInner) {
    if inner.state != CircuitState::Open {
        return;
    }
    let Some(opened_at) = inner.opened_at else {
        return;
    };
    if opened_at.elapsed() >= inner.cooldown {
        inner.state = CircuitState::HalfOpen;
        inner.opened_at = None;
        inner.half_open_active = 0;
        inner.half_open_successes = 0;
        inner.half_open_failures = 0;
    }
}

fn evaluate_half_open(
    inner: &mut CircuitInner,
    base_cooldown: Duration,
    permitted_half_open_calls: u32,
) {
    let total_completed = inner.half_open_successes + inner.half_open_failures;
    if total_completed < permitted_half_open_calls {
        return;
    }

    if inner.half_open_successes > inner.half_open_failures {
        inner.state = CircuitState::Closed;
        inner.consecutive_failures = 0;
        inner.opened_at = None;
        inner.cooldown = base_cooldown;
    } else {
        inner.state = CircuitState::Open;
        inner.opened_at = Some(Instant::now());
        inner.cooldown = (inner.cooldown * 2).min(MAX_COOLDOWN);
    }
}

#[derive(Debug)]
pub struct KillSwitch {
    path: PathBuf,
}

impl KillSwitch {
    pub fn new() -> io::Result<Self> {
        Self::with_state_dir(resolve_state_dir())
    }

    pub fn with_state_dir(path: impl AsRef<Path>) -> io::Result<Self> {
        Ok(Self {
            path: migrate_legacy_killswitch(path.as_ref())?,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn check(&self) -> LlmixResult<()> {
        match fs::metadata(&self.path) {
            Ok(_) => Err(KillSwitchActiveError {
                path: self.path.display().to_string(),
            }
            .into()),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(err) => Err(err.into()),
        }
    }

    pub fn is_active(&self) -> LlmixResult<bool> {
        match fs::metadata(&self.path) {
            Ok(_) => Ok(true),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(err) => Err(err.into()),
        }
    }

    pub async fn check_async(&self) -> LlmixResult<()> {
        match tokio::fs::metadata(&self.path).await {
            Ok(_) => Err(KillSwitchActiveError {
                path: self.path.display().to_string(),
            }
            .into()),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(err) => Err(err.into()),
        }
    }

    pub async fn is_active_async(&self) -> LlmixResult<bool> {
        match tokio::fs::metadata(&self.path).await {
            Ok(_) => Ok(true),
            Err(err) if err.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(err) => Err(err.into()),
        }
    }
}

pub type SharedCallResult<T, E> = Result<Arc<T>, Arc<E>>;

#[derive(Debug)]
struct FlightEntry<T, E> {
    notify: Notify,
    result: Mutex<Option<SharedCallResult<T, E>>>,
}

impl<T, E> FlightEntry<T, E> {
    fn new() -> Self {
        Self {
            notify: Notify::new(),
            result: Mutex::new(None),
        }
    }
}

#[derive(Debug, Default)]
pub struct Singleflight<T, E> {
    in_flight: Mutex<HashMap<String, Arc<FlightEntry<T, E>>>>,
}

impl<T, E> Singleflight<T, E>
where
    T: Send + Sync + 'static,
    E: Send + Sync + 'static,
{
    pub fn new() -> Self {
        Self {
            in_flight: Mutex::new(HashMap::new()),
        }
    }

    pub fn make_key(data: &str) -> String {
        format!("{:x}", Sha256::digest(data.as_bytes()))
    }

    pub async fn do_call<F, Fut>(&self, key: impl Into<String>, func: F) -> SharedCallResult<T, E>
    where
        F: FnOnce() -> Fut,
        Fut: Future<Output = Result<T, E>> + Send,
    {
        let key = key.into();
        let (entry, is_leader) = {
            let mut in_flight = self.in_flight.lock().expect("singleflight mutex poisoned");
            if let Some(existing) = in_flight.get(&key) {
                (existing.clone(), false)
            } else {
                let entry = Arc::new(FlightEntry::new());
                in_flight.insert(key.clone(), entry.clone());
                (entry, true)
            }
        };

        if is_leader {
            let result = func().await.map(Arc::new).map_err(Arc::new);
            {
                let mut slot = entry
                    .result
                    .lock()
                    .expect("singleflight result mutex poisoned");
                *slot = Some(result.clone());
            }
            self.in_flight
                .lock()
                .expect("singleflight mutex poisoned")
                .remove(&key);
            entry.notify.notify_waiters();
            return result;
        }

        loop {
            if let Some(result) = entry
                .result
                .lock()
                .expect("singleflight result mutex poisoned")
                .clone()
            {
                return result;
            }
            entry.notify.notified().await;
        }
    }

    pub fn in_flight_count(&self) -> usize {
        self.in_flight
            .lock()
            .expect("singleflight mutex poisoned")
            .len()
    }
}

pub fn calculate_delay(attempt: u32, base_ms: u64, max_delay_ms: u64, jitter_ms: u64) -> u64 {
    let factor = 1_u64.checked_shl(attempt.min(63)).unwrap_or(u64::MAX);
    let exponential = base_ms.saturating_mul(factor).min(max_delay_ms);
    let jitter = if jitter_ms == 0 {
        0
    } else {
        let nanos = SystemTime::now()
            .duration_since(SystemTime::UNIX_EPOCH)
            .map(|duration| duration.subsec_nanos() as u64)
            .unwrap_or(0);
        nanos % (jitter_ms + 1)
    };
    exponential.saturating_add(jitter)
}

pub fn parse_retry_after(header_value: Option<&str>, max_ms: u64) -> Option<u64> {
    let value = header_value?.trim();

    if let Ok(seconds) = value.parse::<u64>() {
        return Some((seconds * 1_000).min(max_ms));
    }

    let parsed = httpdate::parse_http_date(value).ok()?;
    let delta = parsed.duration_since(SystemTime::now()).ok()?;
    Some(delta.as_millis().min(max_ms as u128) as u64)
}

#[derive(Debug, Clone, Copy)]
pub struct RetryPolicyOptions {
    pub max_retries: u32,
    pub base_ms: u64,
    pub max_delay_ms: u64,
    pub jitter_ms: u64,
    pub max_retry_after_ms: u64,
}

impl Default for RetryPolicyOptions {
    fn default() -> Self {
        Self {
            max_retries: 3,
            base_ms: DEFAULT_BASE_DELAY_MS,
            max_delay_ms: DEFAULT_MAX_DELAY_MS,
            jitter_ms: DEFAULT_JITTER_MS,
            max_retry_after_ms: DEFAULT_MAX_RETRY_AFTER_MS,
        }
    }
}

#[derive(Debug, Clone, Copy)]
pub struct RetryPolicy {
    options: RetryPolicyOptions,
}

impl RetryPolicy {
    pub fn new(options: RetryPolicyOptions) -> LlmixResult<Self> {
        if options.max_delay_ms < options.base_ms {
            return Err(LlmixError::InvalidRetryPolicyConfig(
                "max_delay_ms must be >= base_ms".to_owned(),
            ));
        }
        Ok(Self { options })
    }

    pub fn with_defaults() -> Self {
        Self::new(RetryPolicyOptions::default())
            .expect("default retry policy configuration must be valid")
    }

    pub fn get_delay_ms(&self, attempt: u32, retry_after_header: Option<&str>) -> u64 {
        parse_retry_after(retry_after_header, self.options.max_retry_after_ms).unwrap_or_else(
            || {
                calculate_delay(
                    attempt,
                    self.options.base_ms,
                    self.options.max_delay_ms,
                    self.options.jitter_ms,
                )
            },
        )
    }

    pub async fn execute<T, E, F, Fut>(&self, mut func: F) -> Result<T, E>
    where
        F: FnMut() -> Fut,
        Fut: Future<Output = Result<T, E>>,
    {
        self.execute_with_hooks(
            &mut func,
            None::<fn(&E) -> bool>,
            None::<fn(&E) -> Option<String>>,
        )
        .await
    }

    pub async fn execute_with_hooks<T, E, F, Fut, P, H>(
        &self,
        mut func: F,
        is_retryable_fn: Option<P>,
        retry_after_header: Option<H>,
    ) -> Result<T, E>
    where
        F: FnMut() -> Fut,
        Fut: Future<Output = Result<T, E>>,
        P: Fn(&E) -> bool,
        H: Fn(&E) -> Option<String>,
    {
        for attempt in 0..=self.options.max_retries {
            match func().await {
                Ok(value) => return Ok(value),
                Err(err) => {
                    if attempt >= self.options.max_retries {
                        return Err(err);
                    }
                    if let Some(predicate) = &is_retryable_fn {
                        if !predicate(&err) {
                            return Err(err);
                        }
                    }
                    let retry_after = retry_after_header
                        .as_ref()
                        .and_then(|extractor| extractor(&err));
                    let delay = self.get_delay_ms(attempt, retry_after.as_deref());
                    tokio::time::sleep(Duration::from_millis(delay)).await;
                }
            }
        }

        unreachable!("retry loop always returns or errors")
    }
}

#[derive(Debug)]
pub struct FileLock {
    enabled: bool,
    lock_path: Option<PathBuf>,
    file: Mutex<Option<File>>,
}

#[derive(Debug)]
pub struct FileLockGuard<'a> {
    file_lock: &'a FileLock,
    released: bool,
}

impl FileLock {
    pub fn new() -> LlmixResult<Self> {
        Self::with_path(resolve_state_dir().join("llmix.lock"))
    }

    pub fn with_path(path: impl Into<PathBuf>) -> LlmixResult<Self> {
        let concurrency = std::env::var("LLM_GLOBAL_CONCURRENCY").ok();
        let enabled = concurrency
            .as_ref()
            .is_some_and(|value| !value.trim().is_empty());

        if let Some(value) = concurrency
            .as_deref()
            .filter(|value| !value.trim().is_empty())
        {
            if value
                .trim()
                .parse::<u32>()
                .ok()
                .filter(|parsed| *parsed > 0)
                .is_none()
            {
                return Err(LlmixError::InvalidFileLockConfig(format!(
                    "LLM_GLOBAL_CONCURRENCY must be a positive integer, got \"{value}\""
                )));
            }
        }

        Ok(Self {
            enabled,
            lock_path: enabled.then_some(path.into()),
            file: Mutex::new(None),
        })
    }

    pub fn enabled(&self) -> bool {
        self.enabled
    }

    pub fn lock_path(&self) -> Option<&Path> {
        self.lock_path.as_deref()
    }

    pub fn acquire(&self) -> LlmixResult<()> {
        if !self.enabled {
            return Ok(());
        }

        let path = self
            .lock_path
            .as_ref()
            .expect("enabled file lock must have a path");
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        let file = File::options()
            .create(true)
            .read(true)
            .write(true)
            .open(path)?;
        file.lock_exclusive()?;
        *self.file.lock().expect("file lock mutex poisoned") = Some(file);
        Ok(())
    }

    pub fn acquire_guard(&self) -> LlmixResult<FileLockGuard<'_>> {
        self.acquire()?;
        Ok(FileLockGuard {
            file_lock: self,
            released: false,
        })
    }

    pub fn release(&self) -> LlmixResult<()> {
        let maybe_file = self.file.lock().expect("file lock mutex poisoned").take();
        if let Some(file) = maybe_file {
            file.unlock()?;
        }
        Ok(())
    }
}

impl FileLockGuard<'_> {
    pub fn release(mut self) -> LlmixResult<()> {
        if self.released {
            return Ok(());
        }
        self.released = true;
        self.file_lock.release()
    }
}

impl Drop for FileLockGuard<'_> {
    fn drop(&mut self) {
        if !self.released {
            let _ = self.file_lock.release();
            self.released = true;
        }
    }
}

impl Drop for FileLock {
    fn drop(&mut self) {
        if let Some(file) = self
            .file
            .get_mut()
            .expect("file lock mutex poisoned")
            .take()
        {
            let _ = file.unlock();
        }
    }
}
