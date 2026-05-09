#!/usr/bin/env python3
"""Tests for the LLMix batch processing module (Python).

Covers batch ID encode/decode roundtrip, colon-safe decode, and
durable metadata file create/read/cleanup.
Uses shared fixtures from fixtures/llmix/batch-id-roundtrip.json.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

# Ensure the python package is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from llmix.batch import (
    BatchMetadata,
    BatchProcessor,
    BatchResult,
    _key_fingerprint,
    decode_batch_id,
    delete_metadata,
    encode_batch_id,
    read_metadata,
    write_metadata,
)
import llmix.batch as batch_module

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "fixtures" / "llmix"
FIXTURES_PATH = FIXTURE_DIR / "batch-id-roundtrip.json"


def load_fixtures() -> dict:
    with open(FIXTURES_PATH) as f:
        return json.load(f)


passed = 0
failed = 0


def assert_true(condition: bool, msg: str) -> None:
    global passed, failed
    if condition:
        passed += 1
        print(f"[PASS] {msg}")
    else:
        failed += 1
        print(f"[FAIL] {msg}")


def assert_throws(fn, msg: str) -> None:
    global passed, failed
    try:
        fn()
        failed += 1
        print(f"[FAIL] {msg} — expected error but none raised")
    except Exception:
        passed += 1
        print(f"[PASS] {msg}")


def key_fingerprint(api_key: str) -> str:
    return hashlib.sha256(api_key.encode()).hexdigest()[-8:]


def metadata_filename(batch_id: str) -> str:
    encoded = base64.urlsafe_b64encode(batch_id.encode("utf-8")).decode("ascii")
    return f"{encoded.rstrip('=')}.json"


fixtures = load_fixtures()


# ---------------------------------------------------------------------------
# Batch ID encode/decode roundtrip (Tasks 110, 111, 115)
# ---------------------------------------------------------------------------

print("\n=== Batch ID encode/decode roundtrip ===")

for scenario in fixtures["roundtrips"]:
    batch_id = encode_batch_id(
        scenario["provider"],
        scenario["apiKey"],
        scenario["nPrompts"],
        scenario["rawBatchId"],
    )

    # Verify format
    expected_fp = key_fingerprint(scenario["apiKey"])
    expected_id = f"{scenario['provider']}:{expected_fp}:{scenario['nPrompts']}:{scenario['rawBatchId']}"
    assert_true(batch_id == expected_id, f"encode format: {scenario['note']}")

    # Decode and verify all fields
    decoded = decode_batch_id(batch_id)
    assert_true(decoded.provider == scenario["provider"], f"roundtrip provider: {scenario['note']}")
    assert_true(decoded.key_fingerprint == expected_fp, f"roundtrip fingerprint: {scenario['note']}")
    assert_true(decoded.n_prompts == scenario["nPrompts"], f"roundtrip nPrompts: {scenario['note']}")
    assert_true(decoded.raw_batch_id == scenario["rawBatchId"], f"roundtrip rawBatchId: {scenario['note']}")


# ---------------------------------------------------------------------------
# Colon-safe decode
# ---------------------------------------------------------------------------

print("\n=== Colon-safe decode ===")

colon_scenario = next(
    (s for s in fixtures["roundtrips"] if ":" in s["rawBatchId"]),
    None,
)
if colon_scenario:
    batch_id = encode_batch_id(
        colon_scenario["provider"],
        colon_scenario["apiKey"],
        colon_scenario["nPrompts"],
        colon_scenario["rawBatchId"],
    )
    decoded = decode_batch_id(batch_id)
    assert_true(
        decoded.raw_batch_id == colon_scenario["rawBatchId"],
        f'colon-safe: rawBatchId preserved with colons ("{colon_scenario["rawBatchId"]}")',
    )
    colon_count = decoded.raw_batch_id.count(":")
    expected_count = colon_scenario["rawBatchId"].count(":")
    assert_true(
        colon_count == expected_count,
        f"colon-safe: colon count matches ({colon_count})",
    )


# ---------------------------------------------------------------------------
# Invalid batch IDs
# ---------------------------------------------------------------------------

print("\n=== Invalid batch IDs ===")

for scenario in fixtures["invalidBatchIds"]:
    assert_throws(
        lambda bid=scenario["batchId"]: decode_batch_id(bid),
        f"rejects invalid: {scenario['reason']}",
    )


# ---------------------------------------------------------------------------
# Durable metadata (Task 112, 115)
# ---------------------------------------------------------------------------

print("\n=== Durable metadata ===")

test_dir = Path(tempfile.mkdtemp(prefix="llmix-batch-test-"))

try:
    for scenario in fixtures["metadataScenarios"]:
        # Write
        write_metadata(
            scenario["batchId"],
            scenario["apiKey"],
            scenario["provider"],
            scenario["nPrompts"],
            test_dir,
        )

        # Check file exists
        expected_path = test_dir / "batches" / metadata_filename(scenario["batchId"])
        assert_true(expected_path.exists(), f"metadata file created: {scenario['note']}")

        # Read
        metadata = read_metadata(scenario["batchId"], test_dir)
        expected_fingerprint = _key_fingerprint(scenario["apiKey"])
        assert_true(metadata.key_fingerprint == expected_fingerprint, f"metadata key_fingerprint preserved: {scenario['note']}")
        assert_true(metadata.provider == scenario["provider"], f"metadata provider preserved: {scenario['note']}")
        assert_true(metadata.n_prompts == scenario["nPrompts"], f"metadata nPrompts preserved: {scenario['note']}")
        assert_true(isinstance(metadata.submitted_at, str), f"metadata submittedAt is string: {scenario['note']}")

        # Delete
        delete_metadata(scenario["batchId"], test_dir)
        assert_true(not expected_path.exists(), f"metadata file deleted: {scenario['note']}")

        # Delete again (idempotent)
        delete_metadata(scenario["batchId"], test_dir)
        assert_true(True, f"metadata double-delete is safe: {scenario['note']}")

    gemini_batch_id = encode_batch_id("gemini", "gemini-key", 1, "operations/abc123")
    write_metadata(gemini_batch_id, "gemini-key", "gemini", 1, test_dir)
    gemini_path = test_dir / "batches" / metadata_filename(gemini_batch_id)
    assert_true(gemini_path.exists(), "metadata filename escapes Gemini path separators")
    assert_true(
        gemini_path.parent == test_dir / "batches",
        "Gemini metadata stays in the batches directory",
    )

finally:
    shutil.rmtree(test_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Results metadata cleanup
# ---------------------------------------------------------------------------

print("\n=== Results metadata cleanup ===")

processor_dir = Path(tempfile.mkdtemp(prefix="llmix-batch-results-"))
override_dir: Path | None = None

try:
    pending_batch_id = encode_batch_id("openai", "pending-key", 2, "raw-pending")
    write_metadata(pending_batch_id, "pending-key", "openai", 2, processor_dir)

    processor = BatchProcessor(state_dir=processor_dir)
    original_results = batch_module._openai_results
    try:
        batch_module._openai_results = lambda api_key, raw_batch_id, n_prompts: []
        pending_results = processor.results(pending_batch_id, "pending-key")
        assert_true(pending_results == [], "pending batch returns empty result list")
        assert_true(
            batch_module._metadata_path(pending_batch_id, processor_dir).exists(),
            "pending batch keeps metadata on disk",
        )

        complete_batch_id = encode_batch_id("openai", "complete-key", 2, "raw-complete")
        write_metadata(complete_batch_id, "complete-key", "openai", 2, processor_dir)
        batch_module._openai_results = lambda api_key, raw_batch_id, n_prompts: [
            BatchResult(index=0, success=True, response="ok"),
            BatchResult(index=1, success=True, response="done"),
        ]
        complete_results = processor.results(complete_batch_id, "complete-key")
        assert_true(len(complete_results) == 2, "completed batch returns terminal results")
        assert_true(
            not batch_module._metadata_path(complete_batch_id, processor_dir).exists(),
            "completed batch deletes metadata after terminal results",
        )

        override_dir = Path(tempfile.mkdtemp(prefix="llmix-batch-results-override-"))
        override_batch_id = encode_batch_id("openai", "override-key", 1, "raw-override")
        write_metadata(override_batch_id, "override-key", "openai", 1, override_dir)
        override_processor = BatchProcessor()
        batch_module._openai_results = lambda api_key, raw_batch_id, n_prompts: [
            BatchResult(index=0, success=True, response="override"),
        ]
        override_results = override_processor.results(
            override_batch_id,
            "override-key",
            state_dir=override_dir,
        )
        assert_true(len(override_results) == 1, "results accepts per-call state_dir override")
        assert_true(
            not batch_module._metadata_path(override_batch_id, override_dir).exists(),
            "per-call state_dir is used for metadata cleanup",
        )
    finally:
        batch_module._openai_results = original_results
        if override_dir is not None:
            shutil.rmtree(override_dir, ignore_errors=True)
finally:
    shutil.rmtree(processor_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Batch ID metadata validation
# ---------------------------------------------------------------------------

print("\n=== Batch ID metadata validation ===")

integrity_dir = Path(tempfile.mkdtemp(prefix="llmix-batch-integrity-"))

try:
    valid_batch_id = encode_batch_id("openai", "integrity-key", 2, "raw-valid")
    write_metadata(valid_batch_id, "integrity-key", "openai", 2, integrity_dir)

    tampered_status_batch_id = encode_batch_id("openai", "integrity-key", 2, "raw-other")
    assert_throws(
        lambda: BatchProcessor(state_dir=integrity_dir).status(tampered_status_batch_id, "integrity-key"),
        "status rejects tampered batch IDs without matching metadata",
    )

    tampered_results_batch_id = encode_batch_id("openai", "integrity-key", 3, "raw-valid")
    assert_throws(
        lambda: BatchProcessor(state_dir=integrity_dir).results(tampered_results_batch_id, "integrity-key"),
        "results rejects tampered batch IDs without matching metadata",
    )
finally:
    shutil.rmtree(integrity_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

print(f"\n=== Summary: {passed} passed, {failed} failed ===")
if failed > 0:
    sys.exit(1)
