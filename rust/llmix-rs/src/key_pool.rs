use crate::error::KeyPoolExhaustedError;
use crate::{LlmixError, LlmixResult};
use std::collections::HashSet;
use std::env;
use std::sync::Mutex;

#[derive(Debug)]
struct KeyPoolState {
    dead: HashSet<String>,
    idx: usize,
}

#[derive(Debug)]
pub struct KeyPool {
    keys: Vec<String>,
    state: Mutex<KeyPoolState>,
}

impl KeyPool {
    pub fn new(keys: Vec<String>) -> LlmixResult<Self> {
        let mut cleaned = Vec::new();
        let mut seen = HashSet::new();

        for key in keys {
            let trimmed = key.trim();
            if trimmed.is_empty() {
                continue;
            }
            if seen.insert(trimmed.to_owned()) {
                cleaned.push(trimmed.to_owned());
            }
        }

        if cleaned.is_empty() {
            return Err(LlmixError::InvalidKeyPoolConfig(
                "KeyPool requires at least one non-empty key".to_owned(),
            ));
        }

        Ok(Self {
            keys: cleaned,
            state: Mutex::new(KeyPoolState {
                dead: HashSet::new(),
                idx: 0,
            }),
        })
    }

    pub fn select(&self) -> Result<String, KeyPoolExhaustedError> {
        let mut state = self.state.lock().expect("key pool mutex poisoned");

        if state.dead.len() >= self.keys.len() {
            return Err(KeyPoolExhaustedError {
                total_keys: self.keys.len(),
            });
        }

        for offset in 0..self.keys.len() {
            let idx = (state.idx + offset) % self.keys.len();
            let key = &self.keys[idx];
            if !state.dead.contains(key) {
                state.idx = idx;
                return Ok(key.clone());
            }
        }

        Err(KeyPoolExhaustedError {
            total_keys: self.keys.len(),
        })
    }

    pub fn rotate(&self) {
        let mut state = self.state.lock().expect("key pool mutex poisoned");

        if state.dead.len() >= self.keys.len() {
            return;
        }

        for offset in 1..=self.keys.len() {
            let idx = (state.idx + offset) % self.keys.len();
            if !state.dead.contains(&self.keys[idx]) {
                state.idx = idx;
                return;
            }
        }
    }

    pub fn mark_dead(&self, key: &str) -> LlmixResult<()> {
        if !self.keys.iter().any(|candidate| candidate == key) {
            return Err(LlmixError::UnknownKeyPoolKey(
                "Key not in pool: cannot mark unknown key as dead".to_owned(),
            ));
        }

        let mut state = self.state.lock().expect("key pool mutex poisoned");
        state.dead.insert(key.to_owned());
        Ok(())
    }

    pub fn is_exhausted(&self) -> bool {
        let state = self.state.lock().expect("key pool mutex poisoned");
        state.dead.len() >= self.keys.len()
    }

    pub fn alive_count(&self) -> usize {
        let state = self.state.lock().expect("key pool mutex poisoned");
        self.keys.len() - state.dead.len()
    }

    pub fn total_count(&self) -> usize {
        self.keys.len()
    }
}

pub fn load_keys_from_env(provider: &str) -> LlmixResult<KeyPool> {
    let provider_upper = provider
        .chars()
        .map(|character| match character {
            'a'..='z' => character.to_ascii_uppercase(),
            'A'..='Z' | '0'..='9' => character,
            _ => '_',
        })
        .collect::<String>();
    let keys_var = format!("{provider_upper}_KEYS");
    let key_var = format!("{provider_upper}_API_KEY");

    if let Ok(keys_raw) = env::var(&keys_var) {
        if !keys_raw.trim().is_empty() {
            return KeyPool::new(keys_raw.split(',').map(ToOwned::to_owned).collect());
        }
    }

    if let Ok(single_key) = env::var(&key_var) {
        if !single_key.trim().is_empty() {
            return KeyPool::new(vec![single_key]);
        }
    }

    Err(LlmixError::InvalidKeyPoolConfig(format!(
        "No API keys found for {provider_upper}. Set {keys_var} (comma-separated) or {key_var}."
    )))
}
