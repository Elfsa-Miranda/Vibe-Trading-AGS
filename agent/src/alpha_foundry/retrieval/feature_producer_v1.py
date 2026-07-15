"""Deterministic Retriever features rebuilt from frozen train/valid evidence."""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping

import pandas as pd  # type: ignore[import-untyped]

from src.alpha_foundry.artifacts import safe_artifact_path, safe_artifact_write_json
from src.alpha_foundry.dsl.operators import evaluate_formula
from src.alpha_foundry.memory.service import ValidationUtilityPolicy
from src.alpha_foundry.memory.utility import (
    SCORECARD_MEDIA_TYPE,
    mean_valid_rank_icir_utility,
)
from src.alpha_quality.execution_evidence_v1 import (
    ExecutionEvidenceArtifactStoreV1,
    ExecutionEvidenceServiceV1,
)
from src.alpha_quality.predictive_evidence_v4 import (
    PREDICTIVE_EVIDENCE_MEDIA_TYPE,
    SCORECARD_V4_MEDIA_TYPE,
    PredictiveEvidenceV1,
    ScorecardDecisionEvidenceV4,
)
from src.alpha_foundry.retrieval.action_template_v1 import (
    FrozenRetrieverActionTemplateV1,
)
from src.alpha_foundry.retrieval.evidence_v3 import (
    _candidate_from_dict,
    _candidate_to_dict,
)
from src.alpha_foundry.retrieval.feature_source_v1 import (
    FrozenTrainValidSnapshotArtifactStoreV1,
)
from src.alpha_foundry.retrieval.model import (
    FactorOutputPanel,
    OutputPoint,
    RetrievalCandidate,
    SemanticEmbeddingEvidence,
)
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, ResearchEventEnvelope, ResearchEventStore
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
# The frozen producer policy may materialize eight candidate panels plus the
# all-other-factor reference panels.  Keep a bounded JSON artifact while
# allowing that production-sized, schema-v1 payload to replay exactly.
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024


def _strict_json_object(raw: bytes, *, label: str) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite {label} value: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"duplicate {label} key")
            result[key] = value
        return result

    payload = json.loads(
        raw.decode("utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(payload, Mapping):
        raise ValueError(f"{label} must be an object")
    if canonical_json(payload) != canonical_json(redact_secrets(payload)):
        raise ValueError(f"{label} contains unsafe material")
    return payload


@dataclass(frozen=True)
class RetrieverFeaturePolicyV1:
    schema_version: str = "retriever_feature_policy.v1"
    output_provider: str = "core_dsl_evaluator.v1"
    reference_pool: str = "all_other_terminal_discovery_factors_sorted.v1"
    base_metric: str = "mean_valid_rank_icir"
    base_transform: str = "exp_clipped_minus20_plus20.v1"
    cost_metric: str = "scorecard_execution_cost_bps_over_10000.v1"
    semantic_policy: str = "explicit_unavailable_without_registered_provider.v1"
    maximum_reference_factors: int = 256

    def __post_init__(self) -> None:
        if (
            self.schema_version != "retriever_feature_policy.v1"
            or self.output_provider != "core_dsl_evaluator.v1"
            or self.reference_pool
            != "all_other_terminal_discovery_factors_sorted.v1"
            or self.base_metric != "mean_valid_rank_icir"
            or self.base_transform != "exp_clipped_minus20_plus20.v1"
            or self.cost_metric
            != "scorecard_execution_cost_bps_over_10000.v1"
            or self.semantic_policy
            != "explicit_unavailable_without_registered_provider.v1"
            or not 1 <= self.maximum_reference_factors <= 256
        ):
            raise ValueError("unsupported Retriever feature policy")

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "output_provider": self.output_provider,
            "reference_pool": self.reference_pool,
            "base_metric": self.base_metric,
            "base_transform": self.base_transform,
            "cost_metric": self.cost_metric,
            "semantic_policy": self.semantic_policy,
            "maximum_reference_factors": self.maximum_reference_factors,
        }


@dataclass(frozen=True)
class RetrieverFeatureSourceV1:
    schema_version: str
    execution_run_id: str
    snapshot_event_hash: str
    snapshot_hash: str
    eligible_event_watermark: str
    retrieval_policy: Mapping[str, Any]
    retrieval_policy_hash: str
    feature_policy: Mapping[str, Any]
    feature_policy_hash: str
    action_event_hashes: tuple[str, ...]
    scorecard_event_hashes: tuple[str, ...]
    candidates: tuple[RetrievalCandidate, ...]
    source_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "retriever_feature_source.v1":
            raise ValueError("unsupported Retriever feature source")
        hashes = (
            self.snapshot_event_hash,
            self.snapshot_hash,
            self.eligible_event_watermark,
            self.retrieval_policy_hash,
            self.feature_policy_hash,
            self.source_hash,
            *self.action_event_hashes,
            *self.scorecard_event_hashes,
        )
        if any(_HASH_RE.fullmatch(value) is None for value in hashes):
            raise ValueError("Retriever feature source contains an invalid hash")
        policy = RetrieverFeaturePolicyV1(**dict(self.feature_policy))
        retrieval_policy = ActivationRetrieverPolicy(**dict(self.retrieval_policy))
        if (
            policy.policy_hash != self.feature_policy_hash
            or retrieval_policy.policy_hash != self.retrieval_policy_hash
        ):
            raise ValueError("Retriever feature policy hash differs")
        if (
            not self.execution_run_id
            or not self.candidates
            or len(self.action_event_hashes) != len(self.candidates)
            or len(self.action_event_hashes) != len(set(self.action_event_hashes))
            or len(self.scorecard_event_hashes)
            != len(set(self.scorecard_event_hashes))
        ):
            raise ValueError("Retriever feature source identities are inconsistent")
        if self.source_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("Retriever feature source hash mismatch")
        object.__setattr__(self, "feature_policy", MappingProxyType(policy.to_dict()))
        object.__setattr__(
            self, "retrieval_policy", MappingProxyType(retrieval_policy.to_dict())
        )

    @classmethod
    def build(
        cls,
        *,
        execution_run_id: str,
        snapshot_event_hash: str,
        snapshot_hash: str,
        eligible_event_watermark: str,
        retrieval_policy: ActivationRetrieverPolicy,
        feature_policy: RetrieverFeaturePolicyV1,
        action_event_hashes: tuple[str, ...],
        scorecard_event_hashes: tuple[str, ...],
        candidates: tuple[RetrievalCandidate, ...],
    ) -> "RetrieverFeatureSourceV1":
        content = {
            "schema_version": "retriever_feature_source.v1",
            "execution_run_id": execution_run_id,
            "snapshot_event_hash": snapshot_event_hash,
            "snapshot_hash": snapshot_hash,
            "eligible_event_watermark": eligible_event_watermark,
            "retrieval_policy": retrieval_policy.to_dict(),
            "retrieval_policy_hash": retrieval_policy.policy_hash,
            "feature_policy": feature_policy.to_dict(),
            "feature_policy_hash": feature_policy.policy_hash,
            "action_event_hashes": list(action_event_hashes),
            "scorecard_event_hashes": list(scorecard_event_hashes),
            "candidates": [_candidate_to_dict(item) for item in candidates],
        }
        return cls(
            schema_version="retriever_feature_source.v1",
            execution_run_id=execution_run_id,
            snapshot_event_hash=snapshot_event_hash,
            snapshot_hash=snapshot_hash,
            eligible_event_watermark=eligible_event_watermark,
            retrieval_policy=retrieval_policy.to_dict(),
            retrieval_policy_hash=retrieval_policy.policy_hash,
            feature_policy=feature_policy.to_dict(),
            feature_policy_hash=feature_policy.policy_hash,
            action_event_hashes=action_event_hashes,
            scorecard_event_hashes=scorecard_event_hashes,
            candidates=candidates,
            source_hash=canonical_json_hash(content),
        )

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "RetrieverFeatureSourceV1":
        expected = {
            "schema_version", "execution_run_id", "snapshot_event_hash",
            "snapshot_hash", "eligible_event_watermark", "retrieval_policy_hash",
            "retrieval_policy",
            "feature_policy", "feature_policy_hash", "action_event_hashes",
            "scorecard_event_hashes", "candidates", "source_hash",
        }
        if (
            set(raw) != expected
            or not isinstance(raw["feature_policy"], Mapping)
            or not isinstance(raw["retrieval_policy"], Mapping)
            or any(
                not isinstance(raw[name], list)
                for name in (
                    "action_event_hashes", "scorecard_event_hashes", "candidates",
                )
            )
        ):
            raise ValueError("Retriever feature source has an invalid schema")
        candidates = tuple(
            _candidate_from_dict(item)
            for item in raw["candidates"]
            if isinstance(item, Mapping)
        )
        if len(candidates) != len(raw["candidates"]):
            raise ValueError("Retriever feature source candidate is invalid")
        return cls(
            schema_version=str(raw["schema_version"]),
            execution_run_id=str(raw["execution_run_id"]),
            snapshot_event_hash=str(raw["snapshot_event_hash"]),
            snapshot_hash=str(raw["snapshot_hash"]),
            eligible_event_watermark=str(raw["eligible_event_watermark"]),
            retrieval_policy=dict(raw["retrieval_policy"]),
            retrieval_policy_hash=str(raw["retrieval_policy_hash"]),
            feature_policy=dict(raw["feature_policy"]),
            feature_policy_hash=str(raw["feature_policy_hash"]),
            action_event_hashes=tuple(str(item) for item in raw["action_event_hashes"]),
            scorecard_event_hashes=tuple(
                str(item) for item in raw["scorecard_event_hashes"]
            ),
            candidates=candidates,
            source_hash=str(raw["source_hash"]),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "execution_run_id": self.execution_run_id,
            "snapshot_event_hash": self.snapshot_event_hash,
            "snapshot_hash": self.snapshot_hash,
            "eligible_event_watermark": self.eligible_event_watermark,
            "retrieval_policy": dict(self.retrieval_policy),
            "retrieval_policy_hash": self.retrieval_policy_hash,
            "feature_policy": dict(self.feature_policy),
            "feature_policy_hash": self.feature_policy_hash,
            "action_event_hashes": list(self.action_event_hashes),
            "scorecard_event_hashes": list(self.scorecard_event_hashes),
            "candidates": [_candidate_to_dict(item) for item in self.candidates],
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "source_hash": self.source_hash}


class RetrieverFeatureSourceArtifactStoreV1:
    media_type = "application/vnd.vibe.retriever-feature-source-v1+json"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def write(self, source: RetrieverFeatureSourceV1) -> dict[str, str]:
        payload = source.to_dict()
        if canonical_json(payload) != canonical_json(redact_secrets(payload)):
            raise ValueError("Retriever feature source contains unsafe material")
        if len(canonical_json(payload).encode("utf-8")) > _MAX_ARTIFACT_BYTES:
            raise ValueError("Retriever feature source exceeds byte budget")
        relative = self.relative_path(source.source_hash)
        target = safe_artifact_path(self.root, relative)
        if target.exists():
            if self.read(relative, source.source_hash) != source:
                raise ValueError("Retriever feature source collision")
        else:
            safe_artifact_write_json(self.root, relative, payload)
        return {
            "relative_path": relative,
            "artifact_hash": hash_artifact(target),
            "media_type": self.media_type,
        }

    def read(self, relative_path: str, expected_source_hash: str) -> RetrieverFeatureSourceV1:
        target = safe_artifact_path(self.root, relative_path)
        raw = target.read_bytes()
        if len(raw) > _MAX_ARTIFACT_BYTES:
            raise ValueError("Retriever feature source exceeds byte budget")
        payload = _strict_json_object(raw, label="Retriever feature source")
        source = RetrieverFeatureSourceV1.from_dict(payload)
        if source.source_hash != expected_source_hash:
            raise ValueError("Retriever feature source identity differs")
        if self.relative_path(source.source_hash) != relative_path.replace("\\", "/"):
            raise ValueError("Retriever feature source path is not content addressed")
        return source

    @staticmethod
    def relative_path(source_hash: str) -> str:
        if _HASH_RE.fullmatch(source_hash) is None:
            raise ValueError("Retriever feature source hash is invalid")
        digest = source_hash.removeprefix("sha256:")
        return f"retriever-feature-source-v1/{digest[:2]}/{digest}.json"


@dataclass(frozen=True)
class RecordedRetrieverFeatureSourceV1:
    source: RetrieverFeatureSourceV1
    event: ResearchEventEnvelope


class RetrieverFeatureSourceServiceV1:
    def __init__(
        self,
        store: ResearchEventStore,
        *,
        flags: ResolvedAGSFlags,
        feature_policy: RetrieverFeaturePolicyV1 | None = None,
    ) -> None:
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG", "VIBE_TRADING_PROCESS_MEMORY",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not flags.enabled(name) for name in required):
            raise RuntimeError("Retriever feature-source capability is disabled")
        self.store = store
        self.flags = flags
        self.feature_policy = feature_policy or RetrieverFeaturePolicyV1()
        self.projector = DiscoveryEvidenceProjector(flags=flags)
        self.artifacts = RetrieverFeatureSourceArtifactStoreV1(store.artifact_root)
        # A frozen watermark identifies an immutable discovery projection.  Share
        # it across the producer and decision replay services bound to this one
        # store instance so append-time validation does not replay the same
        # prefix for every downstream artifact.
        self._discovery_cache = store.__dict__.setdefault(
            "_immutable_discovery_projection_cache", {}
        )
        self._raw_panel_cache: dict[str, Mapping[str, Any]] = {}
        self._output_panel_cache: dict[tuple[str, str, str], FactorOutputPanel] = {}

    def record(
        self,
        *,
        execution_run_id: str,
        snapshot_event_hash: str,
        action_event_hashes: tuple[str, ...],
        eligible_event_watermark: str,
        retrieval_policy: ActivationRetrieverPolicy,
    ) -> RecordedRetrieverFeatureSourceV1:
        source = self.rebuild(
            execution_run_id=execution_run_id,
            snapshot_event_hash=snapshot_event_hash,
            action_event_hashes=action_event_hashes,
            eligible_event_watermark=eligible_event_watermark,
            retrieval_policy=retrieval_policy,
        )
        reference = self.artifacts.write(source)
        identifier = (
            "retriever-feature-source-v1-"
            + source.source_hash.removeprefix("sha256:")[:24]
        )
        event = self.store.append_event(
            EventDraft(
                event_type="RetrieverFeatureSourceRecorded",
                entity_id=identifier,
                run_id=execution_run_id,
                payload_schema_version="retriever_feature_source_recorded.v1",
                idempotency_key="retriever-feature-source-v1:" + source.source_hash,
                payload={
                    "feature_source_id": identifier,
                    "source_hash": source.source_hash,
                    "execution_run_id": execution_run_id,
                    "snapshot_event_hash": snapshot_event_hash,
                    "snapshot_hash": source.snapshot_hash,
                    "eligible_event_watermark": eligible_event_watermark,
                    "retrieval_policy_hash": retrieval_policy.policy_hash,
                    "feature_policy_hash": source.feature_policy_hash,
                    "action_event_hashes": list(action_event_hashes),
                    "scorecard_event_hashes": list(source.scorecard_event_hashes),
                    "candidate_count": len(source.candidates),
                    "candidate_hashes": [
                        canonical_json_hash(_candidate_to_dict(candidate))
                        for candidate in source.candidates
                    ],
                    "semantic_state": "unavailable",
                    "artifact_refs": [reference],
                },
            )
        )
        return RecordedRetrieverFeatureSourceV1(source, event)

    def rebuild(
        self,
        *,
        execution_run_id: str,
        snapshot_event_hash: str,
        action_event_hashes: tuple[str, ...],
        eligible_event_watermark: str,
        retrieval_policy: ActivationRetrieverPolicy,
    ) -> RetrieverFeatureSourceV1:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        order = {event.event_hash: index for index, event in enumerate(events)}
        snapshot_event = by_hash.get(snapshot_event_hash)
        if snapshot_event is None or snapshot_event.event_type != "TrainValidDataSnapshotFrozen":
            raise ValueError("Retriever feature source lacks frozen train/valid snapshot")
        if order[snapshot_event_hash] >= order.get(eligible_event_watermark, -1):
            raise ValueError("train/valid snapshot must be frozen before discovery evidence")
        refs = [
            reference
            for reference in snapshot_event.payload["artifact_refs"]
            if reference["media_type"]
            == FrozenTrainValidSnapshotArtifactStoreV1.media_type
        ]
        if len(refs) != 1:
            raise ValueError("Retriever feature source snapshot artifact is missing")
        snapshot = FrozenTrainValidSnapshotArtifactStoreV1(
            self.store.artifact_root
        ).read(
            str(refs[0]["relative_path"]),
            str(snapshot_event.payload["snapshot_hash"]),
        )
        replay_key = (snapshot.snapshot_hash, eligible_event_watermark)
        discovery = self._discovery_cache.get(replay_key)
        if discovery is None:
            discovery = self.projector.project_at_watermark(
                self.store,
                data_snapshot_hash=snapshot.snapshot_hash,
                watermark_event_hash=eligible_event_watermark,
            )
            self._discovery_cache[replay_key] = discovery
        actions: list[FrozenRetrieverActionTemplateV1] = []
        for event_hash in action_event_hashes:
            event = by_hash.get(event_hash)
            if (
                event is None
                or event.event_type != "RetrieverActionTemplateFrozen"
                or event.run_id != execution_run_id
                or order[event_hash] <= order[eligible_event_watermark]
            ):
                raise ValueError("Retriever feature action source is invalid")
            action = FrozenRetrieverActionTemplateV1.from_dict(event.payload)
            if (
                action.execution_run_id != execution_run_id
                or action.data_snapshot_hash != snapshot.snapshot_hash
                or action.eligible_event_watermark != eligible_event_watermark
                or action.retrieval_policy_hash != retrieval_policy.policy_hash
            ):
                raise ValueError("Retriever feature action scope differs")
            actions.append(action)
        if not actions or len(action_event_hashes) != len(set(action_event_hashes)):
            raise ValueError("Retriever feature actions must be non-empty and unique")

        factor_ids = discovery.factual.factor_ids()
        definitions = {
            event.entity_id: event
            for event in events
            if event.event_type == "FactorDefinitionRecorded"
            and order[event.event_hash] <= order[eligible_event_watermark]
        }
        panel = self._raw_panel_cache.get(snapshot.snapshot_hash)
        if panel is None:
            panel = snapshot.to_panel()
            self._raw_panel_cache[snapshot.snapshot_hash] = panel
        output_panels = {
            factor_id: self._output_panel(
                factor_id,
                str(definitions[factor_id].payload["metadata"]["canonical_formula"]),
                panel,
                snapshot.snapshot_hash,
            )
            for factor_id in factor_ids
        }
        scorecards = {
            factor_id: self._scorecard_source(
                factor_id,
                snapshot.snapshot_hash,
                events,
                order,
                eligible_event_watermark,
            )
            for factor_id in {action.parent_factor_spec_id for action in actions}
        }
        candidates: list[RetrievalCandidate] = []
        cited_scorecards: list[str] = []
        utility_policy_hash = ValidationUtilityPolicy().policy_hash
        for action in actions:
            factor_id = action.parent_factor_spec_id
            definition = definitions[factor_id]
            references = tuple(
                reference_id
                for reference_id in factor_ids
                if reference_id != factor_id
            )[: self.feature_policy.maximum_reference_factors]
            source_events, utility, estimated_cost, cost_hash = scorecards[factor_id]
            cited_scorecards.extend(event.event_hash for event in source_events)
            context_hash = canonical_json_hash(
                {
                    "parent_factor_spec_id": factor_id,
                    "policy_hash": retrieval_policy.policy_hash,
                    "utility_policy_hash": utility_policy_hash,
                    "data_snapshot_hash": snapshot.snapshot_hash,
                    "regime_config_hash": None,
                }
            )
            candidates.append(
                RetrievalCandidate(
                    factor_spec_id=factor_id,
                    action_id=action.action_id,
                    motif=str(action.expected_motif),
                    parent_context_hash=context_hash,
                    base_ledger_score=math.exp(max(-20.0, min(20.0, utility))),
                    output_panel=output_panels[factor_id],
                    reference_panels=tuple(output_panels[item] for item in references),
                    canonical_ast=definition.payload["metadata"]["canonical_ast"],
                    reference_asts=tuple(
                        definitions[item].payload["metadata"]["canonical_ast"]
                        for item in references
                    ),
                    semantic=SemanticEmbeddingEvidence.build(
                        model_id="unavailable",
                        model_version="none",
                        candidate_vector=None,
                        missing_reason="NO_REGISTERED_SEMANTIC_PROVIDER",
                    ),
                    estimated_cost=estimated_cost,
                    cost_evidence_hash=cost_hash,
                )
            )
        return RetrieverFeatureSourceV1.build(
            execution_run_id=execution_run_id,
            snapshot_event_hash=snapshot_event_hash,
            snapshot_hash=snapshot.snapshot_hash,
            eligible_event_watermark=eligible_event_watermark,
            retrieval_policy=retrieval_policy,
            feature_policy=self.feature_policy,
            action_event_hashes=action_event_hashes,
            scorecard_event_hashes=tuple(sorted(set(cited_scorecards))),
            candidates=tuple(candidates),
        )

    def _scorecard_source(
        self,
        factor_id: str,
        snapshot_hash: str,
        events: list[ResearchEventEnvelope],
        order: Mapping[str, int],
        watermark: str,
    ) -> tuple[tuple[ResearchEventEnvelope, ...], float, float, str]:
        evaluations = [
            event
            for event in events
            if event.event_type == "EvaluationRecorded"
            and event.payload["factor_spec_id"] == factor_id
            and event.payload["data_scope"] in {"valid", "train_valid"}
            and order[event.event_hash] <= order[watermark]
        ]
        if len(evaluations) != 1:
            raise ValueError("Retriever feature requires one validation scorecard")
        evaluation = evaluations[0]
        refs = [
            reference
            for reference in evaluation.payload["artifact_refs"]
            if reference["media_type"] == SCORECARD_MEDIA_TYPE
            and reference["artifact_hash"] == evaluation.payload["scorecard_hash"]
        ]
        if len(refs) == 1:
            path = self.store.artifact_root.joinpath(*str(refs[0]["relative_path"]).split("/"))
            if hash_artifact(path) != evaluation.payload["scorecard_hash"]:
                raise ValueError("Retriever feature scorecard changed after evaluation")
            utility = mean_valid_rank_icir_utility(
                path,
                expected_factor_spec_id=factor_id,
                expected_data_snapshot_hash=snapshot_hash,
            )
            raw = _strict_json_object(path.read_bytes(), label="Retriever discovery scorecard")
            execution = raw.get("execution")
            if (
                not isinstance(execution, Mapping)
                or execution.get("uses_execution_return") is not True
                or isinstance(execution.get("cost_bps_mean"), bool)
                or not isinstance(execution.get("cost_bps_mean"), (int, float))
                or not math.isfinite(float(execution["cost_bps_mean"]))
                or float(execution["cost_bps_mean"]) < 0.0
            ):
                raise ValueError("Retriever feature requires execution cost evidence")
            estimated_cost = float(execution["cost_bps_mean"]) / 10_000.0
            cost_hash = canonical_json_hash(
                {
                    "schema_version": "retriever_cost_evidence.v1",
                    "scorecard_event_hash": evaluation.event_hash,
                    "scorecard_hash": evaluation.payload["scorecard_hash"],
                    "cost_metric": self.feature_policy.cost_metric,
                    "estimated_cost": estimated_cost,
                }
            )
            return (evaluation,), utility, estimated_cost, cost_hash

        v4_refs = [
            reference
            for reference in evaluation.payload["artifact_refs"]
            if reference["media_type"] == SCORECARD_V4_MEDIA_TYPE
        ]
        if len(v4_refs) != 1:
            raise ValueError("Retriever feature scorecard artifact is missing")
        scorecard_path = self.store.artifact_root.joinpath(
            *str(v4_refs[0]["relative_path"]).split("/")
        )
        if hash_artifact(scorecard_path) != v4_refs[0]["artifact_hash"]:
            raise ValueError("Retriever feature v4 scorecard changed after evaluation")
        scorecard = ScorecardDecisionEvidenceV4.from_dict(
            _strict_json_object(scorecard_path.read_bytes(), label="Retriever v4 scorecard")
        )
        if (
            scorecard.factor_spec_id != factor_id
            or scorecard.scorecard_evidence_hash != evaluation.payload["scorecard_hash"]
        ):
            raise ValueError("Retriever feature v4 scorecard identity differs")
        by_hash = {event.event_hash: event for event in events}
        observed = by_hash.get(scorecard.observed_event_hash)
        if (
            observed is None
            or observed.event_type != "ObservedPanelPredictiveEvidenceRecorded"
            or observed.payload["factor_spec_id"] != factor_id
            or order[observed.event_hash] > order[watermark]
        ):
            raise ValueError("Retriever feature observed evidence is unavailable")
        observed_refs = [
            reference
            for reference in observed.payload["artifact_refs"]
            if reference["media_type"] == PREDICTIVE_EVIDENCE_MEDIA_TYPE
        ]
        if len(observed_refs) != 1:
            raise ValueError("Retriever feature observed artifact is missing")
        observed_path = self.store.artifact_root.joinpath(
            *str(observed_refs[0]["relative_path"]).split("/")
        )
        if hash_artifact(observed_path) != observed_refs[0]["artifact_hash"]:
            raise ValueError("Retriever feature observed artifact changed")
        predictive = PredictiveEvidenceV1.from_dict(
            _strict_json_object(observed_path.read_bytes(), label="Retriever observed evidence")
        )
        try:
            utility = float(predictive.split_metrics["valid"]["rank_icir"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("Retriever feature lacks v4 validation utility") from exc
        if not math.isfinite(utility):
            raise ValueError("Retriever feature v4 utility is non-finite")
        execution_events = [
            event
            for event in events
            if event.event_type == "ExecutionEvidenceRecorded"
            and event.payload["factor_spec_id"] == factor_id
            and order[event.event_hash] <= order[watermark]
        ]
        if len(execution_events) != 1:
            raise ValueError("Retriever feature requires one execution evidence event")
        recorded_execution = ExecutionEvidenceServiceV1(self.store)._read_event(
            execution_events[0]
        )
        _, aggregate = ExecutionEvidenceArtifactStoreV1(
            self.store.artifact_root
        ).read_tables(recorded_execution.artifact)
        costs = aggregate["cost_return"].astype(float)
        if costs.empty or not costs.map(math.isfinite).all() or (costs < 0.0).any():
            raise ValueError("Retriever feature execution costs are invalid")
        estimated_cost = float(costs.mean())
        cost_hash = canonical_json_hash(
            {
                "schema_version": "retriever_cost_evidence.v1",
                "scorecard_event_hash": evaluation.event_hash,
                "scorecard_hash": evaluation.payload["scorecard_hash"],
                "execution_event_hash": execution_events[0].event_hash,
                "execution_artifact_hash": recorded_execution.artifact.execution_artifact_hash,
                "cost_metric": self.feature_policy.cost_metric,
                "estimated_cost": estimated_cost,
            }
        )
        return (evaluation, execution_events[0]), utility, estimated_cost, cost_hash

    def _output_panel(
        self,
        factor_id: str,
        formula: str,
        raw_panel: Mapping[str, Any],
        snapshot_hash: str,
    ) -> FactorOutputPanel:
        cache_key = (factor_id, formula, snapshot_hash)
        cached = self._output_panel_cache.get(cache_key)
        if cached is not None:
            return cached
        frames = {
            name: frame
            for name, frame in raw_panel.items()
            if isinstance(frame, pd.DataFrame)
        }
        output = evaluate_formula(formula, frames)
        points = [
            OutputPoint(
                date=pd.Timestamp(date).isoformat(),
                symbol=str(symbol),
                value=(0.0 if pd.isna(value) else float(value)),
                valid=not pd.isna(value),
            )
            for date in output.index
            for symbol, value in output.loc[date].items()
        ]
        panel = FactorOutputPanel.build(
            factor_id,
            points,
            data_snapshot_hash=snapshot_hash,
            data_scope="train_valid",
        )
        self._output_panel_cache[cache_key] = panel
        return panel


__all__ = [
    "RecordedRetrieverFeatureSourceV1",
    "RetrieverFeaturePolicyV1",
    "RetrieverFeatureSourceArtifactStoreV1",
    "RetrieverFeatureSourceServiceV1",
    "RetrieverFeatureSourceV1",
]
