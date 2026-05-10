"""MDA §11-2 source-mode loader stages A/B/C/D/E/F/G."""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypeVar, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import BaseModel, ValidationError

from .errors import ErrorCategory, MdaConfigError
from .frontmatter import extract_frontmatter, parse_frontmatter_yaml
from .integrity import IntegrityField
from .integrity import verify_integrity as run_integrity_check
from .mda_schema import MDA_SOURCE_SCHEMA
from .requires import RequiresEnvironment
from .requires import enforce_requires as run_requires_check
from .signature import DidWebVerifier, RekorClient, SignatureEntry, SigstoreVerifier
from .signature import verify_signatures as run_signature_check
from .trust_policy import TrustPolicy, validate_trust_policy

ModelT = TypeVar("ModelT", bound=BaseModel)

_validator_cache: Draft202012Validator | None = None


def _mda_source_validator() -> Draft202012Validator:
    global _validator_cache  # noqa: PLW0603
    if _validator_cache is None:
        _validator_cache = Draft202012Validator(MDA_SOURCE_SCHEMA, format_checker=FormatChecker())
    return _validator_cache


def _json_path(error: JsonSchemaValidationError) -> str:
    path = ".".join(str(part) for part in error.absolute_path)
    return path or "<root>"


def _validate_mda_source(frontmatter: dict[str, Any]) -> None:
    raw_errors = list(
        _mda_source_validator().iter_errors(frontmatter)  # type: ignore[reportUnknownMemberType]
    )
    errors = sorted(cast("list[JsonSchemaValidationError]", raw_errors), key=_json_path)
    if errors:
        raise MdaConfigError(
            ErrorCategory.SchemaViolation,
            "frontmatter failed MDA source-mode JSON Schema validation",
            {
                "errors": [
                    {"path": _json_path(error), "message": error.message} for error in errors
                ]
            },
        )


def _cross_field_check(frontmatter: dict[str, Any]) -> None:
    integrity = frontmatter.get("integrity")
    signatures = frontmatter.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        return
    if not isinstance(integrity, dict):
        raise MdaConfigError(
            ErrorCategory.SignaturesWithoutIntegrity,
            "signatures[] present without integrity",
        )
    integrity_dict = cast("dict[str, Any]", integrity)
    digest = integrity_dict.get("digest")
    signature_entries = cast("list[Any]", signatures)
    for raw_sig in signature_entries:
        sig = cast("dict[str, Any]", raw_sig) if isinstance(raw_sig, dict) else {}
        if sig.get("payload-digest") != digest:
            raise MdaConfigError(
                ErrorCategory.SignatureDigestMismatch,
                "signatures[i].payload-digest does not equal integrity.digest",
                {
                    "signer": sig.get("signer"),
                    "expected": digest,
                    "actual": sig.get("payload-digest"),
                },
            )


def _project_validate(frontmatter: dict[str, Any], schema: type[ModelT]) -> ModelT:
    try:
        return schema.model_validate(frontmatter)
    except ValidationError as cause:
        raise MdaConfigError(
            ErrorCategory.ProjectSchemaViolation,
            "consumer pydantic schema rejected the frontmatter",
            {"issues": cause.errors()},
        ) from cause


def _coerce_integrity(value: Any) -> IntegrityField | None:
    if not isinstance(value, dict):
        return None
    return {"algorithm": value["algorithm"], "digest": value["digest"]}


def load_mda_source(
    path: str | Path,
    *,
    schema: type[ModelT],
    verify_integrity: bool = False,
    verify_signatures: bool = False,
    enforce_requires: bool = False,
    trusted_runtime: bool = False,
    allowed_networks: list[str] | None = None,
    trust_policy: object | None = None,
    rekor_client: RekorClient | None = None,
    sigstore_verifier: SigstoreVerifier | None = None,
    did_web_verifier: DidWebVerifier | None = None,
) -> ModelT:
    """MDA §11-2 loads a `.mda` source-mode file through Stages A/B/C/D/E/F/G."""
    file_bytes = Path(path).read_bytes()
    return load_mda_source_from_bytes(
        file_bytes,
        schema=schema,
        verify_integrity=verify_integrity,
        verify_signatures=verify_signatures,
        enforce_requires=enforce_requires,
        trusted_runtime=trusted_runtime,
        allowed_networks=allowed_networks,
        trust_policy=trust_policy,
        rekor_client=rekor_client,
        sigstore_verifier=sigstore_verifier,
        did_web_verifier=did_web_verifier,
    )


def load_mda_source_from_bytes(
    file_bytes: bytes,
    *,
    schema: type[ModelT],
    verify_integrity: bool = False,
    verify_signatures: bool = False,
    enforce_requires: bool = False,
    trusted_runtime: bool = False,
    allowed_networks: list[str] | None = None,
    trust_policy: object | None = None,
    rekor_client: RekorClient | None = None,
    sigstore_verifier: SigstoreVerifier | None = None,
    did_web_verifier: DidWebVerifier | None = None,
) -> ModelT:
    """MDA §11-2 loads raw source-mode bytes through Stages A/B/C/D/E/F/G."""
    extracted = extract_frontmatter(file_bytes)
    if extracted.frontmatter_str == "":
        raise MdaConfigError(
            ErrorCategory.MissingRequiredFrontmatter,
            "source-mode .mda file has no opening '---' fence",
        )
    frontmatter = parse_frontmatter_yaml(extracted.frontmatter_str)
    validated_policy = (
        _require_valid_trust_policy(trust_policy)
        if trusted_runtime or verify_signatures
        else None
    )

    _validate_mda_source(frontmatter)
    _cross_field_check(frontmatter)

    integrity = _coerce_integrity(frontmatter.get("integrity"))
    signatures = frontmatter.get("signatures")
    signature_entries = (
        cast("list[SignatureEntry]", signatures) if isinstance(signatures, list) else []
    )

    if trusted_runtime and integrity is None:
        raise MdaConfigError(
            ErrorCategory.MissingRequiredIntegrity,
            "trusted_runtime=True requires integrity",
        )

    if (trusted_runtime or verify_signatures) and not signature_entries:
        raise MdaConfigError(
            ErrorCategory.MissingRequiredSignature
            if trusted_runtime
            else ErrorCategory.SignatureVerificationFailure,
            "signature verification requires a non-empty signatures[] field",
        )

    if verify_integrity or verify_signatures or trusted_runtime:
        if integrity is None:
            raise MdaConfigError(
                ErrorCategory.MissingRequiredIntegrity
                if trusted_runtime
                else ErrorCategory.SchemaViolation,
                "integrity is required when verification is enabled",
            )
        run_integrity_check(frontmatter, extracted.body_str, integrity)

    if verify_signatures or trusted_runtime:
        if integrity is None:
            raise MdaConfigError(
                ErrorCategory.SchemaViolation,
                "integrity is required when signature verification is enabled",
            )
        run_signature_check(
            signature_entries,
            integrity,
            validated_policy,
            rekor_client=rekor_client,
            sigstore_verifier=sigstore_verifier,
            did_web_verifier=did_web_verifier,
        )

    if enforce_requires:
        requires = frontmatter.get("requires")
        run_requires_check(
            cast("dict[str, Any] | None", requires) if isinstance(requires, dict) else None,
            RequiresEnvironment(allowed_networks=allowed_networks or ()),
        )

    return _project_validate(frontmatter, schema)


def _require_valid_trust_policy(input_value: object | None) -> TrustPolicy:
    if not isinstance(input_value, dict) or len(cast("dict[str, object]", input_value)) == 0:
        raise MdaConfigError(
            ErrorCategory.TrustPolicyViolation,
            "trusted-runtime requires a valid trustPolicy",
        )
    return validate_trust_policy(cast("dict[str, object]", input_value))
