"""Canonical source identity and deterministic evidence fingerprints."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from civitas.contracts.evidence import EvidenceIdentity


@dataclass(frozen=True, slots=True)
class CanonicalSourceGroup:
    source_id: str
    source_type: str

    @property
    def key(self) -> str:
        return f"{self.source_type.casefold()}:{self.source_id.casefold()}"


def canonical_source_group(identity: EvidenceIdentity) -> CanonicalSourceGroup:
    """Group endpoints by declared canonical upstream dataset/source."""

    return CanonicalSourceGroup(
        source_id=identity.canonical_source_id.strip(),
        source_type=identity.canonical_source_type.strip(),
    )


def normalized_arguments(arguments: Mapping[str, Any]) -> str:
    """Produce a stable JSON representation for tool-call identity."""

    return json.dumps(arguments, sort_keys=True, separators=(",", ":"), default=str)


def evidence_identity_fingerprint(identity: EvidenceIdentity) -> str:
    """Fingerprint an observation, not merely the endpoint that returned it."""

    payload = {
        "canonical_source": canonical_source_group(identity).key,
        "mcp_server": identity.mcp_server,
        "tool_name": identity.tool_name,
        "arguments": normalized_arguments(identity.normalized_arguments),
        "retrieved_at": identity.retrieved_at.isoformat(),
        "observation_version": identity.observation_version,
        "raw_response_sha256": identity.raw_response_sha256,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()
