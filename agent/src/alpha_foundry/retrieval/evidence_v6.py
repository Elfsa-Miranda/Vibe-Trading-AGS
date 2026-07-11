"""Minimal v6 decision input referencing authoritative control and feature events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_BYTES = 64 * 1024


@dataclass(frozen=True)
class RetrieverDecisionInputV6:
    schema_version: str
    control_evidence_event_hash: str
    control_evidence_hash: str
    feature_source_event_hash: str
    feature_source_hash: str
    seed: int
    candidate_budget: int
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "retriever_decision_input.v6":
            raise ValueError("unsupported Retriever v6 input")
        if any(
            _HASH_RE.fullmatch(value) is None
            for value in (
                self.control_evidence_event_hash,
                self.control_evidence_hash,
                self.feature_source_event_hash,
                self.feature_source_hash,
                self.bundle_hash,
            )
        ):
            raise ValueError("Retriever v6 input identity is invalid")
        if (
            isinstance(self.seed, bool)
            or not isinstance(self.seed, int)
            or isinstance(self.candidate_budget, bool)
            or not isinstance(self.candidate_budget, int)
            or self.candidate_budget < 1
        ):
            raise ValueError("Retriever v6 seed or budget is invalid")
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Retriever v6 input hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        control_evidence_event_hash: str,
        control_evidence_hash: str,
        feature_source_event_hash: str,
        feature_source_hash: str,
        seed: int,
        candidate_budget: int,
    ) -> "RetrieverDecisionInputV6":
        content = {
            "schema_version": "retriever_decision_input.v6",
            "control_evidence_event_hash": control_evidence_event_hash,
            "control_evidence_hash": control_evidence_hash,
            "feature_source_event_hash": feature_source_event_hash,
            "feature_source_hash": feature_source_hash,
            "seed": seed,
            "candidate_budget": candidate_budget,
        }
        return cls(
            schema_version="retriever_decision_input.v6",
            control_evidence_event_hash=control_evidence_event_hash,
            control_evidence_hash=control_evidence_hash,
            feature_source_event_hash=feature_source_event_hash,
            feature_source_hash=feature_source_hash,
            seed=seed,
            candidate_budget=candidate_budget,
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrieverDecisionInputV6":
        expected = {
            "schema_version", "control_evidence_event_hash", "control_evidence_hash",
            "feature_source_event_hash", "feature_source_hash", "seed",
            "candidate_budget", "bundle_hash",
        }
        if set(raw) != expected:
            raise ValueError("Retriever v6 input has an invalid schema")
        return cls(
            schema_version=str(raw["schema_version"]),
            control_evidence_event_hash=str(raw["control_evidence_event_hash"]),
            control_evidence_hash=str(raw["control_evidence_hash"]),
            feature_source_event_hash=str(raw["feature_source_event_hash"]),
            feature_source_hash=str(raw["feature_source_hash"]),
            seed=raw["seed"],
            candidate_budget=raw["candidate_budget"],
            bundle_hash=str(raw["bundle_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "control_evidence_event_hash": self.control_evidence_event_hash,
            "control_evidence_hash": self.control_evidence_hash,
            "feature_source_event_hash": self.feature_source_event_hash,
            "feature_source_hash": self.feature_source_hash,
            "seed": self.seed,
            "candidate_budget": self.candidate_budget,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


class RetrieverDecisionInputArtifactStoreV6:
    media_type = "application/vnd.vibe.retriever-decision-input-v6+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, bundle: RetrieverDecisionInputV6) -> dict[str, str]:
        payload = bundle.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v6 input contains unsafe material")
        relative = self.relative_path(bundle.bundle_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, bundle.bundle_hash) != bundle:
                raise ValueError("Retriever v6 input collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(self, relative_path: str, expected_hash: str) -> RetrieverDecisionInputV6:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ValueError("Retriever v6 input exceeds byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite Retriever v6 value: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate Retriever v6 input key")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("Retriever v6 input must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v6 input contains unsafe material")
        bundle = RetrieverDecisionInputV6.from_dict(payload)
        if bundle.bundle_hash != expected_hash:
            raise ValueError("Retriever v6 input identity differs")
        if self.relative_path(bundle.bundle_hash) != relative_path.replace("\\", "/"):
            raise ValueError("Retriever v6 input path is not content addressed")
        return bundle

    @staticmethod
    def relative_path(bundle_hash: str) -> str:
        if _HASH_RE.fullmatch(bundle_hash) is None:
            raise ValueError("Retriever v6 input hash is invalid")
        digest = bundle_hash.removeprefix("sha256:")
        return f"retriever-decision-input-v6/{digest[:2]}/{digest}.json"


__all__ = ["RetrieverDecisionInputArtifactStoreV6", "RetrieverDecisionInputV6"]
