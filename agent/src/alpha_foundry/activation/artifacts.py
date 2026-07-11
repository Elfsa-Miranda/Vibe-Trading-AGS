"""Content-addressed, strict-JSON artifacts for retriever activation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


ArtifactKind = Literal[
    "plan", "run", "run_source", "resource", "generation_consumption",
    "result", "decision"
]
_HASH_FIELD = {
    "plan": "plan_hash",
    "run": "manifest_hash",
    "run_source": "audit_hash",
    "resource": "evidence_hash",
    "generation_consumption": "evidence_hash",
    "result": "result_hash",
    "decision": "decision_hash",
}


class ActivationArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def put(self, kind: ArtifactKind, payload: Mapping[str, Any]) -> str:
        plain = dict(payload)
        if canonical_json(redact_secrets(plain)) != canonical_json(plain):
            raise ValueError("activation artifacts reject secrets and absolute private paths")
        hash_field = _HASH_FIELD[kind]
        content_hash = str(plain.get(hash_field, ""))
        if canonical_json_hash(plain, exclude_keys=(hash_field,)) != content_hash:
            raise ValueError(f"activation {kind} content hash is invalid")
        relative = self.relative_path(kind, content_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.get(kind, content_hash) != plain:
                raise ValueError("content-addressed activation artifact collision")
            return relative
        safe_artifact_write_json(self.root, relative, plain)
        return relative

    def get(self, kind: ArtifactKind, content_hash: str) -> dict[str, Any]:
        relative = self.relative_path(kind, content_hash)
        target = safe_artifact_path(self.root, relative)
        raw = target.read_text(encoding="utf-8")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite JSON constant is forbidden: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate activation artifact key")
                result[key] = value
            return result

        payload = json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, dict):
            raise ValueError("activation artifact must contain one JSON object")
        hash_field = _HASH_FIELD[kind]
        if payload.get(hash_field) != content_hash:
            raise ValueError("activation artifact filename and identity differ")
        if canonical_json_hash(payload, exclude_keys=(hash_field,)) != content_hash:
            raise ValueError("activation artifact content hash mismatch")
        if canonical_json(redact_secrets(payload)) != canonical_json(payload):
            raise ValueError("activation artifact contains unsafe material")
        return payload

    @staticmethod
    def relative_path(kind: ArtifactKind, content_hash: str) -> str:
        prefix, separator, digest = content_hash.partition(":")
        if prefix != "sha256" or separator != ":" or len(digest) != 64:
            raise ValueError("activation artifact identity must be sha256")
        int(digest, 16)
        return f"{kind}/{digest[:2]}/{digest}.json"


__all__ = ["ActivationArtifactStore", "ArtifactKind"]
