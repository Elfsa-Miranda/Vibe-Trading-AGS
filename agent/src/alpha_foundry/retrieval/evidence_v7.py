"""Minimal v7 input binding authoritative feature and pre-arm schedule events."""

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
class RetrieverDecisionInputV7:
    schema_version: str
    schedule_event_hash: str
    schedule_hash: str
    feature_source_event_hash: str
    feature_source_hash: str
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "retriever_decision_input.v7":
            raise ValueError("unsupported Retriever v7 input")
        if any(
            _HASH_RE.fullmatch(value) is None
            for value in (
                self.schedule_event_hash, self.schedule_hash,
                self.feature_source_event_hash, self.feature_source_hash,
                self.bundle_hash,
            )
        ):
            raise ValueError("Retriever v7 input identity is invalid")
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Retriever v7 input hash mismatch")

    @classmethod
    def build(
        cls, *, schedule_event_hash: str, schedule_hash: str,
        feature_source_event_hash: str, feature_source_hash: str,
    ) -> "RetrieverDecisionInputV7":
        content = {
            "schema_version": "retriever_decision_input.v7",
            "schedule_event_hash": schedule_event_hash,
            "schedule_hash": schedule_hash,
            "feature_source_event_hash": feature_source_event_hash,
            "feature_source_hash": feature_source_hash,
        }
        return cls(
            schema_version="retriever_decision_input.v7",
            schedule_event_hash=schedule_event_hash,
            schedule_hash=schedule_hash,
            feature_source_event_hash=feature_source_event_hash,
            feature_source_hash=feature_source_hash,
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrieverDecisionInputV7":
        expected = {
            "schema_version", "schedule_event_hash", "schedule_hash",
            "feature_source_event_hash", "feature_source_hash", "bundle_hash",
        }
        if set(raw) != expected:
            raise ValueError("Retriever v7 input has an invalid schema")
        return cls(*(str(raw[name]) for name in (
            "schema_version", "schedule_event_hash", "schedule_hash",
            "feature_source_event_hash", "feature_source_hash", "bundle_hash",
        )))

    def _content_dict(self) -> dict[str, str]:
        return {
            "schema_version": self.schema_version,
            "schedule_event_hash": self.schedule_event_hash,
            "schedule_hash": self.schedule_hash,
            "feature_source_event_hash": self.feature_source_event_hash,
            "feature_source_hash": self.feature_source_hash,
        }

    def to_dict(self) -> dict[str, str]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


class RetrieverDecisionInputArtifactStoreV7:
    media_type = "application/vnd.vibe.retriever-decision-input-v7+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, bundle: RetrieverDecisionInputV7) -> dict[str, str]:
        payload = bundle.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v7 input contains unsafe material")
        relative = self.relative_path(bundle.bundle_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, bundle.bundle_hash) != bundle:
                raise ValueError("Retriever v7 input collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(self, relative_path: str, expected_hash: str) -> RetrieverDecisionInputV7:
        raw = safe_artifact_path(self.root, relative_path).read_bytes()
        if len(raw) > _MAX_BYTES:
            raise ValueError("Retriever v7 input exceeds byte budget")
        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite Retriever v7 value: {value}")
        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate Retriever v7 input key")
                result[key] = value
            return result
        payload = json.loads(
            raw.decode("utf-8"), parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("Retriever v7 input must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v7 input contains unsafe material")
        bundle = RetrieverDecisionInputV7.from_dict(payload)
        if bundle.bundle_hash != expected_hash:
            raise ValueError("Retriever v7 input identity differs")
        if self.relative_path(expected_hash) != relative_path.replace("\\", "/"):
            raise ValueError("Retriever v7 input path is not content addressed")
        return bundle

    @staticmethod
    def relative_path(bundle_hash: str) -> str:
        if _HASH_RE.fullmatch(bundle_hash) is None:
            raise ValueError("Retriever v7 input hash is invalid")
        digest = bundle_hash.removeprefix("sha256:")
        return f"retriever-decision-input-v7/{digest[:2]}/{digest}.json"


__all__ = ["RetrieverDecisionInputArtifactStoreV7", "RetrieverDecisionInputV7"]
