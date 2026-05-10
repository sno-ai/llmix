"""MDA loader option types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class MdaConfigLoadOptions:
    """Options passed through to the MDA source loader."""

    verify_integrity: bool = False
    verify_signatures: bool = False
    trusted_runtime: bool = False
    enforce_requires: bool = False
    allowed_networks: list[str] | None = None
    trust_policy: Any | None = None
    rekor_client: Any | None = None
    sigstore_verifier: Any | None = None
    did_web_verifier: Any | None = None
