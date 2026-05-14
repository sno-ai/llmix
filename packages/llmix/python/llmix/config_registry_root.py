"""Signed Config Registry root helpers."""

from __future__ import annotations

import inspect
import json
from datetime import datetime
from typing import Any, cast

from snoai_mda_config import IntegrityField, MdaConfigError, verify_signatures

from llmix.config_registry_common import (
    _canonical_json_bytes,
    _compare_revision,
    _current_pointer_sha256,
    _sha256_bytes,
    _compiled_registry_path,
    _validate_sha256,
)
from llmix.config_registry_types import (
    REGISTRY_ROOT_ENVELOPE_SCHEMA,
    REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
    REGISTRY_ROOT_PAYLOAD_TYPE,
    REGISTRY_ROOT_SCHEMA,
    REGISTRY_ROOT_SCHEMA_VERSION,
    RegistryRootFreshnessInput,
    RegistryRootSigningInput,
    RegistryRootSigningOptions,
    RegistryRootVerificationOptions,
)
from llmix.config_registry_root_parse import (
    ensure_object,
    parse_registry_root_file_digest,
    parse_registry_root_signature,
    require_object,
    require_string,
)
from llmix.types import InvalidConfigError, SecurityError


def registry_root_file_digests(manifest: dict[str, Any]) -> list[dict[str, str]]:
    files: list[dict[str, str]] = []
    revision = require_string(manifest, "revision", "registry manifest")
    presets = require_object(manifest, "presets", "registry manifest")
    for preset_id, entry_value in sorted(presets.items()):
        entry = ensure_object(entry_value, f"registry manifest preset {preset_id}")
        for role, path_key, sha_key in (
            ("source", "source_path", "source_sha256"),
            ("resolved", "resolved_path", "resolved_sha256"),
        ):
            relative_path = require_string(entry, path_key, f"preset {preset_id}")
            sha256 = require_string(entry, sha_key, f"preset {preset_id}")
            path = _compiled_registry_path(revision, relative_path)
            _validate_sha256(sha256, f"registry root file {path} ({preset_id})")
            files.append({"path": path, "sha256": sha256, "role": role})
    return sorted(files, key=lambda item: item["path"])


def sorted_registry_root_files(files: list[dict[str, Any]]) -> list[dict[str, str]]:
    sorted_files = sorted(
        (parse_registry_root_file_digest(file, "registry root files") for file in files),
        key=lambda item: item["path"],
    )
    seen: set[str] = set()
    for file in sorted_files:
        if file["path"] in seen:
            raise InvalidConfigError(
                f"Registry root contains duplicate file path: {file['path']}"
            )
        seen.add(file["path"])
    return sorted_files


def build_registry_root_payload(
    manifest: dict[str, Any], manifest_sha256: str
) -> dict[str, Any]:
    _validate_sha256(manifest_sha256, "registry root manifest")
    revision = require_string(manifest, "revision", "registry manifest")
    published_at = require_string(manifest, "published_at", "registry manifest")
    current = {"revision": revision, "manifest_sha256": manifest_sha256}
    return {
        "schema": REGISTRY_ROOT_SCHEMA,
        "schema_version": REGISTRY_ROOT_SCHEMA_VERSION,
        "revision": revision,
        "published_at": published_at,
        "current": {
            "path": "current.json",
            "revision": revision,
            "manifest_sha256": manifest_sha256,
            "sha256": _current_pointer_sha256(current),
        },
        "manifest": {
            "path": _compiled_registry_path(revision, "manifest.json"),
            "sha256": manifest_sha256,
        },
        "files": registry_root_file_digests(manifest),
    }


def create_registry_root_envelope(
    payload: dict[str, Any], options: RegistryRootSigningOptions
) -> dict[str, Any]:
    signing_input = _registry_root_signing_input(payload)
    signed = options.signer(signing_input)
    if inspect.isawaitable(signed):
        raise InvalidConfigError("Registry root signer must be synchronous")
    signatures = [
        _normalize_registry_root_signature(signature)
        for signature in (signed if isinstance(signed, list | tuple) else [signed])
    ]
    min_signatures = _required_signature_count(options.min_signatures)
    if len(signatures) < min_signatures:
        raise InvalidConfigError(
            f"Registry root signer returned {len(signatures)} signatures; "
            f"expected at least {min_signatures}"
        )
    return {
        "schema": REGISTRY_ROOT_ENVELOPE_SCHEMA,
        "schema_version": REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
        "payload": payload,
        "integrity": signing_input.integrity,
        "payload_sha256": signing_input.payload_sha256,
        "signatures": signatures,
    }


def verify_registry_root_signatures(
    envelope: dict[str, Any], options: RegistryRootVerificationOptions
) -> None:
    signing_input = _registry_root_signing_input_for_envelope(envelope)
    try:
        verify_signatures(
            cast("list[Any]", envelope["signatures"]),
            cast("IntegrityField", envelope["integrity"]),
            options.trust_policy,
            rekor_client=options.rekor_client,
            sigstore_verifier=options.sigstore_verifier,
            did_web_verifier=options.did_web_verifier,
            payload_bytes=signing_input.canonical_payload.encode("utf-8"),
        )
    except MdaConfigError as exc:
        raise SecurityError(f"Registry root signature verification failed: {exc}") from exc


def enforce_registry_root_freshness(
    envelope: dict[str, Any], options: RegistryRootVerificationOptions
) -> None:
    payload = envelope["payload"]
    revision = payload["revision"]
    if options.expected_revision is not None and revision != options.expected_revision:
        raise SecurityError(
            f"Registry root revision mismatch: expected {options.expected_revision}, got {revision}"
        )
    if options.minimum_revision is not None:
        if _compare_revision(revision, options.minimum_revision) < 0:
            raise SecurityError(
                f"Registry root revision {revision} is older than minimum {options.minimum_revision}"
            )
    if options.minimum_published_at is not None:
        _enforce_minimum_published_at(
            payload["published_at"], options.minimum_published_at
        )
    if options.high_watermark is not None:
        signing_input = _registry_root_signing_input_for_envelope(envelope)
        approved = options.high_watermark(
            RegistryRootFreshnessInput(
                payload=signing_input.payload,
                canonical_payload=signing_input.canonical_payload,
                integrity=signing_input.integrity,
                payload_type=signing_input.payload_type,
                payload_sha256=signing_input.payload_sha256,
                envelope=envelope,
            )
        )
        if inspect.isawaitable(approved):
            raise SecurityError("Registry root high-watermark policy must be synchronous")
        if not approved:
            raise SecurityError("Registry root rejected by high-watermark policy")


def _registry_root_signing_input(payload: dict[str, Any]) -> RegistryRootSigningInput:
    return _registry_root_signing_input_with_bytes(
        payload, _canonical_compact_json_bytes(payload)
    )


def _registry_root_signing_input_for_envelope(
    envelope: dict[str, Any],
) -> RegistryRootSigningInput:
    current = _registry_root_signing_input(envelope["payload"])
    if _registry_root_digest_matches_envelope(current, envelope):
        return current

    legacy = _registry_root_signing_input_with_bytes(
        envelope["payload"], _canonical_json_bytes(envelope["payload"])
    )
    if _registry_root_digest_matches_envelope(legacy, envelope):
        return legacy

    raise InvalidConfigError("Registry root payload digest mismatch")


def _registry_root_signing_input_with_bytes(
    payload: dict[str, Any], canonical_payload_bytes: bytes
) -> RegistryRootSigningInput:
    canonical_payload = canonical_payload_bytes.decode("utf-8")
    payload_sha256 = _sha256_bytes(canonical_payload_bytes)
    return RegistryRootSigningInput(
        payload=payload,
        canonical_payload=canonical_payload,
        integrity={"algorithm": "sha256", "digest": f"sha256:{payload_sha256}"},
        payload_type=REGISTRY_ROOT_PAYLOAD_TYPE,
        payload_sha256=payload_sha256,
    )


def _registry_root_digest_matches_envelope(
    signing_input: RegistryRootSigningInput, envelope: dict[str, Any]
) -> bool:
    return (
        signing_input.payload_sha256 == envelope["payload_sha256"]
        and signing_input.integrity["digest"] == envelope["integrity"]["digest"]
    )


def _canonical_compact_json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _normalize_registry_root_signature(value: dict[str, Any]) -> dict[str, Any]:
    try:
        normalized = json.loads(json.dumps(value))
    except (TypeError, ValueError) as exc:
        raise InvalidConfigError("Registry root signature must be JSON serializable") from exc
    return parse_registry_root_signature(normalized, "registry root signer")


def _required_signature_count(min_signatures: int) -> int:
    if not isinstance(min_signatures, int) or min_signatures < 1:
        raise InvalidConfigError("Registry root min_signatures must be an integer >= 1")
    return min_signatures


def _enforce_minimum_published_at(
    published_at: str, minimum_published_at: str | datetime
) -> None:
    actual = _parse_datetime(published_at)
    minimum = (
        minimum_published_at
        if isinstance(minimum_published_at, datetime)
        else _parse_datetime(minimum_published_at)
    )
    if actual < minimum:
        raise SecurityError(
            f"Registry root published_at {published_at} is older than minimum {minimum.isoformat()}"
        )


def _parse_datetime(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SecurityError("Registry root published_at freshness values must be valid dates") from exc
