from __future__ import annotations

import hashlib

import pytest

from snoai_mda_config import ErrorCategory, MdaConfigError
from snoai_mda_config.frontmatter import extract_frontmatter, parse_frontmatter_yaml
from snoai_mda_config.integrity import (
    canonicalize_artifact,
    hash_canonical,
    normalize_body,
    verify_integrity,
)

from .conftest import FIXTURES


def test_body_normalization_empty_body_has_no_newline() -> None:
    assert normalize_body("") == ""


def test_body_normalization_strips_trailing_spaces_and_keeps_one_newline() -> None:
    assert normalize_body("foo  \nbar\t\nbaz\n") == "foo\nbar\nbaz\n"


def test_body_normalization_collapses_trailing_blank_lines() -> None:
    assert normalize_body("foo\n\n\n") == "foo\n"


def test_integrity_accepts_matching_fixture() -> None:
    extracted = extract_frontmatter((FIXTURES / "valid/02-with-integrity.mda").read_bytes())
    frontmatter = parse_frontmatter_yaml(extracted.frontmatter_str)

    verify_integrity(frontmatter, extracted.body_str, frontmatter["integrity"])


def test_canonical_bytes_match_typescript_baseline() -> None:
    """PRD §3 byte-equivalence pins Python canonical bytes to TS-generated SHA."""
    extracted = extract_frontmatter((FIXTURES / "valid/02-with-integrity.mda").read_bytes())
    frontmatter = parse_frontmatter_yaml(extracted.frontmatter_str)
    canonical = canonicalize_artifact(frontmatter, extracted.body_str)
    expected = frontmatter["integrity"]["digest"].split(":", 1)[1]

    assert hashlib.sha256(canonical).hexdigest() == expected


def test_integrity_rejects_mismatch() -> None:
    extracted = extract_frontmatter((FIXTURES / "invalid/11-integrity-mismatch.mda").read_bytes())
    frontmatter = parse_frontmatter_yaml(extracted.frontmatter_str)

    with pytest.raises(MdaConfigError) as exc_info:
        verify_integrity(frontmatter, extracted.body_str, frontmatter["integrity"])

    assert exc_info.value.category is ErrorCategory.IntegrityMismatch


def test_canonical_bytes_are_deterministic_across_key_order() -> None:
    first = canonicalize_artifact({"b": 2, "a": 1}, "")
    second = canonicalize_artifact({"a": 1, "b": 2}, "")

    assert hash_canonical(first, "sha256") == hash_canonical(second, "sha256")
