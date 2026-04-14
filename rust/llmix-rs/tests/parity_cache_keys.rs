use llmix_rs::{generate_cache_key, CacheKeyParams, CACHE_KEY_PREFIX};
use serde::Deserialize;
use std::fs;
use std::path::PathBuf;

#[derive(Debug, Deserialize)]
struct VectorFile {
    vectors: Vec<TestVector>,
}

#[derive(Debug, Deserialize)]
struct TestVector {
    name: String,
    input: CacheKeyParams,
    #[serde(rename = "expectedKey")]
    expected_key: String,
}

fn fixture_path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/cache-key-vectors.json")
}

fn final_contract_key(legacy_expected_key: &str) -> String {
    let hash = legacy_expected_key
        .rsplit(':')
        .next()
        .expect("fixture key should include a hash suffix");
    format!("{CACHE_KEY_PREFIX}{hash}")
}

#[test]
fn shared_vectors_match_hashes_under_final_llmix_prefix() {
    let raw = fs::read_to_string(fixture_path()).expect("shared fixture should load");
    let vectors: VectorFile = serde_json::from_str(&raw).expect("fixture should parse");

    for vector in vectors.vectors {
        let actual = generate_cache_key(&vector.input).expect("cache key should serialize");
        let expected = final_contract_key(&vector.expected_key);
        assert_eq!(actual, expected, "vector {}", vector.name);
        assert!(actual.starts_with(CACHE_KEY_PREFIX));
    }
}
