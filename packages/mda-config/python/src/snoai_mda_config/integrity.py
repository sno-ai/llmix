"""MDA §08 integrity canonicalization, hashing, and verification."""

from __future__ import annotations

import hashlib
from typing import Any, Literal, TypedDict

import jcs  # type: ignore[reportMissingTypeStubs]

from .errors import ErrorCategory, MdaConfigError

HashAlgorithm = Literal["sha256", "sha384", "sha512"]


class IntegrityField(TypedDict):
    """MDA §08-2 top-level integrity field."""

    algorithm: HashAlgorithm
    digest: str


def _strip_security_fields(frontmatter: dict[str, Any]) -> dict[str, Any]:
    stripped = dict(frontmatter)
    stripped.pop("integrity", None)
    stripped.pop("signatures", None)
    return stripped


def normalize_body(body_str: str) -> str:
    """MDA §08-3.3 normalizes body text before digest computation."""
    if body_str == "":
        return ""
    stripped = [line.rstrip(" \t") for line in body_str.split("\n")]
    while stripped and stripped[-1] == "":
        stripped.pop()
    if not stripped:
        return ""
    return "\n".join(stripped) + "\n"


def canonicalize_artifact(frontmatter: dict[str, Any], body_str: str) -> bytes:
    """MDA §08-3 assembles canonical single-file source artifact bytes."""
    raw_jcs = jcs.canonicalize(_strip_security_fields(frontmatter))  # type: ignore[reportUnknownMemberType]
    jcs_bytes = raw_jcs if isinstance(raw_jcs, bytes) else raw_jcs.encode("utf-8")
    normalized_body = normalize_body(body_str).encode("utf-8")
    return b"---\n" + jcs_bytes + b"\n---\n" + normalized_body


def parse_digest(digest: str) -> tuple[str, str]:
    """MDA §08-2 parses '<algorithm>:<lowercase-hex>' digest strings."""
    algorithm, sep, hex_digest = digest.partition(":")
    if not sep or not algorithm:
        raise MdaConfigError(
            ErrorCategory.SchemaViolation,
            "integrity.digest is not in '<algorithm>:<hex>' form",
            {"digest": digest},
        )
    return algorithm, hex_digest


def hash_canonical(canonical_bytes: bytes, algorithm: HashAlgorithm) -> str:
    """MDA §08-3.5 hashes canonical bytes with the declared algorithm."""
    return hashlib.new(algorithm, canonical_bytes).hexdigest()


def verify_integrity(
    frontmatter: dict[str, Any],
    body_str: str,
    integrity: IntegrityField,
) -> None:
    """MDA §08-4 verifies declared integrity against recomputed canonical hash."""
    algorithm, expected_hex = parse_digest(integrity["digest"])
    if algorithm != integrity["algorithm"]:
        raise MdaConfigError(
            ErrorCategory.SchemaViolation,
            "integrity.digest prefix does not match integrity.algorithm",
            {"algorithm": integrity["algorithm"], "digestPrefix": algorithm},
        )

    computed = hash_canonical(canonicalize_artifact(frontmatter, body_str), integrity["algorithm"])
    if computed != expected_hex:
        raise MdaConfigError(
            ErrorCategory.IntegrityMismatch,
            "computed digest does not match integrity.digest",
            {"expected": expected_hex, "computed": computed, "algorithm": integrity["algorithm"]},
        )
