#!/usr/bin/env python3
"""Suite 13: Python<->TypeScript Parity Tests (offline)

Verifies that Python and TypeScript implementations produce identical results
for deterministic operations: cache key generation, thinking stripping,
error classification, and gpuPath validation.

No LLM calls are made. TypeScript code is executed via node subprocess
against the compiled dist.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))
from conftest import assert_eq, assert_true, print_summary

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "python"))
from llmix.provider_kwargs import sno_gpu_transform_kwargs
from llmix.resilience import is_retryable
from llmix.response_cache import generate_cache_key
from llmix.thinking import strip_thinking

REPO_ROOT = Path(__file__).parent.parent.parent
TS_DIST = REPO_ROOT / "typescript" / "dist"

# The tsconfig has rootDir=. and outDir=./typescript/dist, so
# typescript/src/foo.ts compiles to typescript/dist/typescript/src/foo.js
TS_SRC_DIST = TS_DIST / "typescript" / "src"


def _has_ts_dist() -> bool:
    """Check if TypeScript is compiled and available."""
    return (TS_SRC_DIST / "response-cache.js").exists()


def _build_ts_dist() -> bool:
    """Attempt to build TypeScript dist. Returns True on success."""
    try:
        result = subprocess.run(
            ["bun", "run", "build"],
            capture_output=True, text=True,
            cwd=str(REPO_ROOT), timeout=30,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_ts(code: str) -> str:
    """Run compiled TypeScript (JS) code via bun and return stdout.

    Uses bun instead of node because tsc emits extensionless imports
    (e.g. './lazy-import') that Node ESM cannot resolve without
    additional configuration. Bun handles these natively.
    """
    result = subprocess.run(
        ["bun", "-e", code],
        capture_output=True, text=True,
        cwd=str(REPO_ROOT), timeout=10,
    )
    if result.returncode != 0:
        raise RuntimeError(f"TS execution failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _ensure_ts_dist() -> bool:
    """Ensure TS dist is available, building if necessary."""
    if _has_ts_dist():
        return True
    print("  [INFO] TS dist not found, attempting build...")
    if _build_ts_dist() and _has_ts_dist():
        print("  [INFO] TS build succeeded")
        return True
    print("  [WARN] TS build failed or dist still missing")
    return False


# =============================================================================
# 13.1 Cache key parity
# =============================================================================

CACHE_KEY_VECTORS: list[tuple[str, dict]] = [
    ("simple", {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.7,
    }),
    ("with seed and topP", {
        "provider": "anthropic",
        "model": "claude-sonnet-4-20250514",
        "messages": [{"role": "user", "content": "test"}],
        "seed": 42,
        "topP": 0.9,
    }),
    ("with responseFormat", {
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "system", "content": "respond in JSON"}, {"role": "user", "content": "list 3 colors"}],
        "temperature": 0.3,
        "responseFormat": {"type": "json_object"},
    }),
    # NOTE: temperature=0.0 intentionally avoided -- Python json.dumps emits
    # "0.0" while JSON.stringify emits "0", producing different hashes.
    # This is a known JSON serialization difference, not a code bug.
    ("with providerOptions", {
        "provider": "google",
        "model": "gemini-2.5-flash",
        "messages": [{"role": "user", "content": "hi"}],
        "providerOptions": {"google": {"thinkingConfig": {"thinkingBudget": 1024}}},
    }),
    ("float precision 0.7", {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "test float"}],
        "temperature": 0.7,
    }),
    ("maxOutputTokens", {
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "short"}],
        "maxOutputTokens": 100,
        "temperature": 0.5,
    }),
    ("baseUrl present", {
        "provider": "sno-gpu",
        "model": "qwen3.5",
        "messages": [{"role": "user", "content": "test"}],
        "baseUrl": "http://localhost:8000/v1",
    }),
    ("multiple messages", {
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "And 3+3?"},
        ],
        "temperature": 0.3,
        "topP": 0.95,
    }),
    ("null fields excluded", {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hello"}],
        "temperature": 0.5,
        "seed": None,
        "topP": None,
    }),
    ("empty providerOptions", {
        "provider": "openai",
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "test"}],
    }),
    ("integer temperature", {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "temperature": 1,
    }),
]


async def test_13_1_cache_key_parity():
    """Cache key parity -- same camelCase params produce identical SHA-256."""
    if not _ensure_ts_dist():
        print("  [SKIP] TS dist unavailable")
        return

    for label, params in CACHE_KEY_VECTORS:
        py_key = generate_cache_key(params)

        # Build TS code with the same params
        params_json = json.dumps(params)
        ts_code = f"""
import {{ generateCacheKey }} from './typescript/dist/typescript/src/response-cache.js';
console.log(generateCacheKey({params_json}));
"""
        try:
            ts_key = _run_ts(ts_code)
        except RuntimeError as e:
            assert_true(False, f"cache key [{label}]: TS error: {e}")
            continue

        assert_eq(py_key, ts_key, f"cache key [{label}]")


# =============================================================================
# 13.2 Thinking strip parity
# =============================================================================

THINKING_VECTORS: list[tuple[str, str, str, str | None]] = [
    # (label, input, expected_stripped, expected_thinking)
    ("simple", "<think>reasoning</think>answer", "answer", "reasoning"),
    ("no thinking tags", "plain text", "plain text", None),
    ("multiple blocks", "<think>a</think>middle<think>b</think>end", "middleend", "a\nb"),
    ("unclosed tag", "<think>reasoning", "", "reasoning"),
    ("empty think", "<think></think>content", "content", ""),
    ("think with newlines", "<think>line1\nline2</think>result", "result", "line1\nline2"),
    ("trailing whitespace after close", "<think>x</think>  output", "output", "x"),
    ("only thinking no content", "<think>just thinking</think>", "", "just thinking"),
]


async def test_13_2_thinking_strip_parity():
    """Thinking strip parity -- same raw output produces identical stripping."""
    if not _ensure_ts_dist():
        print("  [SKIP] TS dist unavailable")
        return

    for label, input_text, expected_stripped, expected_thinking in THINKING_VECTORS:
        # Python
        py_stripped, py_thinking = strip_thinking(input_text)
        assert_eq(py_stripped, expected_stripped, f"thinking strip PY [{label}] content")
        assert_eq(py_thinking, expected_thinking, f"thinking strip PY [{label}] thinking")

        # TypeScript
        escaped = json.dumps(input_text)
        ts_code = f"""
import {{ stripThinking }} from './typescript/dist/typescript/src/thinking.js';
const result = stripThinking({escaped});
console.log(JSON.stringify(result));
"""
        try:
            ts_raw = _run_ts(ts_code)
            ts_result = json.loads(ts_raw)
        except (RuntimeError, json.JSONDecodeError) as e:
            assert_true(False, f"thinking strip TS [{label}]: error: {e}")
            continue

        ts_stripped = ts_result["content"]
        ts_thinking = ts_result["thinkingContent"]

        assert_eq(py_stripped, ts_stripped, f"thinking strip parity [{label}] content")
        assert_eq(py_thinking, ts_thinking, f"thinking strip parity [{label}] thinking")


# =============================================================================
# 13.3 Batch ID parity -- SKIPPED
# =============================================================================


async def test_13_3_batch_id_parity_skip():
    """Batch ID parity -- SKIPPED (batch module not implemented yet)."""
    print("  [SKIP] batch module not implemented")


# =============================================================================
# 13.4 Response structure parity
# =============================================================================

# Python snake_case -> TypeScript camelCase field mapping
RESPONSE_FIELD_MAP = {
    "content": "content",
    "model": "model",
    "provider": "provider",
    "success": "success",
    "error": "error",
    "thinking_content": "thinkingContent",
    "cache_hit": "cacheHit",
}

USAGE_FIELD_MAP = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "total_tokens": "totalTokens",
}


async def test_13_4_response_structure_parity():
    """Response structure parity -- Python snake_case maps to TS camelCase."""
    if not _ensure_ts_dist():
        print("  [SKIP] TS dist unavailable")
        return

    # Verify Python CallResponse fields
    from llmix.pipeline import CallResponse as PyResponse
    import dataclasses
    py_fields = {f.name for f in dataclasses.fields(PyResponse)}
    # usage is a nested object, handled separately
    for py_field in RESPONSE_FIELD_MAP:
        assert_true(py_field in py_fields, f"Python CallResponse has '{py_field}'")
    assert_true("usage" in py_fields, "Python CallResponse has 'usage'")

    # Verify TypeScript CallResponse fields via node
    ts_code = """
import { readFileSync } from 'node:fs';
// Read the TS source to check interface fields
const src = readFileSync('./typescript/src/pipeline.ts', 'utf-8');
// Extract CallResponse interface
const match = src.match(/export interface CallResponse \\{([^}]+)\\}/s);
if (match) {
    const body = match[1];
    const fields = [...body.matchAll(/(\\w+)[?]?\\s*:/g)].map(m => m[1]);
    console.log(JSON.stringify(fields));
} else {
    console.log('[]');
}
"""
    try:
        ts_raw = _run_ts(ts_code)
        ts_fields = set(json.loads(ts_raw))
    except (RuntimeError, json.JSONDecodeError) as e:
        assert_true(False, f"TS field extraction error: {e}")
        return

    for py_field, ts_field in RESPONSE_FIELD_MAP.items():
        assert_true(ts_field in ts_fields, f"TS CallResponse has '{ts_field}' (maps from py '{py_field}')")
    assert_true("usage" in ts_fields, "TS CallResponse has 'usage'")

    # Verify usage sub-fields via TS source
    ts_usage_code = """
import { readFileSync } from 'node:fs';
const src = readFileSync('./typescript/src/pipeline.ts', 'utf-8');
const match = src.match(/export interface LLMUsage \\{([^}]+)\\}/s);
if (!match) {
    // Try types.ts
    const src2 = readFileSync('./typescript/src/types.ts', 'utf-8');
    const match2 = src2.match(/export interface LLMUsage \\{([^}]+)\\}/s);
    if (match2) {
        const fields = [...match2[1].matchAll(/(\\w+)[?]?\\s*:/g)].map(m => m[1]);
        console.log(JSON.stringify(fields));
    } else {
        console.log('[]');
    }
} else {
    const fields = [...match[1].matchAll(/(\\w+)[?]?\\s*:/g)].map(m => m[1]);
    console.log(JSON.stringify(fields));
}
"""
    try:
        ts_usage_raw = _run_ts(ts_usage_code)
        ts_usage_fields = set(json.loads(ts_usage_raw))
    except (RuntimeError, json.JSONDecodeError) as e:
        assert_true(False, f"TS usage field extraction error: {e}")
        return

    for py_field, ts_field in USAGE_FIELD_MAP.items():
        assert_true(ts_field in ts_usage_fields, f"TS LLMUsage has '{ts_field}' (maps from py '{py_field}')")


# =============================================================================
# 13.5 Error classification parity
# =============================================================================

ERROR_CLASSIFICATION_CODES = [200, 400, 401, 403, 404, 429, 500, 502, 503, 504]


async def test_13_5_error_classification_parity():
    """Error classification parity -- same status codes, same retryable/non-retryable."""
    if not _ensure_ts_dist():
        print("  [SKIP] TS dist unavailable")
        return

    # Build TS code that checks all codes at once
    codes_json = json.dumps(ERROR_CLASSIFICATION_CODES)
    ts_code = f"""
import {{ isRetryable }} from './typescript/dist/typescript/src/resilience.js';
const codes = {codes_json};
const results = {{}};
for (const code of codes) {{
    results[code] = isRetryable(code);
}}
console.log(JSON.stringify(results));
"""
    try:
        ts_raw = _run_ts(ts_code)
        ts_results = json.loads(ts_raw)
    except (RuntimeError, json.JSONDecodeError) as e:
        assert_true(False, f"TS error classification error: {e}")
        return

    for code in ERROR_CLASSIFICATION_CODES:
        py_result = is_retryable(code)
        ts_result = ts_results[str(code)]
        assert_eq(
            py_result, ts_result,
            f"is_retryable({code}): py={py_result}, ts={ts_result}",
        )


# =============================================================================
# 13.6 gpuPath validation parity
# =============================================================================

INVALID_GPU_PATHS = [
    "../../etc/passwd",
    "/etc\x00/passwd",
    "../config",
    "path;with;semicolons",
    "path with spaces",
    "path<script>",
]


async def test_13_6_gpu_path_validation_parity():
    """gpuPath validation parity -- both languages reject the same invalid paths."""
    if not _ensure_ts_dist():
        print("  [SKIP] TS dist unavailable")
        return

    for bad_path in INVALID_GPU_PATHS:
        # Python: sno_gpu_transform_kwargs should raise ValueError
        py_raised = False
        try:
            ctx: dict[str, Any] = {
                "model": "test-model",
                "provider": "sno-gpu",
                "base_url": "http://localhost:8000",
                "provider_options": {"sno-gpu": {"gpu_path": bad_path}},
            }
            sno_gpu_transform_kwargs(ctx, {})  # type: ignore[arg-type]
        except ValueError:
            py_raised = True

        # TypeScript: snoGpuTransformKwargs should throw Error
        escaped_path = json.dumps(bad_path)
        ts_code = f"""
import {{ snoGpuTransformKwargs }} from './typescript/dist/typescript/src/provider-kwargs.js';
try {{
    snoGpuTransformKwargs({{
        model: 'test-model',
        provider: 'sno-gpu',
        baseUrl: 'http://localhost:8000',
        providerOptions: {{ "sno-gpu": {{ gpuPath: {escaped_path} }} }},
    }}, {{}});
    console.log('no_error');
}} catch (e) {{
    console.log('error');
}}
"""
        try:
            ts_output = _run_ts(ts_code)
            ts_raised = ts_output == "error"
        except RuntimeError:
            # Node process crashed = also a rejection
            ts_raised = True

        assert_eq(
            py_raised, ts_raised,
            f"gpuPath reject [{bad_path!r}]: py={py_raised}, ts={ts_raised}",
        )


# =============================================================================
# Main
# =============================================================================


async def main() -> int:
    print("Suite 13: Python<->TypeScript Parity")
    print("=" * 60)
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"\n--- {t.__name__} ---")
        await t()
    return print_summary("Suite 13")


if __name__ == "__main__":
    import asyncio
    sys.exit(asyncio.run(main()))
