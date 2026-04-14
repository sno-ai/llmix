use llmix_rs::{load_keys_from_env, KeyPool, KeyPoolExhaustedError, LlmixError};
use std::env;
use std::sync::{Mutex, OnceLock};

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

#[test]
fn select_is_stable_until_rotate_and_skips_dead_keys() {
    let pool = KeyPool::new(vec!["a".into(), "b".into(), "c".into()]).unwrap();

    assert_eq!(pool.select().unwrap(), "a");
    assert_eq!(pool.select().unwrap(), "a");

    pool.mark_dead("b").unwrap();
    pool.rotate();
    assert_eq!(pool.select().unwrap(), "c");

    pool.rotate();
    assert_eq!(pool.select().unwrap(), "a");
}

#[test]
fn constructor_trims_filters_and_deduplicates_keys() {
    let pool = KeyPool::new(vec![
        "  a  ".into(),
        "".into(),
        "a".into(),
        "  ".into(),
        "b".into(),
    ])
    .unwrap();

    assert_eq!(pool.total_count(), 2);
    assert_eq!(pool.alive_count(), 2);
    assert_eq!(pool.select().unwrap(), "a");
    pool.rotate();
    assert_eq!(pool.select().unwrap(), "b");
}

#[test]
fn constructor_rejects_empty_and_all_whitespace_inputs() {
    let empty = KeyPool::new(Vec::new()).unwrap_err();
    let whitespace = KeyPool::new(vec!["".into(), "   ".into()]).unwrap_err();

    assert!(matches!(empty, LlmixError::InvalidKeyPoolConfig(_)));
    assert!(matches!(whitespace, LlmixError::InvalidKeyPoolConfig(_)));
}

#[test]
fn single_key_rotation_is_a_noop_and_exhaustion_is_explicit() {
    let pool = KeyPool::new(vec!["only".into()]).unwrap();
    assert_eq!(pool.select().unwrap(), "only");

    pool.rotate();
    assert_eq!(pool.select().unwrap(), "only");

    pool.mark_dead("only").unwrap();
    assert!(pool.is_exhausted());

    let err = pool.select().unwrap_err();
    assert_eq!(err, KeyPoolExhaustedError { total_keys: 1 });
}

#[test]
fn marking_unknown_key_is_an_error() {
    let pool = KeyPool::new(vec!["a".into(), "b".into()]).unwrap();
    let err = pool.mark_dead("missing").unwrap_err();

    assert!(matches!(err, LlmixError::UnknownKeyPoolKey(_)));
}

#[test]
fn alive_count_tracks_dead_keys_until_exhaustion() {
    let pool = KeyPool::new(vec!["a".into(), "b".into(), "c".into()]).unwrap();
    assert_eq!(pool.alive_count(), 3);

    pool.mark_dead("b").unwrap();
    assert_eq!(pool.alive_count(), 2);
    assert!(!pool.is_exhausted());

    pool.mark_dead("a").unwrap();
    assert_eq!(pool.alive_count(), 1);
    assert_eq!(pool.select().unwrap(), "c");

    pool.mark_dead("c").unwrap();
    assert_eq!(pool.alive_count(), 0);
    assert!(pool.is_exhausted());
}

#[test]
fn load_keys_from_env_prefers_multi_key_var_then_falls_back_to_single_key() {
    let _guard = env_lock();
    let keys_var = "RUSTPORT_KEYS";
    let key_var = "RUSTPORT_API_KEY";
    let old_keys = env::var(keys_var).ok();
    let old_key = env::var(key_var).ok();

    env::set_var(keys_var, "k1, k2, k1");
    env::set_var(key_var, "single");

    let multi = load_keys_from_env("rustport").unwrap();
    assert_eq!(multi.total_count(), 2);
    assert_eq!(multi.select().unwrap(), "k1");
    multi.rotate();
    assert_eq!(multi.select().unwrap(), "k2");

    env::set_var(keys_var, "   ");
    let single = load_keys_from_env("rustport").unwrap();
    assert_eq!(single.total_count(), 1);
    assert_eq!(single.select().unwrap(), "single");

    restore_var(keys_var, old_keys);
    restore_var(key_var, old_key);
}

#[test]
fn load_keys_from_env_filters_empty_entries_from_multi_key_var() {
    let _guard = env_lock();
    let keys_var = "RUSTPORT_FILTER_KEYS";
    let key_var = "RUSTPORT_FILTER_API_KEY";
    let old_keys = env::var(keys_var).ok();
    let old_key = env::var(key_var).ok();

    env::set_var(keys_var, " k1, , k2,   , k1 ");
    env::remove_var(key_var);

    let pool = load_keys_from_env("rustport_filter").unwrap();
    assert_eq!(pool.total_count(), 2);
    assert_eq!(pool.alive_count(), 2);
    assert_eq!(pool.select().unwrap(), "k1");
    pool.rotate();
    assert_eq!(pool.select().unwrap(), "k2");

    restore_var(keys_var, old_keys);
    restore_var(key_var, old_key);
}

#[test]
fn load_keys_from_env_normalizes_provider_delimiters_for_env_var_names() {
    let _guard = env_lock();
    let keys_var = "SNO_GPU_KEYS";
    let key_var = "SNO_GPU_API_KEY";
    let old_keys = env::var(keys_var).ok();
    let old_key = env::var(key_var).ok();

    env::set_var(keys_var, "gpu-a, gpu-b");
    env::remove_var(key_var);

    let pool = load_keys_from_env("sno-gpu").unwrap();
    assert_eq!(pool.total_count(), 2);
    assert_eq!(pool.select().unwrap(), "gpu-a");
    pool.rotate();
    assert_eq!(pool.select().unwrap(), "gpu-b");

    restore_var(keys_var, old_keys);
    restore_var(key_var, old_key);
}

#[test]
fn load_keys_from_env_missing_vars_is_an_error() {
    let _guard = env_lock();
    let keys_var = "RUSTPORT_MISSING_KEYS";
    let key_var = "RUSTPORT_MISSING_API_KEY";
    let old_keys = env::var(keys_var).ok();
    let old_key = env::var(key_var).ok();

    env::remove_var(keys_var);
    env::remove_var(key_var);

    let err = load_keys_from_env("rustport_missing").unwrap_err();
    assert!(
        matches!(err, LlmixError::InvalidKeyPoolConfig(message) if message.contains("RUSTPORT_MISSING"))
    );

    restore_var(keys_var, old_keys);
    restore_var(key_var, old_key);
}
