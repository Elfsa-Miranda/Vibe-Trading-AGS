"""Serial, producer-bound reference evaluator for AGS production research."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from src.alpha_quality.predictive_evidence_v4 import (
    PITPredictiveEvidenceServiceV4,
    RecordedPredictiveEvidenceV4,
)
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    ContentAddressedArtifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso


PRODUCTION_NODE_EVENT_TYPE = "ProductionEvaluationNodeRecorded"
TERMINAL_DOSSIER_EVENT_TYPE = "TrialTerminalDossierRecorded"
REPORT_FAILURE_EVENT_TYPE = "ReportMaterializationFailed"
DOSSIER_MEDIA_TYPE = "application/vnd.vibe.trial-terminal-dossier-v1+json"
PRODUCER_SCHEMA_VERSION = "production_candidate_evaluator.v1"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "production_candidate_evaluator_policy.v1",
        "execution_order": [
            "source_resolution",
            "backend_capability",
            "snapshot_authority",
            "factor_output",
            "observed_predictive",
            "pit_predictive",
            "duplicate_identity",
            "execution",
            "complement_mechanism",
            "claim_assessments",
            "narrow_decision",
        ],
        "execution_authority": "blocked_until_phase_4",
        "secondary_authority": "producer_bound_phase_5",
        "claim_decision_authority": "producer_event_only_phase_6",
        "canonical_dossier_projection": "optional_flag_bound_phase_7",
        "infrastructure_decision": "none",
        "timeout_seconds": 300.0,
    }
)
_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_REQUEST_KEYS = frozenset(
    {
        "run_id",
        "trial_id",
        "factor_definition_event_hash",
        "resolved_contract_hash",
        "snapshot_event_hash",
        "source_watermark_event_hash",
        "frozen_comparison_pool_hash",
    }
)
_DOSSIER_KEYS = frozenset(
    {
        "schema_version",
        "run_id",
        "trial_id",
        "factor_spec_id",
        "resolved_contract_hash",
        "snapshot_event_hash",
        "source_watermark_event_hash",
        "completion_status",
        "evidence_bundle_hash",
        "evidence_event_hashes",
        "node_event_hashes",
        "claim_assessment_hashes",
        "quality_decision_event_hash",
        "evaluation_event_hash",
        "terminal_event_hash",
        "reason_codes",
        "producer_schema_version",
        "producer_policy_hash",
        "terminal_dossier_hash",
    }
)


class ProductionProviderUnavailable(RuntimeError):
    """Typed upstream-data unavailability, distinct from infrastructure failure."""


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _strict_json(path: Path) -> Mapping[str, Any]:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite dossier JSON: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate dossier JSON key")
            result[key] = value
        return result

    raw = json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicates,
    )
    if not isinstance(raw, Mapping):
        raise ValueError("terminal dossier must be an object")
    return raw


@dataclass(frozen=True)
class ProductionEvaluationRequestV1:
    run_id: str
    trial_id: str
    factor_definition_event_hash: str
    resolved_contract_hash: str
    snapshot_event_hash: str
    source_watermark_event_hash: str
    frozen_comparison_pool_hash: str | None = None

    def __post_init__(self) -> None:
        if not self.run_id or not self.trial_id:
            raise ValueError("production evaluation run and trial are required")
        for name in (
            "factor_definition_event_hash",
            "resolved_contract_hash",
            "snapshot_event_hash",
            "source_watermark_event_hash",
        ):
            _require_hash(str(getattr(self, name)), name)
        if self.frozen_comparison_pool_hash is not None:
            _require_hash(
                self.frozen_comparison_pool_hash,
                "frozen_comparison_pool_hash",
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> "ProductionEvaluationRequestV1":
        if set(raw) != _REQUEST_KEYS:
            raise ValueError("production evaluation request schema is closed")
        forbidden = {
            "panel",
            "raw_panel",
            "ic",
            "score",
            "decision",
            "caps",
            "warnings",
            "precomputed_evidence",
        }
        if forbidden.intersection(raw):
            raise ValueError("production evaluation request contains caller truth")
        return cls(
            run_id=str(raw["run_id"]),
            trial_id=str(raw["trial_id"]),
            factor_definition_event_hash=str(raw["factor_definition_event_hash"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            snapshot_event_hash=str(raw["snapshot_event_hash"]),
            source_watermark_event_hash=str(raw["source_watermark_event_hash"]),
            frozen_comparison_pool_hash=(
                None
                if raw["frozen_comparison_pool_hash"] is None
                else str(raw["frozen_comparison_pool_hash"])
            ),
        )


class _ProductionResultAuthority:
    pass


_RESULT_AUTHORITY = _ProductionResultAuthority()


@dataclass(frozen=True)
class ProductionEvaluationResultV1:
    completion_status: Literal[
        "completed",
        "partially_completed",
        "invalid",
        "unavailable",
        "timeout",
        "infrastructure_failure",
    ]
    evidence_bundle_hash: str
    evidence_event_hashes: tuple[str, ...]
    claim_assessment_hashes: tuple[str, ...]
    quality_decision_event_hash: str | None
    terminal_event_hash: str
    terminal_dossier_hash: str
    _authority: object = field(repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._authority is not _RESULT_AUTHORITY:
            raise TypeError("production evaluation results are evaluator-minted")
        _require_hash(self.evidence_bundle_hash, "evidence_bundle_hash")
        _require_hash(self.terminal_event_hash, "terminal_event_hash")
        _require_hash(self.terminal_dossier_hash, "terminal_dossier_hash")
        if self.evidence_event_hashes != tuple(sorted(set(self.evidence_event_hashes))):
            raise ValueError("production result evidence hashes are not canonical")
        if self.claim_assessment_hashes != tuple(
            sorted(set(self.claim_assessment_hashes))
        ):
            raise ValueError("production result claim hashes are not canonical")


@dataclass(frozen=True)
class ProductionEvaluationNodeV1:
    run_id: str
    trial_id: str
    factor_spec_id: str
    resolved_contract_hash: str
    node_name: str
    status: Literal["completed", "blocked", "not_run", "unavailable", "invalid"]
    reason_codes: tuple[str, ...]
    source_event_hashes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    node_hash: str

    def __post_init__(self) -> None:
        if self.producer_schema_version != PRODUCER_SCHEMA_VERSION:
            raise ValueError("unknown production evaluator producer")
        if self.producer_policy_hash != PRODUCER_POLICY_HASH:
            raise ValueError("production evaluator policy differs")
        if self.reason_codes != tuple(sorted(set(self.reason_codes))):
            raise ValueError("production node reasons are not canonical")
        if self.source_event_hashes != tuple(sorted(set(self.source_event_hashes))):
            raise ValueError("production node sources are not canonical")
        if self.node_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("production node hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trial_id": self.trial_id,
            "factor_spec_id": self.factor_spec_id,
            "resolved_contract_hash": self.resolved_contract_hash,
            "node_name": self.node_name,
            "status": self.status,
            "reason_codes": list(self.reason_codes),
            "source_event_hashes": list(self.source_event_hashes),
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "node_hash": self.node_hash}

    @classmethod
    def build(
        cls,
        *,
        request: ProductionEvaluationRequestV1,
        factor_spec_id: str,
        node_name: str,
        status: Literal["completed", "blocked", "not_run", "unavailable", "invalid"],
        reason_codes: tuple[str, ...],
        source_event_hashes: tuple[str, ...],
    ) -> "ProductionEvaluationNodeV1":
        content = {
            "run_id": request.run_id,
            "trial_id": request.trial_id,
            "factor_spec_id": factor_spec_id,
            "resolved_contract_hash": request.resolved_contract_hash,
            "node_name": node_name,
            "status": status,
            "reason_codes": sorted(set(reason_codes)),
            "source_event_hashes": sorted(set(source_event_hashes)),
            "producer_schema_version": PRODUCER_SCHEMA_VERSION,
            "producer_policy_hash": PRODUCER_POLICY_HASH,
        }
        return cls(
            run_id=request.run_id,
            trial_id=request.trial_id,
            factor_spec_id=factor_spec_id,
            resolved_contract_hash=request.resolved_contract_hash,
            node_name=node_name,
            status=status,
            reason_codes=tuple(content["reason_codes"]),
            source_event_hashes=tuple(content["source_event_hashes"]),
            producer_schema_version=PRODUCER_SCHEMA_VERSION,
            producer_policy_hash=PRODUCER_POLICY_HASH,
            node_hash=canonical_json_hash(content),
        )


@dataclass(frozen=True)
class TrialTerminalDossierV1:
    schema_version: Literal["trial_terminal_dossier.v1"]
    run_id: str
    trial_id: str
    factor_spec_id: str
    resolved_contract_hash: str
    snapshot_event_hash: str
    source_watermark_event_hash: str
    completion_status: str
    evidence_bundle_hash: str
    evidence_event_hashes: tuple[str, ...]
    node_event_hashes: tuple[str, ...]
    claim_assessment_hashes: tuple[str, ...]
    quality_decision_event_hash: str | None
    evaluation_event_hash: str | None
    terminal_event_hash: str
    reason_codes: tuple[str, ...]
    producer_schema_version: str
    producer_policy_hash: str
    terminal_dossier_hash: str

    def __post_init__(self) -> None:
        if self.schema_version != "trial_terminal_dossier.v1":
            raise ValueError("unsupported terminal dossier")
        if self.completion_status not in {
            "completed",
            "partially_completed",
            "invalid",
            "unavailable",
            "timeout",
            "infrastructure_failure",
        }:
            raise ValueError("unknown terminal dossier completion status")
        for values, name in (
            (self.evidence_event_hashes, "evidence events"),
            (self.node_event_hashes, "node events"),
            (self.claim_assessment_hashes, "claim assessments"),
            (self.reason_codes, "reason codes"),
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError(f"terminal dossier {name} are not canonical")
        if self.producer_schema_version != PRODUCER_SCHEMA_VERSION:
            raise ValueError("unknown dossier producer")
        if self.producer_policy_hash != PRODUCER_POLICY_HASH:
            raise ValueError("dossier producer policy differs")
        if self.terminal_dossier_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("terminal dossier hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "trial_id": self.trial_id,
            "factor_spec_id": self.factor_spec_id,
            "resolved_contract_hash": self.resolved_contract_hash,
            "snapshot_event_hash": self.snapshot_event_hash,
            "source_watermark_event_hash": self.source_watermark_event_hash,
            "completion_status": self.completion_status,
            "evidence_bundle_hash": self.evidence_bundle_hash,
            "evidence_event_hashes": list(self.evidence_event_hashes),
            "node_event_hashes": list(self.node_event_hashes),
            "claim_assessment_hashes": list(self.claim_assessment_hashes),
            "quality_decision_event_hash": self.quality_decision_event_hash,
            "evaluation_event_hash": self.evaluation_event_hash,
            "terminal_event_hash": self.terminal_event_hash,
            "reason_codes": list(self.reason_codes),
            "producer_schema_version": self.producer_schema_version,
            "producer_policy_hash": self.producer_policy_hash,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            **self._content_dict(),
            "terminal_dossier_hash": self.terminal_dossier_hash,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TrialTerminalDossierV1":
        if set(raw) != _DOSSIER_KEYS:
            raise ValueError("terminal dossier schema is not closed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            run_id=str(raw["run_id"]),
            trial_id=str(raw["trial_id"]),
            factor_spec_id=str(raw["factor_spec_id"]),
            resolved_contract_hash=str(raw["resolved_contract_hash"]),
            snapshot_event_hash=str(raw["snapshot_event_hash"]),
            source_watermark_event_hash=str(raw["source_watermark_event_hash"]),
            completion_status=str(raw["completion_status"]),
            evidence_bundle_hash=str(raw["evidence_bundle_hash"]),
            evidence_event_hashes=tuple(
                str(item) for item in raw["evidence_event_hashes"]
            ),
            node_event_hashes=tuple(str(item) for item in raw["node_event_hashes"]),
            claim_assessment_hashes=tuple(
                str(item) for item in raw["claim_assessment_hashes"]
            ),
            quality_decision_event_hash=(
                None
                if raw["quality_decision_event_hash"] is None
                else str(raw["quality_decision_event_hash"])
            ),
            evaluation_event_hash=(
                None
                if raw["evaluation_event_hash"] is None
                else str(raw["evaluation_event_hash"])
            ),
            terminal_event_hash=str(raw["terminal_event_hash"]),
            reason_codes=tuple(str(item) for item in raw["reason_codes"]),
            producer_schema_version=str(raw["producer_schema_version"]),
            producer_policy_hash=str(raw["producer_policy_hash"]),
            terminal_dossier_hash=str(raw["terminal_dossier_hash"]),
        )


class TrialTerminalDossierArtifactStoreV1:
    namespace = "trial-terminal-dossier-v1"

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve(strict=True)
        self.writer = AtomicContentAddressedArtifactWriter(
            self.root,
            max_bytes=4 * 1024**2,
        )

    def write(self, dossier: TrialTerminalDossierV1) -> ContentAddressedArtifact:
        return self.writer.write_json(
            namespace=self.namespace,
            payload=dossier.to_dict(),
            schema_version="trial_terminal_dossier.v1",
            semantic_hash_field="terminal_dossier_hash",
            closed_keys=_DOSSIER_KEYS,
            media_type=DOSSIER_MEDIA_TYPE,
        )

    def read(
        self,
        relative_path: str,
        *,
        expected_hash: str,
        expected_blob_hash: str,
    ) -> TrialTerminalDossierV1:
        normalized = validate_artifact_references(
            self.root,
            [
                {
                    "relative_path": relative_path,
                    "artifact_hash": expected_blob_hash,
                    "media_type": DOSSIER_MEDIA_TYPE,
                }
            ],
        )[0]
        digest = expected_hash.removeprefix("sha256:")
        expected = f"{self.namespace}/{digest[:2]}/{digest}.json"
        if normalized["relative_path"] != expected:
            raise ValueError("terminal dossier path is not content addressed")
        dossier = TrialTerminalDossierV1.from_dict(
            _strict_json(self.root.joinpath(*expected.split("/")))
        )
        if dossier.terminal_dossier_hash != expected_hash:
            raise ValueError("terminal dossier identity differs")
        return dossier


class _ProductionFactoryToken:
    pass


_FACTORY_TOKEN = _ProductionFactoryToken()


class ProductionCandidateEvaluatorV1:
    """Fixed-order serial evaluator. Construct only through its production factory."""

    def __init__(self, store: Any, *, _token: _ProductionFactoryToken) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if _token is not _FACTORY_TOKEN:
            raise TypeError("production evaluator must be created by its factory")
        if not isinstance(store, ResearchEventStore):
            raise TypeError("production evaluator requires ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("production evaluator capability is disabled")
        self.store = store
        self.dossiers = TrialTerminalDossierArtifactStoreV1(store.artifact_root)

    def evaluate(
        self,
        request: ProductionEvaluationRequestV1,
    ) -> ProductionEvaluationResultV1:
        if not isinstance(request, ProductionEvaluationRequestV1):
            raise TypeError("production evaluator accepts only closed request refs")
        sources = self._resolve_sources(request)
        existing = self._existing_result(request, sources["factor"])
        if existing is not None:
            return existing
        started_at = time.monotonic()
        deadline = started_at + 300.0
        nodes: list[ResearchEventEnvelope] = []
        evidence: list[ResearchEventEnvelope] = []
        try:
            nodes.append(
                self._node(
                    request,
                    sources["factor"].entity_id,
                    "source_resolution",
                    "completed",
                    (),
                    tuple(event.event_hash for event in sources.values()),
                )
            )
            self._check_deadline(deadline)
            nodes.append(
                self._node(
                    request,
                    sources["factor"].entity_id,
                    "backend_capability",
                    "completed",
                    (),
                    (sources["factor"].event_hash, sources["contract"].event_hash),
                )
            )
            recorded = PITPredictiveEvidenceServiceV4(self.store).record(
                run_id=request.run_id,
                contract_event_hash=sources["contract"].event_hash,
                factor_definition_event_hash=sources["factor"].event_hash,
                pit_snapshot_event_hash=sources["snapshot"].event_hash,
            )
            evidence.extend(
                [
                    recorded.factor_event,
                    recorded.observed_event,
                    recorded.pit_event,
                    recorded.scorecard_event,
                ]
            )
            execution_recorded: Any | None = None
            from src.alpha_quality.execution_evidence_v1 import (
                ExecutionEvidenceServiceV1,
            )

            if ExecutionEvidenceServiceV1.supports_contract(
                self.store,
                sources["contract"].event_hash,
            ):
                execution_recorded = ExecutionEvidenceServiceV1(self.store).record(
                    run_id=request.run_id,
                    factor_output_event_hash=recorded.factor_event.event_hash,
                    observed_predictive_event_hash=recorded.observed_event.event_hash,
                )
                evidence.append(execution_recorded.event)
            secondary_event: ResearchEventEnvelope | None = None
            claim_decision_events: tuple[ResearchEventEnvelope, ...] = ()
            if request.frozen_comparison_pool_hash is not None:
                from src.alpha_quality.evaluation_contract.applicability import (
                    ApplicabilityAssessmentServiceV1,
                )
                from src.alpha_quality.secondary_evidence_v1 import (
                    SecondaryEvidenceServiceV1,
                )

                applicability = ApplicabilityAssessmentServiceV1(
                    self.store, flags=self.store.flags
                ).assess(
                    run_id=request.run_id,
                    contract_event_hash=sources["contract"].event_hash,
                    factor_definition_event_hash=sources["factor"].event_hash,
                    claim_type="mechanism",
                )
                secondary_event = SecondaryEvidenceServiceV1(self.store).record(
                    run_id=request.run_id,
                    factor_definition_event_hash=sources["factor"].event_hash,
                    comparison_pool_hash=request.frozen_comparison_pool_hash,
                    factor_output_event_hash=recorded.factor_event.event_hash,
                    execution_event_hash=(
                        None
                        if execution_recorded is None
                        else execution_recorded.event.event_hash
                    ),
                    applicability_event_hash=applicability.event.event_hash,
                )
                evidence.append(secondary_event)
                if self.store.flags.enabled("VIBE_TRADING_DECISION_V2"):
                    from src.alpha_quality.claim_decision_v1 import (
                        ClaimDecisionServiceV1,
                    )

                    selection_event, claim_event, decision_event, _ = (
                        ClaimDecisionServiceV1(self.store).record(
                            run_id=request.run_id,
                            trial_id=request.trial_id,
                            factor_spec_id=sources["factor"].entity_id,
                            contract_event_hash=sources["contract"].event_hash,
                            observed_event_hash=recorded.observed_event.event_hash,
                            pit_event_hash=recorded.pit_event.event_hash,
                            execution_event_hash=(
                                None
                                if execution_recorded is None
                                else execution_recorded.event.event_hash
                            ),
                            secondary_event_hash=secondary_event.event_hash,
                        )
                    )
                    claim_decision_events = (
                        selection_event,
                        claim_event,
                        decision_event,
                    )
                    evidence.extend(claim_decision_events)
            nodes.extend(self._predictive_nodes(request, recorded))
            nodes.extend(
                self._blocked_nodes(
                    request,
                    sources["factor"].entity_id,
                    evidence,
                    execution_recorded=execution_recorded,
                    secondary_event=secondary_event,
                    claim_decision_events=claim_decision_events,
                )
            )
            self._check_deadline(deadline)
            return self._complete_evaluation(
                request=request,
                factor=sources["factor"],
                recorded=recorded,
                evidence=evidence,
                nodes=nodes,
                quality_decision_event=(
                    None if not claim_decision_events else claim_decision_events[-1]
                ),
            )
        except TimeoutError:
            return self._terminate_failure(
                request,
                sources["factor"],
                completion_status="timeout",
                reason_codes=("PRODUCTION_EVALUATION_TIMEOUT",),
                evidence=evidence,
                nodes=nodes,
            )
        except ProductionProviderUnavailable:
            return self._terminate_unavailable(
                request,
                sources["factor"],
                evidence=evidence,
                nodes=nodes,
            )
        except EventValidationError as exc:
            return self._terminate_failure(
                request,
                sources["factor"],
                completion_status="invalid",
                reason_codes=("PRODUCTION_EVALUATION_INVALID",),
                evidence=evidence,
                nodes=nodes,
                detail=type(exc).__name__,
            )
        except Exception as exc:
            return self._terminate_failure(
                request,
                sources["factor"],
                completion_status="infrastructure_failure",
                reason_codes=("PRODUCTION_EVALUATION_INFRASTRUCTURE_FAILURE",),
                evidence=evidence,
                nodes=nodes,
                detail=type(exc).__name__,
            )

    @staticmethod
    def _check_deadline(deadline: float) -> None:
        if time.monotonic() > deadline:
            raise TimeoutError("production evaluation exceeded its fixed deadline")

    def _resolve_sources(
        self,
        request: ProductionEvaluationRequestV1,
    ) -> dict[str, ResearchEventEnvelope]:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        factor = by_hash.get(request.factor_definition_event_hash)
        snapshot = by_hash.get(request.snapshot_event_hash)
        watermark = by_hash.get(request.source_watermark_event_hash)
        contracts = [
            event
            for event in events
            if event.event_type == "ResolvedEvaluationContractRegistered"
            and event.payload["contract_hash"] == request.resolved_contract_hash
        ]
        if (
            factor is None
            or factor.event_type != "FactorDefinitionRecorded"
            or snapshot is None
            or snapshot.event_type != "AsharePITSnapshotRecorded"
            or watermark is None
            or len(contracts) != 1
        ):
            raise EventValidationError("production evaluation sources are incomplete")
        contract = contracts[0]
        core = (factor, snapshot, contract)
        if any(event.run_id != request.run_id for event in core):
            raise EventTransitionError("production evaluation cannot mix runs")
        starts = self.store.query_events(
            event_type="TrialStarted",
            entity_id=request.trial_id,
        )
        if (
            len(starts) != 1
            or starts[0].run_id != request.run_id
            or factor.payload["metadata"].get("originating_trial_id")
            != request.trial_id
        ):
            raise EventTransitionError("production evaluation trial binding differs")
        positions = {event.event_hash: index for index, event in enumerate(events)}
        if any(
            positions[event.event_hash] > positions[watermark.event_hash]
            for event in core
        ):
            raise EventTransitionError(
                "production source lies after the frozen watermark"
            )
        if watermark.event_hash != factor.event_hash:
            raise EventTransitionError(
                "production watermark must close at factor definition"
            )
        result = {
            "contract": contract,
            "factor": factor,
            "snapshot": snapshot,
            "watermark": watermark,
        }
        if request.frozen_comparison_pool_hash is not None:
            pools = [
                event
                for event in events
                if event.event_type == "ComparisonPoolFrozen"
                and event.payload["comparison_pool_hash"]
                == request.frozen_comparison_pool_hash
            ]
            if len(pools) != 1:
                raise EventValidationError("frozen comparison pool is unavailable or ambiguous")
            result["comparison_pool"] = pools[0]
        return result

    def _node(
        self,
        request: ProductionEvaluationRequestV1,
        factor_spec_id: str,
        node_name: str,
        status: Literal["completed", "blocked", "not_run", "unavailable", "invalid"],
        reason_codes: tuple[str, ...],
        sources: tuple[str, ...],
    ) -> ResearchEventEnvelope:
        node = ProductionEvaluationNodeV1.build(
            request=request,
            factor_spec_id=factor_spec_id,
            node_name=node_name,
            status=status,
            reason_codes=reason_codes,
            source_event_hashes=sources,
        )
        return self.store._append_producer_event(
            EventDraft(
                event_type=PRODUCTION_NODE_EVENT_TYPE,
                entity_id=f"production-node-{request.trial_id}-{node_name}",
                run_id=request.run_id,
                payload_schema_version="production_evaluation_node_recorded.v1",
                idempotency_key=f"production-node:{request.trial_id}:{node_name}",
                payload={
                    "node_id": f"production-node-{request.trial_id}-{node_name}",
                    **node.to_dict(),
                },
            )
        )

    def _predictive_nodes(
        self,
        request: ProductionEvaluationRequestV1,
        recorded: RecordedPredictiveEvidenceV4,
    ) -> list[ResearchEventEnvelope]:
        factor_id = recorded.factor_output.factor_spec_id
        pit_status: Literal["completed", "unavailable"] = (
            "completed" if recorded.pit.availability == "available" else "unavailable"
        )
        return [
            self._node(
                request,
                factor_id,
                "snapshot_authority",
                (
                    "completed"
                    if recorded.pit.pit_authority["decision_grade"]
                    else "unavailable"
                ),
                (
                    ()
                    if recorded.pit.pit_authority["decision_grade"]
                    else ("PIT_AUTHORITY_UNAVAILABLE",)
                ),
                (recorded.factor_output.pit_snapshot_event_hash,),
            ),
            self._node(
                request,
                factor_id,
                "factor_output",
                "completed",
                (),
                (recorded.factor_event.event_hash,),
            ),
            self._node(
                request,
                factor_id,
                "observed_predictive",
                "completed",
                (),
                (recorded.observed_event.event_hash,),
            ),
            self._node(
                request,
                factor_id,
                "pit_predictive",
                pit_status,
                () if pit_status == "completed" else tuple(recorded.pit.caps),
                (recorded.pit_event.event_hash,),
            ),
        ]

    def _blocked_nodes(
        self,
        request: ProductionEvaluationRequestV1,
        factor_spec_id: str,
        evidence: list[ResearchEventEnvelope],
        *,
        execution_recorded: Any | None,
        secondary_event: ResearchEventEnvelope | None,
        claim_decision_events: tuple[ResearchEventEnvelope, ...],
    ) -> list[ResearchEventEnvelope]:
        source_hashes = tuple(event.event_hash for event in evidence)
        if secondary_event is None:
            secondary_nodes = [
                self._node(
                    request,
                    factor_spec_id,
                    "duplicate_identity",
                    "blocked",
                    ("FROZEN_COMPARISON_POOL_NOT_PROVIDED",),
                    source_hashes,
                ),
                self._node(
                    request,
                    factor_spec_id,
                    "complement_mechanism",
                    "blocked",
                    ("FROZEN_COMPARISON_POOL_NOT_PROVIDED",),
                    source_hashes,
                ),
            ]
        else:
            secondary_nodes = [
                self._node(
                    request,
                    factor_spec_id,
                    "duplicate_identity",
                    "completed",
                    (),
                    (secondary_event.event_hash,),
                ),
                self._node(
                    request,
                    factor_spec_id,
                    "complement_mechanism",
                    "completed",
                    (),
                    (secondary_event.event_hash,),
                ),
            ]
        if claim_decision_events:
            claim_nodes = [
                self._node(
                    request,
                    factor_spec_id,
                    "claim_assessments",
                    "completed",
                    (),
                    tuple(event.event_hash for event in claim_decision_events[:2]),
                )
            ]
        else:
            claim_nodes = [
                self._node(
                    request,
                    factor_spec_id,
                    "claim_assessments",
                    "blocked",
                    ("CLAIM_DECISION_CAPABILITY_DISABLED_OR_SECONDARY_MISSING",),
                    source_hashes,
                )
            ]
        result = [*secondary_nodes, *claim_nodes]
        if execution_recorded is None:
            execution_node = self._node(
                request,
                factor_spec_id,
                "execution",
                "blocked",
                ("EXECUTION_PRODUCER_NOT_AVAILABLE",),
                source_hashes,
            )
        elif execution_recorded.artifact.availability == "available":
            execution_node = self._node(
                request,
                factor_spec_id,
                "execution",
                "completed",
                (),
                (execution_recorded.event.event_hash,),
            )
        else:
            execution_node = self._node(
                request,
                factor_spec_id,
                "execution",
                "unavailable",
                tuple(execution_recorded.artifact.caps)
                or ("EXECUTION_EVIDENCE_PARTIAL",),
                (execution_recorded.event.event_hash,),
            )
        result.insert(1, execution_node)
        if claim_decision_events:
            result.append(
                self._node(
                    request,
                    factor_spec_id,
                    "narrow_decision",
                    "completed",
                    (),
                    (claim_decision_events[-1].event_hash,),
                )
            )
        else:
            result.append(
                self._node(
                    request,
                    factor_spec_id,
                    "narrow_decision",
                    "not_run",
                    ("NARROW_DECISION_PRODUCER_NOT_AVAILABLE",),
                    tuple(event.event_hash for event in result),
                )
            )
        return result

    def _complete_evaluation(
        self,
        *,
        request: ProductionEvaluationRequestV1,
        factor: ResearchEventEnvelope,
        recorded: RecordedPredictiveEvidenceV4,
        evidence: list[ResearchEventEnvelope],
        nodes: list[ResearchEventEnvelope],
        quality_decision_event: ResearchEventEnvelope | None,
    ) -> ProductionEvaluationResultV1:
        evidence_bundle_hash = self._bundle_hash(evidence, nodes)
        evaluation = self.store.append_event(
            EventDraft(
                event_type="EvaluationRecorded",
                entity_id=f"production-evaluation-{request.trial_id}",
                run_id=request.run_id,
                payload_schema_version="evaluation_recorded.v1",
                idempotency_key=f"production-evaluation:{request.trial_id}",
                payload={
                    "evaluation_id": f"production-evaluation-{request.trial_id}",
                    "trial_id": request.trial_id,
                    "factor_spec_id": factor.entity_id,
                    "data_scope": "train_valid",
                    "scorecard_hash": recorded.scorecard.scorecard_evidence_hash,
                    "artifact_refs": [
                        dict(item)
                        for item in recorded.scorecard_event.payload["artifact_refs"]
                    ],
                    "metadata": {
                        "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                        "producer_policy_hash": PRODUCER_POLICY_HASH,
                        "resolved_contract_hash": request.resolved_contract_hash,
                        "source_watermark_event_hash": request.source_watermark_event_hash,
                        "evidence_bundle_hash": evidence_bundle_hash,
                        "quality_decision_event_hash": (
                            None
                            if quality_decision_event is None
                            else quality_decision_event.event_hash
                        ),
                        "formal_yield_eligible": quality_decision_event is not None,
                    },
                },
            )
        )
        if quality_decision_event is None:
            terminal_status = "skip"
            terminal_decision = "none"
            terminal_reasons = ["PRODUCTION_EVALUATION_PARTIAL"]
            completion_status = "partially_completed"
        else:
            terminal_decision = str(quality_decision_event.payload["decision"])
            terminal_status = "reject" if terminal_decision == "reject" else "success"
            terminal_reasons = list(quality_decision_event.payload["reasons"])
            completion_status = "completed"
        terminal = self.store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=request.trial_id,
                run_id=request.run_id,
                payload_schema_version="trial_terminated.v1",
                idempotency_key=f"trial-terminal:{request.trial_id}",
                payload={
                    "trial_id": request.trial_id,
                    "status": terminal_status,
                    "reason_codes": terminal_reasons,
                    "decision": terminal_decision,
                    "evaluation_event_hash": evaluation.event_hash,
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        return self._materialize_dossier(
            request=request,
            factor_spec_id=factor.entity_id,
            completion_status=completion_status,
            evidence=evidence,
            nodes=nodes,
            evaluation=evaluation,
            terminal=terminal,
            reason_codes=tuple(terminal_reasons),
            evidence_bundle_hash=evidence_bundle_hash,
            quality_decision_event=quality_decision_event,
        )

    def _terminate_failure(
        self,
        request: ProductionEvaluationRequestV1,
        factor: ResearchEventEnvelope,
        *,
        completion_status: Literal["invalid", "timeout", "infrastructure_failure"],
        reason_codes: tuple[str, ...],
        evidence: list[ResearchEventEnvelope],
        nodes: list[ResearchEventEnvelope],
        detail: str | None = None,
    ) -> ProductionEvaluationResultV1:
        existing_terminals = self.store.query_events(
            event_type="TrialTerminated",
            entity_id=request.trial_id,
        )
        if existing_terminals:
            existing = self._existing_result(request, factor)
            if existing is None:
                raise EventTransitionError("terminal trial has no recoverable dossier")
            return existing
        failure_kind = {
            "invalid": "invalid",
            "timeout": "timeout",
            "infrastructure_failure": "infrastructure_failure",
        }[completion_status]
        self.store.append_event(
            EventDraft(
                event_type="GenerationFailureRecorded",
                entity_id=request.trial_id,
                run_id=request.run_id,
                payload_schema_version="generation_failure_recorded.v1",
                idempotency_key=f"production-failure:{request.trial_id}",
                payload={
                    "trial_id": request.trial_id,
                    "failure_code": reason_codes[0],
                    "failure_kind": failure_kind,
                    "message": "production evaluator terminated before full evidence"
                    + (" (" + detail + ")" if detail else ""),
                    "occurred_at": utc_now_iso(),
                },
            )
        )
        terminal = self.store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=request.trial_id,
                run_id=request.run_id,
                payload_schema_version="trial_terminated.v1",
                idempotency_key=f"trial-terminal:{request.trial_id}",
                payload={
                    "trial_id": request.trial_id,
                    "status": completion_status,
                    "reason_codes": list(reason_codes),
                    "decision": "none",
                    "evaluation_event_hash": None,
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        bundle_hash = self._bundle_hash(evidence, nodes)
        return self._materialize_dossier(
            request=request,
            factor_spec_id=factor.entity_id,
            completion_status=completion_status,
            evidence=evidence,
            nodes=nodes,
            evaluation=None,
            terminal=terminal,
            reason_codes=reason_codes,
            evidence_bundle_hash=bundle_hash,
        )

    def _terminate_unavailable(
        self,
        request: ProductionEvaluationRequestV1,
        factor: ResearchEventEnvelope,
        *,
        evidence: list[ResearchEventEnvelope],
        nodes: list[ResearchEventEnvelope],
    ) -> ProductionEvaluationResultV1:
        terminal = self.store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id=request.trial_id,
                run_id=request.run_id,
                payload_schema_version="trial_terminated.v1",
                idempotency_key=f"trial-terminal:{request.trial_id}",
                payload={
                    "trial_id": request.trial_id,
                    "status": "skip",
                    "reason_codes": ["PRODUCTION_PROVIDER_UNAVAILABLE"],
                    "decision": "none",
                    "evaluation_event_hash": None,
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        bundle_hash = self._bundle_hash(evidence, nodes)
        return self._materialize_dossier(
            request=request,
            factor_spec_id=factor.entity_id,
            completion_status="unavailable",
            evidence=evidence,
            nodes=nodes,
            evaluation=None,
            terminal=terminal,
            reason_codes=("PRODUCTION_PROVIDER_UNAVAILABLE",),
            evidence_bundle_hash=bundle_hash,
        )

    @staticmethod
    def _bundle_hash(
        evidence: list[ResearchEventEnvelope],
        nodes: list[ResearchEventEnvelope],
    ) -> str:
        return canonical_json_hash(
            {
                "schema_version": "production_evidence_bundle.v1",
                "evidence_payload_hashes": sorted(
                    event.payload_hash for event in evidence
                ),
                "node_hashes": sorted(
                    str(event.payload["node_hash"]) for event in nodes
                ),
            }
        )

    def _materialize_dossier(
        self,
        *,
        request: ProductionEvaluationRequestV1,
        factor_spec_id: str,
        completion_status: str,
        evidence: list[ResearchEventEnvelope],
        nodes: list[ResearchEventEnvelope],
        evaluation: ResearchEventEnvelope | None,
        terminal: ResearchEventEnvelope,
        reason_codes: tuple[str, ...],
        evidence_bundle_hash: str,
        quality_decision_event: ResearchEventEnvelope | None = None,
    ) -> ProductionEvaluationResultV1:
        content = {
            "schema_version": "trial_terminal_dossier.v1",
            "run_id": request.run_id,
            "trial_id": request.trial_id,
            "factor_spec_id": factor_spec_id,
            "resolved_contract_hash": request.resolved_contract_hash,
            "snapshot_event_hash": request.snapshot_event_hash,
            "source_watermark_event_hash": request.source_watermark_event_hash,
            "completion_status": completion_status,
            "evidence_bundle_hash": evidence_bundle_hash,
            "evidence_event_hashes": sorted(event.event_hash for event in evidence),
            "node_event_hashes": sorted(event.event_hash for event in nodes),
            "claim_assessment_hashes": sorted(
                str(event.payload["claim_matrix_hash"])
                for event in evidence
                if event.event_type == "ClaimMatrixRecorded"
            ),
            "quality_decision_event_hash": (
                None
                if quality_decision_event is None
                else quality_decision_event.event_hash
            ),
            "evaluation_event_hash": (
                None if evaluation is None else evaluation.event_hash
            ),
            "terminal_event_hash": terminal.event_hash,
            "reason_codes": sorted(set(reason_codes)),
            "producer_schema_version": PRODUCER_SCHEMA_VERSION,
            "producer_policy_hash": PRODUCER_POLICY_HASH,
        }
        dossier = TrialTerminalDossierV1.from_dict(
            {**content, "terminal_dossier_hash": canonical_json_hash(content)}
        )
        try:
            artifact = self.dossiers.write(dossier)
            terminal_dossier_event = self.store._append_producer_event(
                EventDraft(
                    event_type=TERMINAL_DOSSIER_EVENT_TYPE,
                    entity_id=f"terminal-dossier-{request.trial_id}",
                    run_id=request.run_id,
                    payload_schema_version="trial_terminal_dossier_recorded.v1",
                    idempotency_key=f"trial-terminal-dossier:{request.trial_id}",
                    payload=self.dossier_event_payload(dossier, artifact.reference()),
                )
            )
            if self.store.flags.enabled("VIBE_TRADING_ALPHA_REPORT_API"):
                from src.alpha_quality.research_dossier_v1 import (
                    ResearchDossierServiceV1,
                )

                reports = ResearchDossierServiceV1(self.store)
                try:
                    reports.record_candidate(
                        run_id=request.run_id,
                        factor_spec_id=factor_spec_id,
                    )
                    reports.record_run_report(run_id=request.run_id)
                    reports.record_release_manifest(run_id=request.run_id)
                except Exception as exc:
                    try:
                        reports.record_failure(
                            run_id=request.run_id,
                            trial_id=request.trial_id,
                            factor_spec_id=factor_spec_id,
                            terminal_dossier_event_hash=terminal_dossier_event.event_hash,
                            quality_decision_event_hash=(
                                None
                                if quality_decision_event is None
                                else quality_decision_event.event_hash
                            ),
                            failure_class=type(exc).__name__,
                        )
                    except Exception:
                        pass
        except Exception as exc:
            self._record_materialization_failure(
                request,
                terminal,
                dossier.terminal_dossier_hash,
                type(exc).__name__,
            )
        return ProductionEvaluationResultV1(
            completion_status=completion_status,  # type: ignore[arg-type]
            evidence_bundle_hash=evidence_bundle_hash,
            evidence_event_hashes=tuple(content["evidence_event_hashes"]),
            claim_assessment_hashes=tuple(content["claim_assessment_hashes"]),
            quality_decision_event_hash=content["quality_decision_event_hash"],
            terminal_event_hash=terminal.event_hash,
            terminal_dossier_hash=dossier.terminal_dossier_hash,
            _authority=_RESULT_AUTHORITY,
        )

    def _record_materialization_failure(
        self,
        request: ProductionEvaluationRequestV1,
        terminal: ResearchEventEnvelope,
        dossier_hash: str,
        failure_class: str,
    ) -> None:
        self.store._append_producer_event(
            EventDraft(
                event_type=REPORT_FAILURE_EVENT_TYPE,
                entity_id=f"report-failure-{request.trial_id}",
                run_id=request.run_id,
                payload_schema_version="report_materialization_failed.v1",
                idempotency_key=f"report-materialization-failed:{request.trial_id}",
                payload={
                    "failure_id": f"report-failure-{request.trial_id}",
                    "trial_id": request.trial_id,
                    "terminal_event_hash": terminal.event_hash,
                    "intended_dossier_hash": dossier_hash,
                    "failure_code": "TERMINAL_DOSSIER_MATERIALIZATION_FAILED",
                    "failure_class": failure_class,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                },
            )
        )

    @staticmethod
    def dossier_event_payload(
        dossier: TrialTerminalDossierV1,
        reference: Mapping[str, str],
    ) -> dict[str, Any]:
        return {
            "dossier_id": f"terminal-dossier-{dossier.trial_id}",
            "terminal_dossier_hash": dossier.terminal_dossier_hash,
            "trial_id": dossier.trial_id,
            "factor_spec_id": dossier.factor_spec_id,
            "completion_status": dossier.completion_status,
            "terminal_event_hash": dossier.terminal_event_hash,
            "evaluation_event_hash": dossier.evaluation_event_hash,
            "evidence_bundle_hash": dossier.evidence_bundle_hash,
            "evidence_event_hashes": list(dossier.evidence_event_hashes),
            "node_event_hashes": list(dossier.node_event_hashes),
            "quality_decision_event_hash": dossier.quality_decision_event_hash,
            "source_event_hashes": sorted(
                {
                    dossier.terminal_event_hash,
                    *dossier.evidence_event_hashes,
                    *dossier.node_event_hashes,
                    *(
                        ()
                        if dossier.evaluation_event_hash is None
                        else (dossier.evaluation_event_hash,)
                    ),
                }
            ),
            "producer_schema_version": dossier.producer_schema_version,
            "producer_policy_hash": dossier.producer_policy_hash,
            "artifact_refs": [dict(reference)],
        }

    def _existing_result(
        self,
        request: ProductionEvaluationRequestV1,
        factor: ResearchEventEnvelope,
    ) -> ProductionEvaluationResultV1 | None:
        terminals = self.store.query_events(
            event_type="TrialTerminated",
            entity_id=request.trial_id,
        )
        if not terminals:
            return None
        if len(terminals) != 1 or terminals[0].run_id != request.run_id:
            raise EventTransitionError("production trial has ambiguous terminal state")
        terminal = terminals[0]
        dossier_events = self.store.query_events(
            event_type=TERMINAL_DOSSIER_EVENT_TYPE,
            entity_id=f"terminal-dossier-{request.trial_id}",
        )
        if len(dossier_events) == 1:
            event = dossier_events[0]
            ref = event.payload["artifact_refs"][0]
            dossier = self.dossiers.read(
                str(ref["relative_path"]),
                expected_hash=str(event.payload["terminal_dossier_hash"]),
                expected_blob_hash=str(ref["artifact_hash"]),
            )
            if (
                dossier.run_id != request.run_id
                or dossier.factor_spec_id != factor.entity_id
                or dossier.resolved_contract_hash != request.resolved_contract_hash
                or dossier.snapshot_event_hash != request.snapshot_event_hash
                or dossier.source_watermark_event_hash
                != request.source_watermark_event_hash
                or dossier.terminal_event_hash != terminal.event_hash
            ):
                raise EventValidationError("existing terminal dossier sources differ")
            return ProductionEvaluationResultV1(
                completion_status=dossier.completion_status,  # type: ignore[arg-type]
                evidence_bundle_hash=dossier.evidence_bundle_hash,
                evidence_event_hashes=dossier.evidence_event_hashes,
                claim_assessment_hashes=dossier.claim_assessment_hashes,
                quality_decision_event_hash=dossier.quality_decision_event_hash,
                terminal_event_hash=dossier.terminal_event_hash,
                terminal_dossier_hash=dossier.terminal_dossier_hash,
                _authority=_RESULT_AUTHORITY,
            )
        if len(dossier_events) > 1:
            raise EventTransitionError(
                "production trial has ambiguous terminal dossiers"
            )
        nodes = [
            event
            for event in self.store.query_events(event_type=PRODUCTION_NODE_EVENT_TYPE)
            if event.payload["trial_id"] == request.trial_id
        ]
        evidence_hashes = {
            str(item) for node in nodes for item in node.payload["source_event_hashes"]
        }
        evidence = [
            event
            for event in self.store.query_events()
            if event.event_hash in evidence_hashes
            and event.event_type
            in {
                "FactorOutputRecordedV3",
                "ObservedPanelPredictiveEvidenceRecorded",
                "PITPredictiveEvidenceRecorded",
                "ScorecardDecisionEvidenceV4Recorded",
                "ExecutionEvidenceRecorded",
                "SecondaryEvidenceRecorded",
                "SelectionAssessmentRecorded",
                "ClaimMatrixRecorded",
                "QualityDecisionV4Recorded",
            }
        ]
        evaluations = [
            event
            for event in self.store.query_events(event_type="EvaluationRecorded")
            if event.payload["trial_id"] == request.trial_id
        ]
        completion, reasons = self._completion_from_terminal(terminal)
        decisions = [
            event for event in evidence
            if event.event_type == "QualityDecisionV4Recorded"
        ]
        return self._materialize_dossier(
            request=request,
            factor_spec_id=factor.entity_id,
            completion_status=completion,
            evidence=evidence,
            nodes=nodes,
            evaluation=evaluations[0] if len(evaluations) == 1 else None,
            terminal=terminal,
            reason_codes=reasons,
            evidence_bundle_hash=self._bundle_hash(evidence, nodes),
            quality_decision_event=(decisions[0] if len(decisions) == 1 else None),
        )

    @staticmethod
    def _completion_from_terminal(
        terminal: ResearchEventEnvelope,
    ) -> tuple[str, tuple[str, ...]]:
        reasons = tuple(str(item) for item in terminal.payload["reason_codes"])
        status = str(terminal.payload["status"])
        if status == "timeout":
            return "timeout", reasons
        if status == "invalid":
            return "invalid", reasons
        if status == "infrastructure_failure":
            return "infrastructure_failure", reasons
        if status == "skip":
            return "partially_completed", reasons
        if status in {"success", "reject"}:
            return "completed", reasons
        return "unavailable", reasons


class ProductionCandidateEvaluatorFactoryV1:
    """Only production construction surface; no evaluator injection is accepted."""

    @staticmethod
    def create(store: Any) -> ProductionCandidateEvaluatorV1:
        return ProductionCandidateEvaluatorV1(store, _token=_FACTORY_TOKEN)

    @staticmethod
    def is_formal_yield(result: ProductionEvaluationResultV1) -> bool:
        if not isinstance(result, ProductionEvaluationResultV1):
            raise TypeError("formal yield requires a production evaluation result")
        return (
            result.completion_status == "completed"
            and result.quality_decision_event_hash is not None
        )


__all__ = [
    "DOSSIER_MEDIA_TYPE",
    "PRODUCTION_NODE_EVENT_TYPE",
    "ProductionCandidateEvaluatorFactoryV1",
    "ProductionCandidateEvaluatorV1",
    "ProductionEvaluationNodeV1",
    "ProductionProviderUnavailable",
    "ProductionEvaluationRequestV1",
    "ProductionEvaluationResultV1",
    "REPORT_FAILURE_EVENT_TYPE",
    "TERMINAL_DOSSIER_EVENT_TYPE",
    "TrialTerminalDossierArtifactStoreV1",
    "TrialTerminalDossierV1",
]
