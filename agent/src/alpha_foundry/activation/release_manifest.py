"""Deterministic release index for local AGS evidence.

This module is deliberately a projection, not a new source of research truth.
It can index legacy/preflight artifacts, but it never upgrades an artifact that
is not bound to a verified research event into authoritative evidence.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
from src.alpha_foundry.artifacts import safe_artifact_write_json
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.research_ledger.hash_utils import canonical_json_hash


_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_ARTIFACT_KINDS = ("plan", "result", "decision")
_SCHEMAS = {
    "plan": "activation_experiment_plan.v1",
    "result": "activation_experiment_result.v1",
    "decision": "retriever_activation_decision.v1",
}


def _blob_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _normalized_text_hash(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="strict")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return "sha256:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _relative_to(path: Path, root: Path) -> str:
    try:
        return path.resolve(strict=True).relative_to(root).as_posix()
    except ValueError as exc:
        raise ValueError("release evidence path escapes repository root") from exc


@dataclass(frozen=True)
class ReleaseArtifactEntry:
    kind: Literal["plan", "result", "decision"]
    schema_version: str
    semantic_hash: str
    relative_path: str
    blob_hash: str
    authority_status: Literal["legacy_unverified"]
    lifecycle_status: str
    superseded: bool
    superseded_reason: str | None
    source_event_hash: None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "schema_version": self.schema_version,
            "semantic_hash": self.semantic_hash,
            "relative_path": self.relative_path,
            "blob_hash": self.blob_hash,
            "authority_status": self.authority_status,
            "lifecycle_status": self.lifecycle_status,
            "superseded": self.superseded,
            "superseded_reason": self.superseded_reason,
            "source_event_hash": self.source_event_hash,
        }


@dataclass(frozen=True)
class LegacyClaimDocument:
    relative_path: str
    normalized_content_hash: str
    authority_status: Literal["historical_claim_unverified"] = (
        "historical_claim_unverified"
    )

    def to_dict(self) -> dict[str, str]:
        return {
            "relative_path": self.relative_path,
            "normalized_content_hash": self.normalized_content_hash,
            "authority_status": self.authority_status,
        }


@dataclass(frozen=True)
class AuditInputRecord:
    document_name: Literal["problem.md"]
    blob_hash: str
    document_worktree_commit: str
    tracked_in_document_worktree: Literal[False] = False
    authority_status: Literal["audit_input_not_code_truth"] = (
        "audit_input_not_code_truth"
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "blob_hash": self.blob_hash,
            "document_worktree_commit": self.document_worktree_commit,
            "tracked_in_document_worktree": self.tracked_in_document_worktree,
            "authority_status": self.authority_status,
        }


@dataclass(frozen=True)
class ReleaseManifestV1:
    accepted_code_commit: str
    audit_input: AuditInputRecord
    current_retriever_policy_hash: str
    artifacts: tuple[ReleaseArtifactEntry, ...]
    legacy_claim_documents: tuple[LegacyClaimDocument, ...]
    activation_status: Literal["preflight_invalidated", "legacy_unverified_claim"]
    engineering_status: Literal["implementation_present_evidence_not_event_bound"]
    empirical_status: Literal["not_established"]
    limitations: tuple[str, ...]
    manifest_hash: str
    schema_version: Literal["ags_release_manifest.v1"] = "ags_release_manifest.v1"

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "accepted_code_commit": self.accepted_code_commit,
            "audit_input": self.audit_input.to_dict(),
            "current_retriever_policy_hash": self.current_retriever_policy_hash,
            "artifacts": [entry.to_dict() for entry in self.artifacts],
            "legacy_claim_documents": [
                document.to_dict() for document in self.legacy_claim_documents
            ],
            "activation_status": self.activation_status,
            "engineering_status": self.engineering_status,
            "empirical_status": self.empirical_status,
            "verification_records": [],
            "limitations": list(self.limitations),
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "manifest_hash": self.manifest_hash}


class ReleaseManifestBuilderV1:
    """Rebuild an honest index from legacy local files.

    This M0 builder intentionally has no parameter for a verdict, empirical
    status, warning, cap, source event, or test result. Those fields are derived
    from validated artifacts and the absence of event/test bindings.
    """

    def __init__(
        self,
        repository_root: str | Path,
        *,
        accepted_code_commit: str,
        audit_document: str | Path,
        audit_document_worktree_commit: str,
    ) -> None:
        self._repository_root = Path(repository_root).resolve(strict=True)
        if _COMMIT_RE.fullmatch(accepted_code_commit) is None:
            raise ValueError("accepted_code_commit must be a full lowercase git commit")
        if _COMMIT_RE.fullmatch(audit_document_worktree_commit) is None:
            raise ValueError(
                "audit_document_worktree_commit must be a full lowercase git commit"
            )
        self._accepted_code_commit = accepted_code_commit
        audit_path = Path(audit_document).resolve(strict=True)
        if audit_path.name != "problem.md" or not audit_path.is_file():
            raise ValueError("audit_document must identify the problem.md audit input")
        self._audit_input = AuditInputRecord(
            document_name="problem.md",
            blob_hash=_blob_hash(audit_path),
            document_worktree_commit=audit_document_worktree_commit,
        )
        self._activation_root = (
            self._repository_root / "agent" / "research_evidence" / "activation"
        ).resolve(strict=True)
        _relative_to(self._activation_root, self._repository_root)
        self._store = ActivationArtifactStore(self._activation_root)

    def build(self) -> ReleaseManifestV1:
        entries, payloads = self._read_artifacts()
        self._validate_relationships(payloads)
        current_policy_hash = ActivationRetrieverPolicy().policy_hash
        limitations = {
            "ACTIVATION_ARTIFACTS_HAVE_NO_TRACKED_SOURCE_EVENT_BINDING",
            "CLOSED_TEST_RUN_RECORDS_UNAVAILABLE",
            "ENGINEERING_TEST_CLAIMS_NOT_REPLAYED_BY_RELEASE_INDEX",
            "FORMAL_ACTIVATION_OUTCOMES_NOT_OPENED",
            "MARKET_EMPIRICAL_EVIDENCE_NOT_ESTABLISHED",
        }
        plan = payloads["plan"][0]
        policy_superseded = (
            plan["provenance"]["treatment_policy_hash"] != current_policy_hash
        )
        if policy_superseded:
            limitations.add("TREATMENT_POLICY_HASH_SUPERSEDED")
            entries = tuple(
                ReleaseArtifactEntry(
                    kind=entry.kind,
                    schema_version=entry.schema_version,
                    semantic_hash=entry.semantic_hash,
                    relative_path=entry.relative_path,
                    blob_hash=entry.blob_hash,
                    authority_status=entry.authority_status,
                    lifecycle_status=entry.lifecycle_status,
                    superseded=True,
                    superseded_reason="TREATMENT_POLICY_HASH_SUPERSEDED",
                )
                for entry in entries
            )
        activation_status = self._activation_status(payloads)
        documents = self._read_legacy_documents()
        provisional = ReleaseManifestV1(
            accepted_code_commit=self._accepted_code_commit,
            audit_input=self._audit_input,
            current_retriever_policy_hash=current_policy_hash,
            artifacts=entries,
            legacy_claim_documents=documents,
            activation_status=activation_status,
            engineering_status="implementation_present_evidence_not_event_bound",
            empirical_status="not_established",
            limitations=tuple(sorted(limitations)),
            manifest_hash="sha256:" + "0" * 64,
        )
        manifest_hash = canonical_json_hash(provisional._content_dict())
        return ReleaseManifestV1(
            accepted_code_commit=provisional.accepted_code_commit,
            audit_input=provisional.audit_input,
            current_retriever_policy_hash=provisional.current_retriever_policy_hash,
            artifacts=provisional.artifacts,
            legacy_claim_documents=provisional.legacy_claim_documents,
            activation_status=provisional.activation_status,
            engineering_status=provisional.engineering_status,
            empirical_status=provisional.empirical_status,
            limitations=provisional.limitations,
            manifest_hash=manifest_hash,
        )

    def write(self, relative_path: str = "agent/research_evidence/release_manifest.json") -> Path:
        manifest = self.build()
        return safe_artifact_write_json(
            self._repository_root,
            relative_path,
            manifest.to_dict(),
        )

    def _read_artifacts(
        self,
    ) -> tuple[tuple[ReleaseArtifactEntry, ...], dict[str, list[dict[str, Any]]]]:
        entries: list[ReleaseArtifactEntry] = []
        payloads: dict[str, list[dict[str, Any]]] = {
            kind: [] for kind in _ARTIFACT_KINDS
        }
        for kind in _ARTIFACT_KINDS:
            directory = self._activation_root / kind
            files = sorted(directory.glob("*/*.json")) if directory.exists() else []
            if not files:
                raise ValueError(f"release evidence is missing the legacy {kind} artifact")
            for path in files:
                semantic_hash = "sha256:" + path.stem
                if _HASH_RE.fullmatch(semantic_hash) is None:
                    raise ValueError("release artifact filename is not a canonical hash")
                payload = self._store.get(kind, semantic_hash)  # type: ignore[arg-type]
                if payload.get("schema_version") != _SCHEMAS[kind]:
                    raise ValueError(f"unexpected legacy activation {kind} schema")
                lifecycle_status = self._lifecycle_status(kind, payload)
                entries.append(
                    ReleaseArtifactEntry(
                        kind=kind,  # type: ignore[arg-type]
                        schema_version=_SCHEMAS[kind],
                        semantic_hash=semantic_hash,
                        relative_path=_relative_to(path, self._repository_root),
                        blob_hash=_blob_hash(path),
                        authority_status="legacy_unverified",
                        lifecycle_status=lifecycle_status,
                        superseded=False,
                        superseded_reason=None,
                    )
                )
                payloads[kind].append(payload)
        entries.sort(key=lambda item: (item.kind, item.semantic_hash))
        return tuple(entries), payloads

    @staticmethod
    def _validate_relationships(payloads: dict[str, list[dict[str, Any]]]) -> None:
        plans = {payload["plan_hash"] for payload in payloads["plan"]}
        results = {payload["result_hash"]: payload for payload in payloads["result"]}
        for result in results.values():
            if result.get("plan_hash") not in plans:
                raise ValueError("activation result is not bound to an indexed plan")
        for decision in payloads["decision"]:
            matched_result = results.get(decision.get("result_hash"))
            if (
                matched_result is None
                or decision.get("plan_hash") != matched_result.get("plan_hash")
            ):
                raise ValueError("activation decision is not bound to its plan and result")

    @staticmethod
    def _lifecycle_status(kind: str, payload: dict[str, Any]) -> str:
        if kind == "plan":
            limitations = set(payload.get("limitations", []))
            return "preflight_only" if "PREFLIGHT_ONLY" in limitations else "legacy_plan"
        if kind == "result":
            if not payload.get("replayable") and payload.get("effective_sample") == 0:
                return "preflight_no_outcome"
            return "legacy_unverified_result"
        verdict = payload.get("verdict")
        active = payload.get("active_research_only")
        if verdict == "invalidated" and active is False:
            return "preflight_invalidated"
        return "legacy_unverified_claim"

    @staticmethod
    def _activation_status(
        payloads: dict[str, list[dict[str, Any]]],
    ) -> Literal["preflight_invalidated", "legacy_unverified_claim"]:
        decisions = payloads["decision"]
        if decisions and all(
            decision.get("verdict") == "invalidated"
            and decision.get("active_research_only") is False
            for decision in decisions
        ):
            return "preflight_invalidated"
        return "legacy_unverified_claim"

    def _read_legacy_documents(self) -> tuple[LegacyClaimDocument, ...]:
        documents: list[LegacyClaimDocument] = []
        for relative in (
            "docs/alpha-genesis-final-acceptance.md",
            "docs/alpha-genesis-known-limitations.md",
        ):
            path = (self._repository_root / relative).resolve(strict=True)
            _relative_to(path, self._repository_root)
            documents.append(
                LegacyClaimDocument(
                    relative_path=relative,
                    normalized_content_hash=_normalized_text_hash(path),
                )
            )
        return tuple(documents)


__all__ = [
    "AuditInputRecord",
    "LegacyClaimDocument",
    "ReleaseArtifactEntry",
    "ReleaseManifestBuilderV1",
    "ReleaseManifestV1",
]
