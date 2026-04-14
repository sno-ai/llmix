use crate::{AdaptiveSemaphoreClosedError, LlmixError, LlmixResult};
use std::collections::{HashMap, VecDeque};
use std::sync::Mutex;
use tokio::sync::oneshot;

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
    waiters: VecDeque<oneshot::Sender<Result<(), AdaptiveSemaphoreClosedError>>>,
    has_header_signal: bool,
    permits_to_absorb: usize,
    closed: bool,
}

#[derive(Debug)]
pub struct AdaptiveSemaphore {
    max: usize,
    min: usize,
    state: Mutex<AdaptiveSemaphoreState>,
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
                waiters: VecDeque::new(),
                has_header_signal: false,
                permits_to_absorb: 0,
                closed: false,
            }),
        })
    }

    pub fn with_defaults() -> Self {
        Self::new(DEFAULT_INITIAL, DEFAULT_MIN_CONCURRENCY)
            .expect("default adaptive semaphore configuration must be valid")
    }

    pub fn window(&self) -> usize {
        self.state
            .lock()
            .expect("adaptive semaphore mutex poisoned")
            .window
    }

    pub fn max_concurrency(&self) -> usize {
        self.max
    }

    pub fn min_concurrency(&self) -> usize {
        self.min
    }

    pub fn closed(&self) -> bool {
        self.state
            .lock()
            .expect("adaptive semaphore mutex poisoned")
            .closed
    }

    pub fn rebind(&self) {
        let mut state = self
            .state
            .lock()
            .expect("adaptive semaphore mutex poisoned");
        state.available = state.window;
        state.waiters.clear();
        state.permits_to_absorb = 0;
    }

    pub async fn acquire(&self) -> Result<(), AdaptiveSemaphoreClosedError> {
        loop {
            let receiver = {
                let mut state = self
                    .state
                    .lock()
                    .expect("adaptive semaphore mutex poisoned");

                if state.closed {
                    return Err(AdaptiveSemaphoreClosedError);
                }

                if state.available > 0 {
                    state.available -= 1;
                    return Ok(());
                }

                let (tx, rx) = oneshot::channel();
                state.waiters.push_back(tx);
                rx
            };

            match receiver.await {
                Ok(result) => return result,
                Err(_) => {
                    if self.closed() {
                        return Err(AdaptiveSemaphoreClosedError);
                    }
                }
            }
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
            let mut state = self
                .state
                .lock()
                .expect("adaptive semaphore mutex poisoned");
            state.closed = true;
            state.waiters.drain(..).collect::<Vec<_>>()
        };

        for waiter in waiters {
            let _ = waiter.send(Err(AdaptiveSemaphoreClosedError));
        }
    }

    pub fn release(&self) {
        let mut state = self
            .state
            .lock()
            .expect("adaptive semaphore mutex poisoned");

        if state.permits_to_absorb > 0 {
            state.permits_to_absorb -= 1;
            return;
        }

        if wake_waiter(&mut state.waiters) {
            return;
        }

        if state.available < state.window {
            state.available += 1;
        }
    }

    pub fn on_success(&self) {
        let mut state = self
            .state
            .lock()
            .expect("adaptive semaphore mutex poisoned");
        if state.closed || state.has_header_signal || state.window >= self.max {
            return;
        }
        let target = state.window + 1;
        adjust_window(&mut state, self.min, self.max, target);
    }

    pub fn on_rate_limit(&self) {
        let mut state = self
            .state
            .lock()
            .expect("adaptive semaphore mutex poisoned");
        if state.closed {
            return;
        }
        let target = (state.window / 2).max(self.min);
        adjust_window(&mut state, self.min, self.max, target);
        state.has_header_signal = false;
    }

    pub fn on_header_feedback(&self, remaining: usize, limit: usize) {
        let mut state = self
            .state
            .lock()
            .expect("adaptive semaphore mutex poisoned");
        if state.closed || limit == 0 {
            return;
        }
        state.has_header_signal = true;

        let ratio = remaining as f64 / limit as f64;
        if ratio >= HEADER_BACKOFF_THRESHOLD {
            if state.window < self.max {
                let target = state.window + 1;
                adjust_window(&mut state, self.min, self.max, target);
            }
            return;
        }

        let scale = ratio / HEADER_BACKOFF_THRESHOLD;
        let target = (self.min as f64 + scale * (self.max - self.min) as f64) as usize;
        adjust_window(&mut state, self.min, self.max, target);
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

impl Drop for AdaptiveSemaphorePermit<'_> {
    fn drop(&mut self) {
        if !self.released {
            self.semaphore.release();
            self.released = true;
        }
    }
}

fn wake_waiter(
    waiters: &mut VecDeque<oneshot::Sender<Result<(), AdaptiveSemaphoreClosedError>>>,
) -> bool {
    while let Some(waiter) = waiters.pop_front() {
        if waiter.send(Ok(())).is_ok() {
            return true;
        }
    }
    false
}

fn adjust_window(state: &mut AdaptiveSemaphoreState, min: usize, max: usize, mut target: usize) {
    target = target.max(min).min(max);
    if target == state.window {
        return;
    }

    if target > state.window {
        let mut grow = target - state.window;
        let absorbed = grow.min(state.permits_to_absorb);
        state.permits_to_absorb -= absorbed;
        grow -= absorbed;
        for _ in 0..grow {
            if !wake_waiter(&mut state.waiters) {
                state.available += 1;
            }
        }
    } else {
        let shrink = state.window - target;
        let immediate = shrink.min(state.available);
        state.available -= immediate;
        state.permits_to_absorb += shrink - immediate;
    }

    state.window = target;
}

pub fn parse_openai_ratelimit_headers(
    headers: &HashMap<String, String>,
) -> Option<RateLimitHeaders> {
    let remaining = headers.get("x-ratelimit-remaining-requests")?;
    let limit = headers.get("x-ratelimit-limit-requests")?;

    let remaining = remaining.parse::<usize>().ok()?;
    let limit = limit.parse::<usize>().ok()?;

    (limit > 0).then_some(RateLimitHeaders { remaining, limit })
}
