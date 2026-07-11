"""Content-addressed v5 inputs binding candidates to frozen action events."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_foundry.retrieval.evidence_v3 import RetrieverInputBundleV3
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_MAX_BUNDLE_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class RetrieverInputBundleV5:
    schema_version: str
    control_evidence_event_hash: str
    control_evidence_hash: str
    action_template_event_hashes: tuple[str, ...]
    retriever_input: RetrieverInputBundleV3
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "retriever_input_bundle.v5":
            raise ValueError("unsupported Retriever v5 input schema")
        hashes = (
            self.control_evidence_event_hash,
            self.control_evidence_hash,
            self.bundle_hash,
            *self.action_template_event_hashes,
        )
        if any(_HASH_RE.fullmatch(value) is None for value in hashes):
            raise ValueError("Retriever v5 source identity is invalid")
        if len(self.action_template_event_hashes) != len(self.retriever_input.candidates):
            raise ValueError("Retriever v5 actions and candidates must align")
        if len(self.action_template_event_hashes) != len(
            set(self.action_template_event_hashes)
        ):
            raise ValueError("Retriever v5 action event hashes must be unique")
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Retriever v5 bundle hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        control_evidence_event_hash: str,
        control_evidence_hash: str,
        action_template_event_hashes: tuple[str, ...],
        retriever_input: RetrieverInputBundleV3,
    ) -> "RetrieverInputBundleV5":
        content = {
            "schema_version": "retriever_input_bundle.v5",
            "control_evidence_event_hash": control_evidence_event_hash,
            "control_evidence_hash": control_evidence_hash,
            "action_template_event_hashes": list(action_template_event_hashes),
            "retriever_input": retriever_input.to_dict(),
        }
        return cls(
            schema_version="retriever_input_bundle.v5",
            control_evidence_event_hash=control_evidence_event_hash,
            control_evidence_hash=control_evidence_hash,
            action_template_event_hashes=action_template_event_hashes,
            retriever_input=retriever_input,
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrieverInputBundleV5":
        expected = {
            "schema_version", "control_evidence_event_hash", "control_evidence_hash",
            "action_template_event_hashes", "retriever_input", "bundle_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["action_template_event_hashes"], list)
            or not isinstance(raw["retriever_input"], Mapping)
        ):
            raise ValueError("Retriever v5 input has an invalid closed schema")
        return cls(
            schema_version=str(raw["schema_version"]),
            control_evidence_event_hash=str(raw["control_evidence_event_hash"]),
            control_evidence_hash=str(raw["control_evidence_hash"]),
            action_template_event_hashes=tuple(
                str(value) for value in raw["action_template_event_hashes"]
            ),
            retriever_input=RetrieverInputBundleV3.from_dict(raw["retriever_input"]),
            bundle_hash=str(raw["bundle_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "control_evidence_event_hash": self.control_evidence_event_hash,
            "control_evidence_hash": self.control_evidence_hash,
            "action_template_event_hashes": list(self.action_template_event_hashes),
            "retriever_input": self.retriever_input.to_dict(),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


class RetrieverInputArtifactStoreV5:
    media_type = "application/vnd.vibe.retriever-input-bundle-v5+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, bundle: RetrieverInputBundleV5) -> dict[str, str]:
        payload = bundle.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v5 input contains unsafe material")
        if len(canonical_json(payload).encode("utf-8")) > _MAX_BUNDLE_BYTES:
            raise ValueError("Retriever v5 input exceeds byte budget")
        relative = self.relative_path(bundle.bundle_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, bundle.bundle_hash) != bundle:
                raise ValueError("Retriever v5 input collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(self, relative_path: str, expected_bundle_hash: str) -> RetrieverInputBundleV5:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_BUNDLE_BYTES:
            raise ValueError("Retriever v5 input exceeds byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite Retriever v5 value: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate Retriever v5 key")
                result[key] = value
            return result

        payload = json.loads(
            raw.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("Retriever v5 input must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever v5 input contains unsafe material")
        bundle = RetrieverInputBundleV5.from_dict(payload)
        if bundle.bundle_hash != expected_bundle_hash:
            raise ValueError("Retriever v5 input identity mismatch")
        if self.relative_path(bundle.bundle_hash) != relative_path.replace("\\", "/"):
            raise ValueError("Retriever v5 input path is not content addressed")
        return bundle

    @staticmethod
    def relative_path(bundle_hash: str) -> str:
        if _HASH_RE.fullmatch(bundle_hash) is None:
            raise ValueError("Retriever v5 bundle hash is invalid")
        digest = bundle_hash.removeprefix("sha256:")
        return f"retriever-input-v5/{digest[:2]}/{digest}.json"


__all__ = ["RetrieverInputArtifactStoreV5", "RetrieverInputBundleV5"]
