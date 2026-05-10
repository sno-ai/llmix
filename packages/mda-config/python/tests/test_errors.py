from __future__ import annotations

from snoai_mda_config import ErrorCategory


def test_error_category_values_mirror_typescript() -> None:
    assert [category.value for category in ErrorCategory] == [
        "invalid-encoding",
        "unterminated-frontmatter",
        "missing-required-frontmatter",
        "frontmatter-yaml-parse-error",
        "schema-violation",
        "signature-digest-mismatch",
        "signatures-without-integrity",
        "integrity-mismatch",
        "rekor-entry-type-mismatch",
        "rekor-inclusion-failure",
        "fulcio-chain-failure",
        "signature-verification-failure",
        "missing-required-integrity",
        "missing-required-signature",
        "no-trusted-signature",
        "insufficient-trusted-signatures",
        "trust-policy-violation",
        "unknown-signer-method",
        "requires-not-satisfied",
        "project-schema-violation",
    ]


def test_error_category_has_uppercase_aliases_for_python_examples() -> None:
    assert ErrorCategory.INTEGRITY_MISMATCH is ErrorCategory.IntegrityMismatch
