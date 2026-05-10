"""Shared Config Registry helpers."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from llmix.mda_loader_validation import _validate_runtime_config
from llmix.types import (
    ConfigAccessError,
    ConfigNotFoundError,
    InvalidConfigError,
    SecurityError,
)

_MANIFEST_SCHEMA_VERSION = 1
_REVISION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_REVISION_TOKEN_PATTERN = re.compile(r"\d+|\D+")
_DIGIT_TOKEN_PATTERN = re.compile(r"^\d+$")
_SHA256_PATTERN = re.compile(r"^[a-f0-9]{64}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json_bytes(value: dict[str, Any]) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(8192)
                if not chunk:
                    break
                digest.update(chunk)
    except FileNotFoundError:
        raise ConfigNotFoundError(
            f"Required registry artifact not found: {path}"
        ) from None
    except PermissionError:
        raise ConfigAccessError(
            f"Permission denied reading registry artifact: {path}"
        ) from None
    return digest.hexdigest()


def _validate_sha256(sha256: str, label: str) -> None:
    if not _SHA256_PATTERN.match(sha256):
        raise InvalidConfigError(f"Invalid SHA-256 digest for {label}")


def _current_pointer_sha256(pointer: dict[str, str]) -> str:
    return _sha256_bytes(
        _canonical_json_bytes(
            {
                "manifest_sha256": pointer["manifest_sha256"],
                "revision": pointer["revision"],
            }
        )
    )


def _snapshot_registry_path(revision: str, relative_path: str) -> str:
    return posixpath.join("snapshots", revision, relative_path)


def _snapshot_relative_path(revision: str, registry_path: str) -> str:
    prefix = _snapshot_registry_path(revision, "")
    if not registry_path.startswith(prefix):
        raise SecurityError(
            f"Registry root file is outside the active snapshot: {registry_path}"
        )
    relative_path = registry_path[len(prefix) :]
    if not relative_path:
        raise SecurityError(
            f"Registry root file path is not a snapshot artifact: {registry_path}"
        )
    return relative_path


def _compare_revision(left: str, right: str) -> int:
    _validate_revision(left)
    _validate_revision(right)
    left_tokens = _REVISION_TOKEN_PATTERN.findall(left)
    right_tokens = _REVISION_TOKEN_PATTERN.findall(right)
    length = max(len(left_tokens), len(right_tokens))
    for index in range(length):
        if index >= len(left_tokens):
            return -1
        if index >= len(right_tokens):
            return 1
        comparison = _compare_revision_token(left_tokens[index], right_tokens[index])
        if comparison != 0:
            return comparison
    return 0


def _compare_revision_token(left: str, right: str) -> int:
    if _DIGIT_TOKEN_PATTERN.match(left) and _DIGIT_TOKEN_PATTERN.match(right):
        left_normalized = left.lstrip("0") or "0"
        right_normalized = right.lstrip("0") or "0"
        if len(left_normalized) != len(right_normalized):
            return -1 if len(left_normalized) < len(right_normalized) else 1
        left = left_normalized
        right = right_normalized
    if left == right:
        return 0
    return -1 if left < right else 1


def _read_json_file(path: Path) -> dict[str, Any]:
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigNotFoundError(f"Required registry file not found: {path}") from None
    except PermissionError:
        raise ConfigAccessError(
            f"Permission denied reading registry file: {path}"
        ) from None

    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise InvalidConfigError(
            f"Invalid JSON in registry file {path}: {exc}"
        ) from exc

    if not isinstance(value, dict):
        raise InvalidConfigError(f"Registry file must contain a JSON object: {path}")

    return value


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _fsync_file(path)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    _write_bytes(path, _canonical_json_bytes(value))


def _atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        _write_json(temp_path, value)
        os.replace(temp_path, path)
        _fsync_dir(path.parent)
    finally:
        try:
            temp_path.unlink()
        except OSError:
            pass


def _fsync_file(path: Path) -> None:
    try:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    except (AttributeError, OSError):
        return


def _fsync_dir(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except (AttributeError, OSError):
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _validate_revision(revision: str) -> None:
    if not revision:
        raise InvalidConfigError("Registry revision cannot be empty")
    if any(char in revision for char in ("/", "\\", "..")):
        raise SecurityError(f"Invalid registry revision: {revision!r}")
    if not _REVISION_PATTERN.match(revision):
        raise InvalidConfigError(f"Invalid registry revision format: {revision!r}")


def _validate_resolved_config(path: Path, value: dict[str, Any]) -> None:
    _validate_runtime_config(value, path)


def _is_legacy_yaml_authoring_path(path: Path) -> bool:
    return path.name.lower().endswith((".yaml", ".yml"))


def _parse_mda_preset_name(path: Path) -> str | None:
    if not path.name.lower().endswith(".mda"):
        return None
    return path.name[:-4]


def _require_manifest_string(entry: dict[str, Any], key: str, preset_id: str) -> str:
    value = entry.get(key)
    if not isinstance(value, str):
        raise InvalidConfigError(
            f"Config Registry manifest entry is missing {key}: {preset_id}"
        )
    return value
