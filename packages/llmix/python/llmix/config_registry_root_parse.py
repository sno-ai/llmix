"""Signed Config Registry root parsing helpers."""

from __future__ import annotations

from typing import Any, cast

from llmix.config_registry_common import (
    _snapshot_registry_path,
    _validate_revision,
    _validate_sha256,
)
from llmix.config_registry_types import (
    REGISTRY_ROOT_ENVELOPE_SCHEMA,
    REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
    REGISTRY_ROOT_PAYLOAD_TYPE,
    REGISTRY_ROOT_SCHEMA,
    REGISTRY_ROOT_SCHEMA_VERSION,
)
from llmix.types import InvalidConfigError


def parse_registry_root_envelope(
    value: dict[str, Any], source_path: str
) -> dict[str, Any]:
    if require_string(value, "schema", source_path) != REGISTRY_ROOT_ENVELOPE_SCHEMA:
        raise InvalidConfigError(f"Unsupported registry root envelope schema in {source_path}")
    if (
        require_number(value, "schema_version", source_path)
        != REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION
    ):
        raise InvalidConfigError(
            f"Unsupported registry root envelope schema version in {source_path}"
        )
    payload = parse_registry_root_payload(
        require_object(value, "payload", source_path), source_path
    )
    integrity = parse_registry_root_integrity(
        require_object(value, "integrity", source_path), source_path
    )
    payload_sha256 = require_string(value, "payload_sha256", source_path)
    _validate_sha256(payload_sha256, "registry root payload")
    signatures_value = value.get("signatures")
    if not isinstance(signatures_value, list):
        raise InvalidConfigError(
            f"Registry root envelope signatures must be an array: {source_path}"
        )
    if integrity["digest"] != f"sha256:{payload_sha256}":
        raise InvalidConfigError(
            f"Registry root integrity digest does not match payload_sha256: {source_path}"
        )
    return {
        "schema": REGISTRY_ROOT_ENVELOPE_SCHEMA,
        "schema_version": REGISTRY_ROOT_ENVELOPE_SCHEMA_VERSION,
        "payload": payload,
        "integrity": integrity,
        "payload_sha256": payload_sha256,
        "signatures": [
            parse_registry_root_signature(signature, source_path)
            for signature in signatures_value
        ],
    }


def parse_registry_root_file_digest(
    value: Any, source_path: str
) -> dict[str, str]:
    entry = ensure_object(value, f"registry root file entry: {source_path}")
    role = require_string(entry, "role", source_path)
    if role not in {"authoring", "resolved"}:
        raise InvalidConfigError(
            f"Registry root file entry has invalid role: {source_path}"
        )
    path = require_string(entry, "path", source_path)
    sha256 = require_string(entry, "sha256", source_path)
    _validate_sha256(sha256, f"registry root file {path}")
    return {"path": path, "sha256": sha256, "role": role}


def parse_registry_root_signature(value: Any, source_path: str) -> dict[str, Any]:
    entry = ensure_object(value, f"registry root signature: {source_path}")
    algorithm = require_string(entry, "algorithm", source_path)
    if algorithm not in {"ed25519", "ecdsa-p256", "rsa-pss-sha256"}:
        raise InvalidConfigError(
            f"Registry root signature has unsupported algorithm: {source_path}"
        )
    payload_type = require_string(entry, "payload-type", source_path)
    if payload_type != REGISTRY_ROOT_PAYLOAD_TYPE:
        raise InvalidConfigError(
            f"Registry root signature payload-type mismatch: {source_path}"
        )
    signature: dict[str, Any] = {
        "signer": require_string(entry, "signer", source_path),
        "key-id": require_string(entry, "key-id", source_path),
        "payload-digest": require_string(entry, "payload-digest", source_path),
        "algorithm": algorithm,
        "signature": require_string(entry, "signature", source_path),
        "payload-type": payload_type,
    }
    if "rekor-log-id" in entry:
        signature["rekor-log-id"] = require_string(entry, "rekor-log-id", source_path)
    if "rekor-log-index" in entry:
        index = entry["rekor-log-index"]
        if not isinstance(index, int):
            raise InvalidConfigError(
                f"Registry root signature rekor-log-index must be a number: {source_path}"
            )
        signature["rekor-log-index"] = index
    return signature


def require_object(value: dict[str, Any], key: str, source_path: str) -> dict[str, Any]:
    return ensure_object(value.get(key), f"{source_path}.{key}")


def ensure_object(value: Any, source_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidConfigError(f"Registry root field must be an object: {source_path}")
    return cast("dict[str, Any]", value)


def require_string(value: dict[str, Any], key: str, source_path: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise InvalidConfigError(
            f"Registry root field must be a string: {source_path}.{key}"
        )
    return item


def require_number(value: dict[str, Any], key: str, source_path: str) -> int:
    item = value.get(key)
    if not isinstance(item, int):
        raise InvalidConfigError(
            f"Registry root field must be a number: {source_path}.{key}"
        )
    return item


def parse_registry_root_payload(
    value: dict[str, Any], source_path: str
) -> dict[str, Any]:
    if require_string(value, "schema", source_path) != REGISTRY_ROOT_SCHEMA:
        raise InvalidConfigError(f"Unsupported registry root payload schema in {source_path}")
    if require_number(value, "schema_version", source_path) != REGISTRY_ROOT_SCHEMA_VERSION:
        raise InvalidConfigError(
            f"Unsupported registry root payload schema version in {source_path}"
        )
    revision = require_string(value, "revision", source_path)
    _validate_revision(revision)
    files_value = value.get("files")
    if not isinstance(files_value, list):
        raise InvalidConfigError(
            f"Registry root payload files must be an array: {source_path}"
        )
    payload = {
        "schema": REGISTRY_ROOT_SCHEMA,
        "schema_version": REGISTRY_ROOT_SCHEMA_VERSION,
        "revision": revision,
        "published_at": require_string(value, "published_at", source_path),
        "current": _parse_registry_root_current(
            require_object(value, "current", source_path), source_path
        ),
        "manifest": _parse_registry_root_manifest(
            require_object(value, "manifest", source_path), source_path
        ),
        "files": [
            parse_registry_root_file_digest(file, source_path)
            for file in files_value
        ],
    }
    _validate_registry_root_payload_bindings(payload, source_path)
    return payload


def _validate_registry_root_payload_bindings(
    payload: dict[str, Any], source_path: str
) -> None:
    if payload["current"]["revision"] != payload["revision"]:
        raise InvalidConfigError(
            f"Registry root current revision does not match payload revision: {source_path}"
        )
    if payload["current"]["manifest_sha256"] != payload["manifest"]["sha256"]:
        raise InvalidConfigError(
            f"Registry root current manifest digest does not match manifest binding: {source_path}"
        )
    if payload["manifest"]["path"] != _snapshot_registry_path(
        payload["revision"], "manifest.json"
    ):
        raise InvalidConfigError(
            f"Registry root manifest path does not match payload revision: {source_path}"
        )


def _parse_registry_root_current(
    value: dict[str, Any], source_path: str
) -> dict[str, str]:
    path = require_string(value, "path", source_path)
    if path != "current.json":
        raise InvalidConfigError(
            f"Registry root current binding must point to current.json: {source_path}"
        )
    revision = require_string(value, "revision", source_path)
    _validate_revision(revision)
    manifest_sha256 = require_string(value, "manifest_sha256", source_path)
    sha256 = require_string(value, "sha256", source_path)
    _validate_sha256(manifest_sha256, "registry root current manifest")
    _validate_sha256(sha256, "registry root current binding")
    return {
        "path": "current.json",
        "revision": revision,
        "manifest_sha256": manifest_sha256,
        "sha256": sha256,
    }


def _parse_registry_root_manifest(
    value: dict[str, Any], source_path: str
) -> dict[str, str]:
    path = require_string(value, "path", source_path)
    sha256 = require_string(value, "sha256", source_path)
    _validate_sha256(sha256, f"registry root manifest {path}")
    return {"path": path, "sha256": sha256}


def parse_registry_root_integrity(
    value: dict[str, Any], source_path: str
) -> dict[str, str]:
    algorithm = require_string(value, "algorithm", source_path)
    if algorithm != "sha256":
        raise InvalidConfigError(f"Registry root integrity must use sha256: {source_path}")
    digest = require_string(value, "digest", source_path)
    if not digest.startswith("sha256:"):
        raise InvalidConfigError(
            f"Registry root integrity digest must be sha256-prefixed: {source_path}"
        )
    _validate_sha256(digest.removeprefix("sha256:"), "registry root integrity")
    return {"algorithm": algorithm, "digest": digest}
