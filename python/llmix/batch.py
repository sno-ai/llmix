"""
LLMix Batch Processing Module

Batch ID encoding/decoding, durable metadata, and provider-specific
batch submit/status/results dispatch.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from llmix.resilience import _resolve_state_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

BATCHES_SUBDIR = "batches"

BatchProvider = Literal["openai", "anthropic", "gemini"]
BatchState = Literal["pending", "in_progress", "completed", "failed", "expired"]

_VALID_PROVIDERS: set[str] = {"openai", "anthropic", "gemini"}


# ---------------------------------------------------------------------------
# Batch ID Encoding / Decoding (Tasks 110, 111)
# ---------------------------------------------------------------------------

def _key_fingerprint(api_key: str) -> str:
    """Compute the key fingerprint: last 8 hex chars of SHA-256(api_key)."""
    h = hashlib.sha256(api_key.encode()).hexdigest()
    return h[-8:]


def encode_batch_id(
    provider: BatchProvider,
    api_key: str,
    n_prompts: int,
    raw_batch_id: str,
) -> str:
    """Encode a batch ID from its components.

    Format: ``{provider}:{key_fingerprint}:{n_prompts}:{raw_batch_id}``
    """
    fp = _key_fingerprint(api_key)
    return f"{provider}:{fp}:{n_prompts}:{raw_batch_id}"


class DecodedBatchId:
    """Decoded batch ID components."""

    __slots__ = ("provider", "key_fingerprint", "n_prompts", "raw_batch_id")

    def __init__(
        self,
        provider: BatchProvider,
        key_fingerprint: str,
        n_prompts: int,
        raw_batch_id: str,
    ) -> None:
        self.provider = provider
        self.key_fingerprint = key_fingerprint
        self.n_prompts = n_prompts
        self.raw_batch_id = raw_batch_id


def decode_batch_id(batch_id: str) -> DecodedBatchId:
    """Decode a batch ID string into its components.

    Splits on the first 3 colons only — raw_batch_id may contain colons.
    """
    first = batch_id.find(":")
    if first == -1:
        raise ValueError(f"Invalid batch ID: {batch_id}")

    second = batch_id.find(":", first + 1)
    if second == -1:
        raise ValueError(f"Invalid batch ID: {batch_id}")

    third = batch_id.find(":", second + 1)
    if third == -1:
        raise ValueError(f"Invalid batch ID: {batch_id}")

    provider = batch_id[:first]
    fp = batch_id[first + 1 : second]
    n_prompts_str = batch_id[second + 1 : third]
    raw_batch_id = batch_id[third + 1 :]

    if provider not in _VALID_PROVIDERS:
        raise ValueError(f"Unknown batch provider: {provider}")
    if len(fp) != 8:
        raise ValueError(f"Invalid key fingerprint length: {fp}")
    try:
        n_prompts = int(n_prompts_str)
    except ValueError:
        raise ValueError(f"Invalid nPrompts: {n_prompts_str}") from None
    if n_prompts < 1:
        raise ValueError(f"Invalid nPrompts: {n_prompts_str}")
    if not raw_batch_id:
        raise ValueError(f"Empty raw_batch_id in: {batch_id}")

    return DecodedBatchId(
        provider=provider,  # type: ignore[arg-type]
        key_fingerprint=fp,
        n_prompts=n_prompts,
        raw_batch_id=raw_batch_id,
    )


# ---------------------------------------------------------------------------
# Schemas (Task 113)
# ---------------------------------------------------------------------------

class BatchStatus:
    """Status of a batch job."""

    __slots__ = ("batch_id", "state", "total_requests", "completed_requests", "failed_requests")

    def __init__(
        self,
        batch_id: str,
        state: BatchState,
        total_requests: int = 0,
        completed_requests: int = 0,
        failed_requests: int = 0,
    ) -> None:
        self.batch_id = batch_id
        self.state = state
        self.total_requests = total_requests
        self.completed_requests = completed_requests
        self.failed_requests = failed_requests


class BatchResult:
    """Result of a single request in a batch."""

    __slots__ = ("index", "success", "response", "error")

    def __init__(
        self,
        index: int,
        success: bool,
        response: str | None = None,
        error: str | None = None,
    ) -> None:
        self.index = index
        self.success = success
        self.response = response
        self.error = error


# ---------------------------------------------------------------------------
# Durable Metadata (Task 112)
# ---------------------------------------------------------------------------

class BatchMetadata:
    """Metadata written to disk on batch submit."""

    __slots__ = ("key_fingerprint", "provider", "n_prompts", "submitted_at")

    def __init__(
        self,
        key_fingerprint: str,
        provider: BatchProvider,
        n_prompts: int,
        submitted_at: str,
    ) -> None:
        self.key_fingerprint = key_fingerprint
        self.provider = provider
        self.n_prompts = n_prompts
        self.submitted_at = submitted_at


def _batches_dir(state_dir: Path | None = None) -> Path:
    base = state_dir or _resolve_state_dir()
    return base / BATCHES_SUBDIR


def _metadata_filename(batch_id: str) -> str:
    encoded = base64.urlsafe_b64encode(batch_id.encode("utf-8")).decode("ascii")
    return f"{encoded.rstrip('=')}.json"


def _metadata_path(batch_id: str, state_dir: Path | None = None) -> Path:
    return _batches_dir(state_dir) / _metadata_filename(batch_id)


def write_metadata(
    batch_id: str,
    api_key: str,
    provider: BatchProvider,
    n_prompts: int,
    state_dir: Path | None = None,
) -> None:
    """Write batch metadata to disk."""
    d = _batches_dir(state_dir)
    d.mkdir(parents=True, exist_ok=True)

    metadata = {
        "keyFingerprint": _key_fingerprint(api_key),
        "provider": provider,
        "nPrompts": n_prompts,
        "submittedAt": datetime.now(timezone.utc).isoformat(),
    }
    _metadata_path(batch_id, state_dir).write_text(
        json.dumps(metadata), encoding="utf-8"
    )


def read_metadata(batch_id: str, state_dir: Path | None = None) -> BatchMetadata:
    """Read batch metadata from disk."""
    raw = json.loads(
        _metadata_path(batch_id, state_dir).read_text(encoding="utf-8")
    )
    return BatchMetadata(
        key_fingerprint=raw["keyFingerprint"],
        provider=raw["provider"],
        n_prompts=raw["nPrompts"],
        submitted_at=raw["submittedAt"],
    )


def delete_metadata(batch_id: str, state_dir: Path | None = None) -> None:
    """Delete batch metadata from disk."""
    path = _metadata_path(batch_id, state_dir)
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _validate_batch_request(
    batch_id: str,
    api_key: str,
    state_dir: Path | None = None,
) -> DecodedBatchId:
    """Validate a batch ID against durable submit-time metadata."""
    decoded = decode_batch_id(batch_id)
    metadata = read_metadata(batch_id, state_dir)
    if _key_fingerprint(api_key) != metadata.key_fingerprint:
        raise ValueError("API key fingerprint does not match batch ID")
    if decoded.provider != metadata.provider or decoded.n_prompts != metadata.n_prompts:
        raise ValueError("Batch ID does not match stored metadata")
    return decoded


# ---------------------------------------------------------------------------
# Provider-specific batch operations (Tasks 107, 108, 109)
# ---------------------------------------------------------------------------

try:
    import httpx
    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False


def _require_httpx() -> Any:
    if not _HAS_HTTPX:
        raise ImportError(
            "httpx is required for batch API calls. Install with: pip install httpx"
        )
    import httpx
    return httpx


# --- OpenAI (Task 107) ---

def _openai_submit(
    api_key: str,
    model: str,
    prompts: list[str],
    system_prompt: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    httpx = _require_httpx()

    lines: list[str] = []
    for i, prompt in enumerate(prompts):
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        body: dict[str, Any] = {"model": model, "messages": messages}
        if params:
            body.update(params)
        lines.append(json.dumps({
            "custom_id": f"req-{i}",
            "method": "POST",
            "url": "/v1/chat/completions",
            "body": body,
        }))

    jsonl_bytes = ("\n".join(lines) + "\n").encode()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        upload_res = client.post(
            "https://api.openai.com/v1/files",
            headers={"Authorization": f"Bearer {api_key}"},
            files={"file": ("batch.jsonl", jsonl_bytes, "application/jsonl")},
            data={"purpose": "batch"},
        )
        upload_res.raise_for_status()
        file_id = upload_res.json()["id"]

        batch_res = client.post(
            "https://api.openai.com/v1/batches",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "input_file_id": file_id,
                "endpoint": "/v1/chat/completions",
                "completion_window": "24h",
            },
        )
        batch_res.raise_for_status()
        return batch_res.json()["id"]


def _openai_status(api_key: str, raw_batch_id: str) -> BatchStatus:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://api.openai.com/v1/batches/{raw_batch_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        res.raise_for_status()
        batch = res.json()

    state_map: dict[str, BatchState] = {
        "validating": "pending",
        "in_progress": "in_progress",
        "finalizing": "in_progress",
        "completed": "completed",
        "failed": "failed",
        "expired": "expired",
        "cancelled": "failed",
        "cancelling": "in_progress",
    }

    counts = batch.get("request_counts", {})
    return BatchStatus(
        batch_id=batch["id"],
        state=state_map.get(batch["status"], "pending"),
        total_requests=counts.get("total", 0),
        completed_requests=counts.get("completed", 0),
        failed_requests=counts.get("failed", 0),
    )


def _openai_results(
    api_key: str,
    raw_batch_id: str,
    n_prompts: int,
) -> list[BatchResult]:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://api.openai.com/v1/batches/{raw_batch_id}",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        res.raise_for_status()
        batch = res.json()

        if batch["status"] != "completed":
            if batch["status"] in ("failed", "expired", "cancelled"):
                raise RuntimeError(f"OpenAI batch {raw_batch_id} {batch['status']}")
            return []

        output_file_id = batch.get("output_file_id")
        if not output_file_id:
            raise RuntimeError(f"OpenAI batch {raw_batch_id} completed but no output_file_id")

        content_res = client.get(
            f"https://api.openai.com/v1/files/{output_file_id}/content",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        content_res.raise_for_status()
        text = content_res.text

    result_map: dict[int, BatchResult] = {}
    for line in text.strip().split("\n"):
        if not line:
            continue
        entry = json.loads(line)
        idx = int(entry["custom_id"].removeprefix("req-"))
        if entry.get("error"):
            result_map[idx] = BatchResult(index=idx, success=False, error=entry["error"]["message"])
        else:
            content = "".join(
                c["message"]["content"] or ""
                for c in entry["response"]["body"]["choices"]
            )
            result_map[idx] = BatchResult(index=idx, success=True, response=content)

    return [
        result_map.get(i, BatchResult(index=i, success=False, error="Missing result"))
        for i in range(n_prompts)
    ]


# --- Anthropic (Task 108) ---

def _anthropic_submit(
    api_key: str,
    model: str,
    prompts: list[str],
    system_prompt: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    httpx = _require_httpx()

    max_tokens = 4096
    extra_params: dict[str, Any] = {}
    if params:
        max_tokens = params.get("max_tokens", max_tokens)
        extra_params = {k: v for k, v in params.items() if k != "max_tokens"}

    requests = []
    for i, prompt in enumerate(prompts):
        req_params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system_prompt:
            req_params["system"] = system_prompt
        req_params.update(extra_params)
        requests.append({"custom_id": f"req-{i}", "params": req_params})

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.post(
            "https://api.anthropic.com/v1/messages/batches",
            headers={
                "x-api-key": api_key,
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={"requests": requests},
        )
        res.raise_for_status()
        return res.json()["id"]


def _anthropic_status(api_key: str, raw_batch_id: str) -> BatchStatus:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://api.anthropic.com/v1/messages/batches/{raw_batch_id}",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        res.raise_for_status()
        batch = res.json()

    counts = batch.get("request_counts", {})

    state: BatchState
    if batch["processing_status"] == "ended":
        if counts.get("succeeded", 0) == 0 and counts.get("errored", 0) > 0:
            state = "failed"
        else:
            state = "completed"
    elif batch["processing_status"] == "canceling":
        state = "in_progress"
    else:
        state = "in_progress"
    total = sum(
        counts.get(k, 0)
        for k in ("processing", "succeeded", "errored", "canceled", "expired")
    )

    return BatchStatus(
        batch_id=raw_batch_id,
        state=state,
        total_requests=total,
        completed_requests=counts.get("succeeded", 0),
        failed_requests=(
            counts.get("errored", 0)
            + counts.get("canceled", 0)
            + counts.get("expired", 0)
        ),
    )


def _anthropic_results(
    api_key: str,
    raw_batch_id: str,
    n_prompts: int,
) -> list[BatchResult]:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://api.anthropic.com/v1/messages/batches/{raw_batch_id}/results",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        res.raise_for_status()
        text = res.text

    result_map: dict[int, BatchResult] = {}
    for line in text.strip().split("\n"):
        if not line:
            continue
        entry = json.loads(line)
        idx = int(entry["custom_id"].removeprefix("req-"))
        if entry["result"]["type"] == "succeeded" and entry["result"].get("message"):
            content = "".join(
                b.get("text", "")
                for b in entry["result"]["message"]["content"]
                if b.get("type") == "text"
            )
            result_map[idx] = BatchResult(index=idx, success=True, response=content)
        else:
            err_msg = "Unknown error"
            if entry["result"].get("error"):
                err_msg = entry["result"]["error"].get("message", err_msg)
            result_map[idx] = BatchResult(index=idx, success=False, error=err_msg)

    return [
        result_map.get(i, BatchResult(index=i, success=False, error="Missing result"))
        for i in range(n_prompts)
    ]


# --- Gemini (Task 109) ---

def _gemini_submit(
    api_key: str,
    model: str,
    prompts: list[str],
    system_prompt: str | None = None,
    params: dict[str, Any] | None = None,
) -> str:
    httpx = _require_httpx()

    requests = []
    for i, prompt in enumerate(prompts):
        req: dict[str, Any] = {
            "request": {
                "model": f"models/{model}",
                "contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": params or {},
            },
            "metadata": {"id": f"req-{i}"},
        }
        if system_prompt:
            req["request"]["system_instruction"] = {"parts": [{"text": system_prompt}]}
        requests.append(req)

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchGenerateContent",
            params={"key": api_key},
            headers={"Content-Type": "application/json"},
            json={"requests": requests},
        )
        res.raise_for_status()
        return res.json()["name"]


def _gemini_status(api_key: str, raw_batch_id: str) -> BatchStatus:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://generativelanguage.googleapis.com/v1beta/{raw_batch_id}",
            params={"key": api_key},
        )
        res.raise_for_status()
        batch = res.json()

    state_map: dict[str, BatchState] = {
        "JOB_STATE_SUCCEEDED": "completed",
        "JOB_STATE_FAILED": "failed",
        "JOB_STATE_CANCELLED": "failed",
        "JOB_STATE_EXPIRED": "expired",
        "JOB_STATE_PENDING": "pending",
        "JOB_STATE_RUNNING": "in_progress",
    }

    stats = batch.get("completionStats", {})
    return BatchStatus(
        batch_id=raw_batch_id,
        state=state_map.get(batch.get("state", ""), "in_progress"),
        total_requests=(
            (stats.get("successfulCount", 0) or 0)
            + (stats.get("failedCount", 0) or 0)
            + (stats.get("incompleteCount", 0) or 0)
        ),
        completed_requests=stats.get("successfulCount", 0) or 0,
        failed_requests=stats.get("failedCount", 0) or 0,
    )


def _gemini_results(
    api_key: str,
    raw_batch_id: str,
    n_prompts: int,
) -> list[BatchResult]:
    httpx = _require_httpx()

    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        res = client.get(
            f"https://generativelanguage.googleapis.com/v1beta/{raw_batch_id}",
            params={"key": api_key},
        )
        res.raise_for_status()
        batch = res.json()

    state = batch.get("state", "")
    if state not in ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED"):
        if state in ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED"):
            raise RuntimeError(f"Gemini batch {raw_batch_id} {state}")
        return []

    result_map: dict[int, BatchResult] = {}
    for resp in batch.get("responses", []):
        id_str = (resp.get("metadata") or {}).get("id", "")
        try:
            idx = int(id_str.removeprefix("req-"))
        except ValueError:
            continue

        if resp.get("error"):
            result_map[idx] = BatchResult(
                index=idx, success=False, error=resp["error"].get("message", "Unknown error")
            )
        else:
            text = ""
            candidates = (resp.get("response") or {}).get("candidates", [])
            if candidates:
                parts = (candidates[0].get("content") or {}).get("parts", [])
                if parts:
                    text = parts[0].get("text", "")
            result_map[idx] = BatchResult(index=idx, success=True, response=text)

    return [
        result_map.get(i, BatchResult(index=i, success=False, error="Missing result"))
        for i in range(n_prompts)
    ]


# ---------------------------------------------------------------------------
# BatchProcessor (Task 105)
# ---------------------------------------------------------------------------

class BatchProcessor:
    """Batch processing with provider dispatch and durable metadata."""

    def __init__(self, state_dir: Path | None = None) -> None:
        self._state_dir = state_dir

    def submit(
        self,
        provider: BatchProvider,
        api_key: str,
        model: str,
        prompts: list[str],
        system_prompt: str | None = None,
        params: dict[str, Any] | None = None,
        state_dir: Path | None = None,
    ) -> str:
        """Submit a batch of prompts. Returns an encoded batch ID."""
        if not prompts:
            raise ValueError("prompts must be non-empty")
        effective_state_dir = state_dir or self._state_dir

        if provider == "openai":
            raw_batch_id = _openai_submit(api_key, model, prompts, system_prompt, params)
        elif provider == "anthropic":
            raw_batch_id = _anthropic_submit(api_key, model, prompts, system_prompt, params)
        elif provider == "gemini":
            raw_batch_id = _gemini_submit(api_key, model, prompts, system_prompt, params)
        else:
            raise ValueError(f"Unsupported batch provider: {provider}")

        batch_id = encode_batch_id(provider, api_key, len(prompts), raw_batch_id)
        write_metadata(batch_id, api_key, provider, len(prompts), effective_state_dir)
        return batch_id

    def status(self, batch_id: str, api_key: str) -> BatchStatus:
        """Check the status of a batch job.

        Args:
            batch_id: Encoded batch ID.
            api_key: API key for the provider (must match the key fingerprint in batch ID).
        """
        decoded = _validate_batch_request(batch_id, api_key, self._state_dir)

        if decoded.provider == "openai":
            return _openai_status(api_key, decoded.raw_batch_id)
        elif decoded.provider == "anthropic":
            return _anthropic_status(api_key, decoded.raw_batch_id)
        elif decoded.provider == "gemini":
            return _gemini_status(api_key, decoded.raw_batch_id)
        else:
            raise ValueError(f"Unsupported batch provider: {decoded.provider}")

    def results(
        self,
        batch_id: str,
        api_key: str,
        state_dir: Path | None = None,
    ) -> list[BatchResult]:
        """Retrieve batch results.

        Metadata is deleted only once the provider returns terminal results.
        Pending polls that return an empty list keep the metadata on disk.

        Args:
            batch_id: Encoded batch ID.
            api_key: API key for the provider (must match the key fingerprint in batch ID).
        """
        effective_state_dir = state_dir or self._state_dir
        decoded = _validate_batch_request(batch_id, api_key, effective_state_dir)

        if decoded.provider == "openai":
            results = _openai_results(api_key, decoded.raw_batch_id, decoded.n_prompts)
        elif decoded.provider == "anthropic":
            results = _anthropic_results(api_key, decoded.raw_batch_id, decoded.n_prompts)
        elif decoded.provider == "gemini":
            results = _gemini_results(api_key, decoded.raw_batch_id, decoded.n_prompts)
        else:
            raise ValueError(f"Unsupported batch provider: {decoded.provider}")

        if results:
            delete_metadata(batch_id, effective_state_dir)
        return results


# ---------------------------------------------------------------------------
# E2E Test Stub (Task 116 — out of scope)
# ---------------------------------------------------------------------------

# TODO: E2E tests with real API keys (Task 116)
# - OpenAI batch submit/status/results roundtrip
# - Anthropic batch submit/status/results roundtrip
# - Gemini batch submit/status/results roundtrip


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

__all__ = [
    "BatchMetadata",
    "BatchProcessor",
    "BatchProvider",
    "BatchResult",
    "BatchState",
    "BatchStatus",
    "DecodedBatchId",
    "decode_batch_id",
    "delete_metadata",
    "encode_batch_id",
    "read_metadata",
    "write_metadata",
]
