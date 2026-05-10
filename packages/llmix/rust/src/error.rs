use std::collections::HashMap;
use thiserror::Error;

pub type LlmixResult<T> = Result<T, LlmixError>;

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("circuit breaker OPEN for ({provider}, {base_url})")]
pub struct CircuitOpenError {
    pub provider: String,
    pub base_url: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("kill switch active: {path}")]
pub struct KillSwitchActiveError {
    pub path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("all {total_keys} keys are dead (401). Replace keys or wait for provider resolution.")]
pub struct KeyPoolExhaustedError {
    pub total_keys: usize,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("config file not found: {path}")]
pub struct ConfigNotFoundError {
    pub path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("permission denied reading config file: {path}")]
pub struct ConfigAccessError {
    pub path: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{message}")]
pub struct InvalidConfigError {
    pub message: String,
}

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{message}")]
pub struct SecurityError {
    pub message: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Error)]
#[error("adaptive semaphore is closed")]
pub struct AdaptiveSemaphoreClosedError;

#[derive(Debug, Clone, PartialEq, Eq, Error)]
#[error("{message}")]
pub struct ProviderError {
    pub message: String,
    pub status_code: Option<u16>,
    pub headers: Option<HashMap<String, String>>,
}

#[derive(Debug, Error)]
pub enum LlmixError {
    #[error("{0}")]
    InvalidResponseCacheConfig(String),

    #[error("{0}")]
    InvalidKeyPoolConfig(String),

    #[error("{0}")]
    InvalidAdaptiveSemaphoreConfig(String),

    #[error("{0}")]
    InvalidRetryPolicyConfig(String),

    #[error("{0}")]
    InvalidFileLockConfig(String),

    #[error("{0}")]
    InvalidProviderKwargsConfig(String),

    #[error("redis error: {0}")]
    Redis(String),

    #[error("{0}")]
    UnknownKeyPoolKey(String),

    #[error("failed to serialize canonical json: {0}")]
    CanonicalJson(#[from] serde_json::Error),

    #[error(transparent)]
    Io(#[from] std::io::Error),

    #[error(transparent)]
    CircuitOpen(#[from] CircuitOpenError),

    #[error(transparent)]
    KillSwitchActive(#[from] KillSwitchActiveError),

    #[error(transparent)]
    KeyPoolExhausted(#[from] KeyPoolExhaustedError),

    #[error(transparent)]
    ConfigNotFound(#[from] ConfigNotFoundError),

    #[error(transparent)]
    ConfigAccess(#[from] ConfigAccessError),

    #[error(transparent)]
    InvalidConfig(#[from] InvalidConfigError),

    #[error(transparent)]
    Security(#[from] SecurityError),

    #[error(transparent)]
    AdaptiveSemaphoreClosed(#[from] AdaptiveSemaphoreClosedError),

    #[error(transparent)]
    Provider(#[from] ProviderError),
}
