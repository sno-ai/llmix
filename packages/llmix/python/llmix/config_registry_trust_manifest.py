"""LLMix deployment trust manifest helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Any

from llmix.config_registry_common import (
    _compare_revision,
    _normalize_sha256_digest,
    _read_json_file,
)
from llmix.config_registry_types import (
    RegistryRootHighWatermark,
    RegistryRootVerificationOptions,
)
from llmix.types import InvalidConfigError

LLMIX_TRUST_MANIFEST_KIND = "llmix-trust-manifest"
LLMIX_TRUST_MANIFEST_VERSION = 1
_DIGEST_PATTERN = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(frozen=True)
class LlmixTrustManifestRegistryRoot:
    path: str
    revision: str
    published_at: str
    high_watermark: str


@dataclass(frozen=True)
class LlmixTrustManifestReleasePlan:
    path: str
    source_count: int


@dataclass(frozen=True)
class LlmixTrustManifest:
    version: int
    kind: str
    expected_root_digest: str
    source_set_digest: str
    release_plan_digest: str
    registry_root_trust_policy: dict[str, Any]
    rekor_policy: dict[str, Any] | None
    minimum_revision: str | None
    minimum_published_at: str | None
    high_watermark: str | None
    registry_root_signer_identity: Any
    registry_root: LlmixTrustManifestRegistryRoot
    release_plan: LlmixTrustManifestReleasePlan


def load_llmix_trust_manifest(path: str | Path) -> LlmixTrustManifest:
    return parse_llmix_trust_manifest(_read_json_file(Path(path)), str(path))


def parse_llmix_trust_manifest(
    value: Any, source_path: str = "LLMix trust manifest"
) -> LlmixTrustManifest:
    manifest = _ensure_object(value, source_path)
    if manifest.get("kind") != LLMIX_TRUST_MANIFEST_KIND:
        raise InvalidConfigError(f"Invalid LLMix trust manifest kind in {source_path}")
    if manifest.get("version") != LLMIX_TRUST_MANIFEST_VERSION:
        raise InvalidConfigError(
            f"Invalid LLMix trust manifest version in {source_path}"
        )
    return LlmixTrustManifest(
        version=LLMIX_TRUST_MANIFEST_VERSION,
        kind=LLMIX_TRUST_MANIFEST_KIND,
        expected_root_digest=_ensure_digest(
            manifest, "expectedRootDigest", source_path
        ),
        source_set_digest=_ensure_digest(manifest, "sourceSetDigest", source_path),
        release_plan_digest=_ensure_digest(
            manifest, "releasePlanDigest", source_path
        ),
        registry_root_trust_policy=_ensure_object(
            manifest.get("registryRootTrustPolicy"),
            f"{source_path}.registryRootTrustPolicy",
        ),
        rekor_policy=_ensure_nullable_object(
            manifest.get("rekorPolicy"), f"{source_path}.rekorPolicy"
        ),
        minimum_revision=_ensure_nullable_string(
            manifest.get("minimumRevision"), f"{source_path}.minimumRevision"
        ),
        minimum_published_at=_ensure_nullable_string(
            manifest.get("minimumPublishedAt"), f"{source_path}.minimumPublishedAt"
        ),
        high_watermark=_ensure_nullable_string(
            manifest.get("highWatermark"), f"{source_path}.highWatermark"
        ),
        registry_root_signer_identity=_ensure_present(
            manifest, "registryRootSignerIdentity", source_path
        ),
        registry_root=_parse_registry_root(
            manifest.get("registryRoot"), f"{source_path}.registryRoot"
        ),
        release_plan=_parse_release_plan(
            manifest.get("releasePlan"), f"{source_path}.releasePlan"
        ),
    )


def registry_root_options_from_trust_manifest(
    manifest: LlmixTrustManifest,
    *,
    rekor_client: Any | None = None,
    sigstore_verifier: Any | None = None,
    did_web_verifier: Any | None = None,
    high_watermark: RegistryRootHighWatermark | None = None,
) -> RegistryRootVerificationOptions:
    minimum_revision = _minimum_revision_from_manifest(manifest)
    return RegistryRootVerificationOptions(
        trust_policy=manifest.registry_root_trust_policy,
        rekor_client=rekor_client,
        sigstore_verifier=sigstore_verifier,
        did_web_verifier=did_web_verifier,
        expected_revision=manifest.registry_root.revision,
        expected_root_digest=_normalize_sha256_digest(
            manifest.expected_root_digest,
            "LLMix trust manifest expectedRootDigest",
        ),
        minimum_revision=minimum_revision,
        minimum_published_at=manifest.minimum_published_at,
        high_watermark=high_watermark,
    )


def _minimum_revision_from_manifest(manifest: LlmixTrustManifest) -> str | None:
    if manifest.high_watermark is None:
        return manifest.minimum_revision
    if manifest.minimum_revision is None:
        return manifest.high_watermark
    if _compare_revision(manifest.minimum_revision, manifest.high_watermark) >= 0:
        return manifest.minimum_revision
    return manifest.high_watermark


def _parse_registry_root(value: Any, source_path: str) -> LlmixTrustManifestRegistryRoot:
    root = _ensure_object(value, source_path)
    published_at = _ensure_non_empty_string(
        root.get("publishedAt"), f"{source_path}.publishedAt"
    )
    try:
        datetime.fromisoformat(published_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise InvalidConfigError(
            f"Invalid ISO timestamp for {source_path}.publishedAt"
        ) from error
    return LlmixTrustManifestRegistryRoot(
        path=_ensure_non_empty_string(root.get("path"), f"{source_path}.path"),
        revision=_ensure_non_empty_string(
            root.get("revision"), f"{source_path}.revision"
        ),
        published_at=published_at,
        high_watermark=_ensure_non_empty_string(
            root.get("highWatermark"), f"{source_path}.highWatermark"
        ),
    )


def _parse_release_plan(value: Any, source_path: str) -> LlmixTrustManifestReleasePlan:
    release_plan = _ensure_object(value, source_path)
    source_count = release_plan.get("sourceCount")
    if not isinstance(source_count, int) or isinstance(source_count, bool) or source_count < 0:
        raise InvalidConfigError(
            f"Invalid non-negative integer for {source_path}.sourceCount"
        )
    return LlmixTrustManifestReleasePlan(
        path=_ensure_non_empty_string(release_plan.get("path"), f"{source_path}.path"),
        source_count=source_count,
    )


def _ensure_digest(value: dict[str, Any], field: str, source_path: str) -> str:
    digest = value.get(field)
    if not isinstance(digest, str) or not _DIGEST_PATTERN.match(digest):
        raise InvalidConfigError(f"Invalid digest for {source_path}.{field}")
    return digest


def _ensure_object(value: Any, source_path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise InvalidConfigError(f"{source_path} must be a JSON object")
    return value


def _ensure_present(value: dict[str, Any], field: str, source_path: str) -> Any:
    if field not in value:
        raise InvalidConfigError(f"{source_path}.{field} must be present")
    return value[field]


def _ensure_nullable_object(value: Any, source_path: str) -> dict[str, Any] | None:
    if value is None:
        return None
    return _ensure_object(value, source_path)


def _ensure_nullable_string(value: Any, source_path: str) -> str | None:
    if value is None:
        return None
    return _ensure_non_empty_string(value, source_path)


def _ensure_non_empty_string(value: Any, source_path: str) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidConfigError(f"{source_path} must be a non-empty string")
    return value
