"""Content-addressed source inputs for independently replayable retriever v3 decisions."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_foundry.dsl.canonical import thaw_canonical_ast
from src.alpha_foundry.retrieval.model import (
    FactorOutputPanel,
    OutputPoint,
    RetrievalCandidate,
    SemanticEmbeddingEvidence,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_MAX_BUNDLE_CANDIDATES = 256
_MAX_BUNDLE_POINTS = 65_536
_MAX_BUNDLE_BYTES = 16 * 1024 * 1024


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _panel_to_dict(panel: FactorOutputPanel) -> dict[str, Any]:
    return {
        "factor_spec_id": panel.factor_spec_id,
        "data_snapshot_hash": panel.data_snapshot_hash,
        "data_scope": panel.data_scope,
        "points": [
            {
                "date": point.date,
                "symbol": point.symbol,
                "value": point.value,
                "valid": point.valid,
            }
            for point in panel.points
        ],
        "panel_hash": panel.panel_hash,
    }


def _panel_from_dict(raw: Mapping[str, Any]) -> FactorOutputPanel:
    expected = {
        "factor_spec_id", "data_snapshot_hash", "data_scope", "points", "panel_hash",
    }
    if set(raw) != expected or not isinstance(raw["points"], list):
        raise ValueError("retriever panel artifact has an invalid closed schema")
    points_list: list[OutputPoint] = []
    for item in raw["points"]:
        if (
            not isinstance(item, Mapping)
            or set(item) != {"date", "symbol", "value", "valid"}
            or not isinstance(item["date"], str)
            or not isinstance(item["symbol"], str)
            or isinstance(item["value"], bool)
            or not isinstance(item["value"], (int, float))
            or not isinstance(item["valid"], bool)
        ):
            raise ValueError("retriever panel contains an invalid point")
        points_list.append(
            OutputPoint(
                date=item["date"],
                symbol=item["symbol"],
                value=float(item["value"]),
                valid=item["valid"],
            )
        )
    points = tuple(points_list)
    return FactorOutputPanel(
        factor_spec_id=str(raw["factor_spec_id"]),
        data_snapshot_hash=str(raw["data_snapshot_hash"]),
        data_scope=str(raw["data_scope"]),
        points=points,
        panel_hash=str(raw["panel_hash"]),
    )


def _semantic_to_dict(evidence: SemanticEmbeddingEvidence) -> dict[str, Any]:
    return {
        "model_id": evidence.model_id,
        "model_version": evidence.model_version,
        "candidate_vector": (
            None if evidence.candidate_vector is None else list(evidence.candidate_vector)
        ),
        "reference_vectors": [list(vector) for vector in evidence.reference_vectors],
        "embedding_hash": evidence.embedding_hash,
        "missing_reason": evidence.missing_reason,
    }


def _semantic_from_dict(raw: Mapping[str, Any]) -> SemanticEmbeddingEvidence:
    expected = {
        "model_id", "model_version", "candidate_vector", "reference_vectors",
        "embedding_hash", "missing_reason",
    }
    if set(raw) != expected or not isinstance(raw["reference_vectors"], list):
        raise ValueError("retriever semantic artifact has an invalid closed schema")
    candidate = raw["candidate_vector"]
    if candidate is not None and not isinstance(candidate, list):
        raise ValueError("retriever semantic candidate vector is invalid")
    if candidate is not None and any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in candidate
    ):
        raise ValueError("retriever semantic candidate vector is invalid")
    if any(
        not isinstance(vector, list)
        or any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in vector
        )
        for vector in raw["reference_vectors"]
    ):
        raise ValueError("retriever semantic reference vector is invalid")
    references = tuple(
        tuple(float(value) for value in vector)
        for vector in raw["reference_vectors"]
        if isinstance(vector, list)
    )
    if len(references) != len(raw["reference_vectors"]):
        raise ValueError("retriever semantic reference vector is invalid")
    return SemanticEmbeddingEvidence(
        model_id=str(raw["model_id"]),
        model_version=str(raw["model_version"]),
        candidate_vector=(
            None if candidate is None else tuple(float(value) for value in candidate)
        ),
        reference_vectors=references,
        embedding_hash=str(raw["embedding_hash"]),
        missing_reason=(
            None if raw["missing_reason"] is None else str(raw["missing_reason"])
        ),
    )


def _candidate_to_dict(candidate: RetrievalCandidate) -> dict[str, Any]:
    return {
        "factor_spec_id": candidate.factor_spec_id,
        "action_id": candidate.action_id,
        "motif": candidate.motif,
        "parent_context_hash": candidate.parent_context_hash,
        "base_ledger_score": candidate.base_ledger_score,
        "output_panel": _panel_to_dict(candidate.output_panel),
        "reference_panels": [_panel_to_dict(panel) for panel in candidate.reference_panels],
        "canonical_ast": thaw_canonical_ast(candidate.canonical_ast),
        "reference_asts": [thaw_canonical_ast(ast) for ast in candidate.reference_asts],
        "semantic": _semantic_to_dict(candidate.semantic),
        "estimated_cost": candidate.estimated_cost,
        "cost_evidence_hash": candidate.cost_evidence_hash,
    }


def _candidate_from_dict(raw: Mapping[str, Any]) -> RetrievalCandidate:
    expected = {
        "factor_spec_id", "action_id", "motif", "parent_context_hash",
        "base_ledger_score", "output_panel", "reference_panels", "canonical_ast",
        "reference_asts", "semantic", "estimated_cost", "cost_evidence_hash",
    }
    if set(raw) != expected:
        raise ValueError("retriever candidate artifact has an invalid closed schema")
    if not all(
        isinstance(raw[name], expected_type)
        for name, expected_type in (
            ("output_panel", Mapping),
            ("reference_panels", list),
            ("canonical_ast", Mapping),
            ("reference_asts", list),
            ("semantic", Mapping),
        )
    ):
        raise ValueError("retriever candidate artifact contains invalid nested evidence")
    if any(
        isinstance(raw[name], bool) or not isinstance(raw[name], (int, float))
        for name in ("base_ledger_score", "estimated_cost")
    ):
        raise ValueError("retriever candidate score or cost is invalid")
    reference_panels = tuple(
        _panel_from_dict(item)
        for item in raw["reference_panels"]
        if isinstance(item, Mapping)
    )
    reference_asts = tuple(
        dict(item) for item in raw["reference_asts"] if isinstance(item, Mapping)
    )
    if (
        len(reference_panels) != len(raw["reference_panels"])
        or len(reference_asts) != len(raw["reference_asts"])
    ):
        raise ValueError("retriever candidate contains an invalid reference")
    return RetrievalCandidate(
        factor_spec_id=str(raw["factor_spec_id"]),
        action_id=str(raw["action_id"]),
        motif=str(raw["motif"]),
        parent_context_hash=str(raw["parent_context_hash"]),
        base_ledger_score=float(raw["base_ledger_score"]),
        output_panel=_panel_from_dict(raw["output_panel"]),
        reference_panels=reference_panels,
        canonical_ast=dict(raw["canonical_ast"]),
        reference_asts=reference_asts,
        semantic=_semantic_from_dict(raw["semantic"]),
        estimated_cost=float(raw["estimated_cost"]),
        cost_evidence_hash=str(raw["cost_evidence_hash"]),
    )


@dataclass(frozen=True)
class RetrieverInputBundleV3:
    schema_version: str
    official_candidate_ids: tuple[str, ...]
    eligible_event_watermark: str
    data_snapshot_hash: str
    seed: int
    candidate_budget: int
    policy_config: Mapping[str, Any]
    candidates: tuple[RetrievalCandidate, ...]
    bundle_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "retriever_input_bundle.v3":
            raise ValueError("unsupported retriever input bundle schema")
        if not self.candidates or len(self.candidates) > _MAX_BUNDLE_CANDIDATES:
            raise ValueError("retriever input candidate count is out of bounds")
        if self.candidate_budget < 1 or self.candidate_budget > len(self.candidates):
            raise ValueError("retriever input candidate budget is invalid")
        if len(self.official_candidate_ids) != len(set(self.official_candidate_ids)):
            raise ValueError("official candidate IDs must be unique")
        points = sum(
            len(candidate.output_panel.points)
            + sum(len(panel.points) for panel in candidate.reference_panels)
            for candidate in self.candidates
        )
        if points > _MAX_BUNDLE_POINTS:
            raise ValueError(
                "retriever input exceeds bounded JSON evidence; use a large-panel adapter"
            )
        policy = ActivationRetrieverPolicy(**dict(self.policy_config))
        object.__setattr__(self, "policy_config", _freeze(policy.to_dict()))
        if self.bundle_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("retriever input bundle hash mismatch")

    @classmethod
    def build(
        cls,
        *,
        official_candidate_ids: tuple[str, ...],
        eligible_event_watermark: str,
        data_snapshot_hash: str,
        seed: int,
        candidate_budget: int,
        policy: ActivationRetrieverPolicy,
        candidates: tuple[RetrievalCandidate, ...],
    ) -> "RetrieverInputBundleV3":
        content = {
            "schema_version": "retriever_input_bundle.v3",
            "official_candidate_ids": list(official_candidate_ids),
            "eligible_event_watermark": eligible_event_watermark,
            "data_snapshot_hash": data_snapshot_hash,
            "seed": seed,
            "candidate_budget": candidate_budget,
            "policy_config": policy.to_dict(),
            "candidates": [_candidate_to_dict(candidate) for candidate in candidates],
        }
        return cls(
            schema_version="retriever_input_bundle.v3",
            official_candidate_ids=official_candidate_ids,
            eligible_event_watermark=eligible_event_watermark,
            data_snapshot_hash=data_snapshot_hash,
            seed=seed,
            candidate_budget=candidate_budget,
            policy_config=policy.to_dict(),
            candidates=candidates,
            bundle_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrieverInputBundleV3":
        expected = {
            "schema_version", "official_candidate_ids", "eligible_event_watermark",
            "data_snapshot_hash", "seed", "candidate_budget", "policy_config",
            "candidates", "bundle_hash",
        }
        if set(raw) != expected:
            raise ValueError("retriever input bundle has an invalid closed schema")
        if (
            not isinstance(raw["official_candidate_ids"], list)
            or not isinstance(raw["policy_config"], Mapping)
            or not isinstance(raw["candidates"], list)
            or isinstance(raw["seed"], bool)
            or not isinstance(raw["seed"], int)
            or isinstance(raw["candidate_budget"], bool)
            or not isinstance(raw["candidate_budget"], int)
        ):
            raise ValueError("retriever input bundle contains invalid nested evidence")
        candidates = tuple(
            _candidate_from_dict(item)
            for item in raw["candidates"]
            if isinstance(item, Mapping)
        )
        if len(candidates) != len(raw["candidates"]):
            raise ValueError("retriever input bundle contains an invalid candidate")
        return cls(
            schema_version=str(raw["schema_version"]),
            official_candidate_ids=tuple(str(item) for item in raw["official_candidate_ids"]),
            eligible_event_watermark=str(raw["eligible_event_watermark"]),
            data_snapshot_hash=str(raw["data_snapshot_hash"]),
            seed=int(raw["seed"]),
            candidate_budget=int(raw["candidate_budget"]),
            policy_config=dict(raw["policy_config"]),
            candidates=candidates,
            bundle_hash=str(raw["bundle_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "official_candidate_ids": list(self.official_candidate_ids),
            "eligible_event_watermark": self.eligible_event_watermark,
            "data_snapshot_hash": self.data_snapshot_hash,
            "seed": self.seed,
            "candidate_budget": self.candidate_budget,
            "policy_config": dict(self.policy_config),
            "candidates": [_candidate_to_dict(candidate) for candidate in self.candidates],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


class RetrieverInputArtifactStoreV3:
    media_type = "application/vnd.vibe.retriever-input-bundle-v3+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, bundle: RetrieverInputBundleV3) -> dict[str, str]:
        payload = bundle.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("retriever input artifact contains secret or private path data")
        encoded = canonical_json(payload).encode("utf-8")
        if len(encoded) > _MAX_BUNDLE_BYTES:
            raise ValueError("retriever input artifact exceeds its byte budget")
        relative = self.relative_path(bundle.bundle_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, expected_bundle_hash=bundle.bundle_hash) != bundle:
                raise ValueError("retriever input artifact collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(
        self,
        relative_path: str,
        *,
        expected_bundle_hash: str,
    ) -> RetrieverInputBundleV3:
        target = safe_artifact_path(self.root, relative_path)
        raw_bytes = target.read_bytes()
        if len(raw_bytes) > _MAX_BUNDLE_BYTES:
            raise ValueError("retriever input artifact exceeds its byte budget")

        def reject_constant(value: str) -> None:
            raise ValueError(f"non-finite retriever artifact value: {value}")

        def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
            result: dict[str, Any] = {}
            for key, value in items:
                if key in result:
                    raise ValueError("duplicate retriever artifact key")
                result[key] = value
            return result

        payload = json.loads(
            raw_bytes.decode("utf-8"),
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
        if not isinstance(payload, Mapping):
            raise ValueError("retriever input artifact must be an object")
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("retriever input artifact contains secret or private path data")
        bundle = RetrieverInputBundleV3.from_dict(payload)
        if bundle.bundle_hash != expected_bundle_hash:
            raise ValueError("retriever input artifact identity mismatch")
        if self.relative_path(bundle.bundle_hash) != relative_path.replace("\\", "/"):
            raise ValueError("retriever input artifact path is not content addressed")
        return bundle

    @staticmethod
    def relative_path(bundle_hash: str) -> str:
        prefix, separator, digest = bundle_hash.partition(":")
        if prefix != "sha256" or separator != ":" or len(digest) != 64:
            raise ValueError("retriever input artifact hash is invalid")
        int(digest, 16)
        return f"retriever-input-v3/{digest[:2]}/{digest}.json"


__all__ = [
    "RetrieverInputArtifactStoreV3", "RetrieverInputBundleV3",
]
