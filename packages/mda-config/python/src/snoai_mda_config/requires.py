"""MDA §10-3.3 / §10-4 requires.network enforcement."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from .errors import ErrorCategory, MdaConfigError

_PRIVATE_V4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
)
_PRIVATE_V6_NETWORKS: tuple[ipaddress.IPv6Network, ...] = (
    ipaddress.IPv6Network("fc00::/7"),
)


@dataclass(frozen=True)
class RequiresEnvironment:
    """Operator-supplied environment for §10-4 enforcement."""

    allowed_networks: Sequence[str] = field(default_factory=tuple)


def enforce_requires(requires: dict[str, Any] | None, env: RequiresEnvironment) -> None:
    """MDA §10-4 enforces the top-level requires.network value."""

    if requires is None:
        return
    if "network" not in requires:
        return

    network = requires["network"]
    allowed = set(env.allowed_networks)
    wildcard = "*" in allowed

    if network == "none":
        return

    if network == "local":
        for host in allowed:
            if not _is_local_host(host):
                raise MdaConfigError(
                    ErrorCategory.RequiresNotSatisfied,
                    "requires.network=local but operator permits non-local host",
                    {"key": "network", "host": host},
                )
        return

    if network == "public":
        if wildcard:
            return
        raise MdaConfigError(
            ErrorCategory.RequiresNotSatisfied,
            "requires.network=public but operator does not grant wildcard '*'",
            {"key": "network"},
        )

    if isinstance(network, list):
        network_items = cast("list[Any]", network)
        for host_candidate in network_items:
            if not isinstance(host_candidate, str) or not host_candidate:
                _invalid_network_shape(network)
        network_hosts = cast("list[str]", network_items)
        if wildcard:
            return
        for host in network_hosts:
            if not _is_network_allowed(host, allowed):
                raise MdaConfigError(
                    ErrorCategory.RequiresNotSatisfied,
                    f"requires.network host '{host}' not in operator allow-list",
                    {"key": "network", "host": host, "allowed": sorted(allowed)},
                )
        return

    _invalid_network_shape(network)


def _invalid_network_shape(value: Any) -> None:
    raise MdaConfigError(
        ErrorCategory.RequiresNotSatisfied,
        "requires.network has an invalid shape",
        {"key": "network", "reason": "invalid-shape", "got": value},
    )


def _is_network_allowed(required_host: str, allowed: set[str]) -> bool:
    if required_host in allowed:
        return True
    return any(_host_matches_pattern(required_host, pattern) for pattern in allowed)


def _host_matches_pattern(host: str, pattern: str) -> bool:
    if "*" not in pattern:
        return host == pattern
    escaped = re.escape(pattern).replace(r"\*", "[^.]+")
    return re.fullmatch(escaped, host) is not None


def _is_local_host(host: str) -> bool:
    normalized = host.rstrip(".").lower()
    if (
        normalized == "localhost"
        or normalized.endswith(".localhost")
        or normalized.endswith(".local")
        or normalized.endswith(".internal")
    ):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    if address.is_loopback:
        return True
    if isinstance(address, ipaddress.IPv4Address):
        return any(address in network for network in _PRIVATE_V4_NETWORKS)
    return any(address in network for network in _PRIVATE_V6_NETWORKS)
