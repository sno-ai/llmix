use crate::{AdaptiveSemaphoreClosedError, LlmixError, LlmixResult};
use std::collections::HashMap;
use std::sync::Mutex;
use tokio::sync::Notify;

pub const DEFAULT_INITIAL: usize = 32;
pub const DEFAULT_MIN_CONCURRENCY: usize = 4;
pub const HEADER_BACKOFF_THRESHOLD: f64 = 0.10;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RateLimitHeaders {
    pub remaining: usize,
    pub limit: usize,
}

#[derive(Debug)]
struct AdaptiveSemaphoreState {
    window: usize,
    available: usize,
    has_header_signal: bool,
    permits_to_absorb: usize,
    waiters: usize,
    closed: bool,
}

#[derive(Debug)]
pub struct AdaptiveSemaphore {
    max: usize,
    min: usize,
    state: Mutex<AdaptiveSemaphoreState>,
    notify: Notify,
}

#[derive(Debug)]
pub struct AdaptiveSemaphorePermit<'a> {
    semaphore: &'a AdaptiveSemaphore,
    released: bool,
}

impl AdaptiveSemaphore {
    pub fn new(initial: usize, min_concurrency: usize) -> LlmixResult<Self> {
        if initial < 1 {
            return Err(LlmixError::InvalidAdaptiveSemaphoreConfig(format!(
                "initial must be >= 1, got {initial}"
            )));
        }
        if min_concurrency < 1 {
            return Err(LlmixError::InvalidAdaptiveSemaphoreConfig(format!(
                "min_concurrency must be >= 1, got {min_concurrency}"
            )));
        }
        if initial < min_concurrency {
            return Err(LlmixError::InvalidAdaptiveSemaphoreConfig(format!(
                "initial ({initial}) must be >= min_concurrency ({min_concurrency})"
            )));
        }

        Ok(Self {
            max: initial,
            min: min_concurrency,
            state: Mutex::new(AdaptiveSemaphoreState {
                window: initial,
                available: initial,
                has_header_signal: false,
                permits_to_absorb: 0,
                waiters: 0,
                closed: false,
            }),
            notify: Notify::new(),
        })
    }

    pub fn with_defaults() -> Self {
        Self::new(DEFAULT_INITIAL, DEFAULT_MIN_CONCURRENCY)
            .expect("default adaptive semaphore configuration must be valid")
    }

    pub fn window(&self) -> usize {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).window
    }

    pub fn max_concurrency(&self) -> usize {
        self.max
    }

    pub fn min_concurrency(&self) -> usize {
        self.min
    }

    pub fn closed(&self) -> bool {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).closed
    }

    pub fn rebind(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let outstanding = state
            .window
            .saturating_sub(state.available)
            .saturating_add(state.permits_to_absorb);
        state.available = state.window.saturating_sub(outstanding.min(state.window));
        state.permits_to_absorb = outstanding.saturating_sub(state.window);
        let waiters = state.waiters;
        drop(state);
        self.notify_n(waiters);
    }

    pub async fn acquire(&self) -> Result<(), AdaptiveSemaphoreClosedError> {
        loop {
            let notified = self.notify.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            let waiter_guard = {
                let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());

                if state.closed {
                    return Err(AdaptiveSemaphoreClosedError);
                }

                if state.available > 0 {
                    state.available -= 1;
                    return Ok(());
                }

                state.waiters += 1;
                WaiterGuard { semaphore: self }
            };

            notified.await;
            drop(waiter_guard);
        }
    }

    pub async fn acquire_guard(
        &self,
    ) -> Result<AdaptiveSemaphorePermit<'_>, AdaptiveSemaphoreClosedError> {
        self.acquire().await?;
        Ok(AdaptiveSemaphorePermit {
            semaphore: self,
            released: false,
        })
    }

    pub fn close(&self) {
        let waiters = {
            let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
            state.closed = true;
            state.waiters
        };
        self.notify_n(waiters);
    }

    pub fn release(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());

        if state.permits_to_absorb > 0 {
            state.permits_to_absorb -= 1;
            return;
        }

        if state.available < state.window {
            state.available += 1;
            self.notify.notify_one();
        }
    }

    pub fn on_success(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.closed || state.has_header_signal || state.window >= self.max {
            return;
        }
        let target = state.window + 1;
        let added = adjust_window(&mut state, self.min, self.max, target);
        drop(state);
        if added > 0 {
            self.notify_n(added);
        }
    }

    pub fn on_rate_limit(&self) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.closed {
            return;
        }
        let target = (state.window / 2).max(self.min);
        let added = adjust_window(&mut state, self.min, self.max, target);
        state.has_header_signal = false;
        drop(state);
        if added > 0 {
            self.notify_n(added);
        }
    }

    pub fn on_header_feedback(&self, remaining: usize, limit: usize) {
        let mut state = self.state.lock().unwrap_or_else(|e| e.into_inner());
        if state.closed || limit == 0 {
            return;
        }
        state.has_header_signal = true;

        let ratio = remaining as f64 / limit as f64;
        if ratio >= HEADER_BACKOFF_THRESHOLD {
            if state.window < self.max {
                let target = state.window + 1;
                let added = adjust_window(&mut state, self.min, self.max, target);
                drop(state);
                if added > 0 {
                    self.notify_n(added);
                }
            }
            return;
        }

        let scale = ratio / HEADER_BACKOFF_THRESHOLD;
        let target = (self.min as f64 + scale * (self.max - self.min) as f64) as usize;
        let added = adjust_window(&mut state, self.min, self.max, target);
        drop(state);
        if added > 0 {
            self.notify_n(added);
        }
    }

    fn notify_n(&self, count: usize) {
        for _ in 0..count {
            self.notify.notify_one();
        }
    }
}

impl AdaptiveSemaphorePermit<'_> {
    pub fn release(mut self) {
        if !self.released {
            self.semaphore.release();
            self.released = true;
        }
    }
}

struct WaiterGuard<'a> {
    semaphore: &'a AdaptiveSemaphore,
}

impl Drop for WaiterGuard<'_> {
    fn drop(&mut self) {
        let mut state = self
            .semaphore
            .state
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        state.waiters = state.waiters.saturating_sub(1);
    }
}

impl Drop for AdaptiveSemaphorePermit<'_> {
    fn drop(&mut self) {
        if !self.released {
            self.semaphore.release();
            self.released = true;
        }
    }
}

fn adjust_window(
    state: &mut AdaptiveSemaphoreState,
    min: usize,
    max: usize,
    mut target: usize,
) -> usize {
    target = target.max(min).min(max);
    if target == state.window {
        return 0;
    }

    let mut added = 0;
    if target > state.window {
        let mut grow = target - state.window;
        let absorbed = grow.min(state.permits_to_absorb);
        state.permits_to_absorb -= absorbed;
        grow -= absorbed;
        state.available += grow;
        added = grow;
    } else {
        let shrink = state.window - target;
        let immediate = shrink.min(state.available);
        state.available -= immediate;
        state.permits_to_absorb += shrink - immediate;
    }

    state.window = target;
    added
}

pub fn parse_openai_ratelimit_headers(
    headers: &HashMap<String, String>,
) -> Option<RateLimitHeaders> {
    let remaining = get_header_case_insensitive(headers, "x-ratelimit-remaining-requests")?;
    let limit = get_header_case_insensitive(headers, "x-ratelimit-limit-requests")?;

    let remaining = remaining.parse::<usize>().ok()?;
    let limit = limit.parse::<usize>().ok()?;

    (limit > 0).then_some(RateLimitHeaders { remaining, limit })
}

fn get_header_case_insensitive<'a>(
    headers: &'a HashMap<String, String>,
    name: &str,
) -> Option<&'a str> {
    headers
        .iter()
        .find_map(|(key, value)| key.eq_ignore_ascii_case(name).then_some(value.as_str()))
}
