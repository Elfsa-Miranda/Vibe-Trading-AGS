"""Narrow production-component adapter for Activation candidate evaluation.

This module deliberately owns no factor parser, scorecard, execution engine,
decision algorithm, ledger, or artifact repository.  It binds the existing
production generator/identity/evaluator/Decision-v3 surfaces and returns only
authoritative event and artifact references.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Literal, Mapping

from src.alpha_foundry.candidate_pool import CandidateExpression
from src.alpha_foundry.dsl.identity import (
    FactorIdentityAttempt,
    FactorIdentityService,
    FactorSpecSemantics,
)
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.seed_bank import SeedBank
from src.alpha_quality.decision_v2.model import DecisionEvidenceRefs
from src.alpha_quality.decision_v2.source_v3 import (
    QualityDecisionV3Service,
    RecordedQualityDecisionV3,
)
from src.alpha_quality.evaluator_dag_v1 import (
    ProductionCandidateDAGEvaluatorFactoryV1,
    ProductionCandidateDAGEvaluatorV1,
)
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash

from .run_input_v1 import (
    PRODUCTION_DAG_POLICY_HASH,
    PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH,
    PRODUCTION_GENERATOR_MANIFEST_HASH,
    ProductionActivationRunInputBundleV1,
)
from .protocol_v2 import CanonicalHashSpecV1
from .runner import activation_arm_execution_run_id


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FORBIDDEN_REQUEST_FIELDS = frozenset(
    {
        "formula",
        "callable",
        "import_path",
        "panel",
        "ic",
        "yield",
        "score",
        "decision",
        "success_count",
        "worker_summary",
        "report_json",
    }
)


def _require_hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical hash")


def _require_reference(value: Mapping[str, str]) -> dict[str, str]:
    if set(value) != {"relative_path", "artifact_hash", "media_type"}:
        raise ValueError("resource artifact reference has an invalid schema")
    _require_hash(str(value["artifact_hash"]), "resource artifact")
    if not value["relative_path"] or not value["media_type"]:
        raise ValueError("resource artifact reference is incomplete")
    return {name: str(item) for name, item in value.items()}


@dataclass(frozen=True)
class ProductionActivationFactoryArmRequestV1:
    """Closed arm request: references and frozen identities, never outcomes."""

    schema_version: Literal["production_activation_factory_arm_request.v1"]
    run_input_bundle_event_hash: str
    plan_hash: str
    pair_id: str
    run_group_id: str
    execution_run_id: str
    arm: Literal["flat", "topology"]
    retriever_policy_hash: str
    retrieval_authority_event_hash: str
    resource_artifact_ref: Mapping[str, str]

    def __post_init__(self) -> None:
        for name in (
            "run_input_bundle_event_hash",
            "plan_hash",
            "retriever_policy_hash",
            "retrieval_authority_event_hash",
        ):
            _require_hash(str(getattr(self, name)), name)
        if not self.pair_id or not self.run_group_id or not self.execution_run_id:
            raise ValueError("Activation factory arm identities are required")
        object.__setattr__(self, "resource_artifact_ref", _require_reference(self.resource_artifact_ref))

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any]
    ) -> "ProductionActivationFactoryArmRequestV1":
        expected = set(cls.__dataclass_fields__)
        if set(raw) != expected:
            forbidden = sorted(_FORBIDDEN_REQUEST_FIELDS.intersection(raw))
            if forbidden:
                raise ValueError(
                    "Activation factory request contains caller truth: "
                    + ",".join(forbidden)
                )
            raise ValueError("Activation factory arm request schema is closed")
        resource = raw["resource_artifact_ref"]
        if not isinstance(resource, Mapping):
            raise ValueError("resource artifact ref must be typed")
        return cls(
            schema_version=str(raw["schema_version"]),  # type: ignore[arg-type]
            run_input_bundle_event_hash=str(raw["run_input_bundle_event_hash"]),
            plan_hash=str(raw["plan_hash"]),
            pair_id=str(raw["pair_id"]),
            run_group_id=str(raw["run_group_id"]),
            execution_run_id=str(raw["execution_run_id"]),
            arm=str(raw["arm"]),  # type: ignore[arg-type]
            retriever_policy_hash=str(raw["retriever_policy_hash"]),
            retrieval_authority_event_hash=str(raw["retrieval_authority_event_hash"]),
            resource_artifact_ref={str(k): str(v) for k, v in resource.items()},
        )


@dataclass(frozen=True)
class ProductionActivationCandidateRefsV1:
    """Refs emitted by the existing identity/evaluator/Decision chain."""

    trial_terminal_event_hash: str
    evaluation_event_hash: str | None
    quality_decision_v3_event_hash: str | None
    terminal_dossier_event_hash: str

    def __post_init__(self) -> None:
        _require_hash(self.trial_terminal_event_hash, "trial terminal event")
        _require_hash(self.terminal_dossier_event_hash, "terminal dossier event")
        for name in ("evaluation_event_hash", "quality_decision_v3_event_hash"):
            value = getattr(self, name)
            if value is not None:
                _require_hash(value, name)


@dataclass(frozen=True)
class ProductionActivationFactoryArmResultV1:
    """The only public arm result: refs, not metrics or verdict truth."""

    schema_version: Literal["production_activation_factory_arm_result.v1"]
    arm_started_event_hash: str
    retriever_decision_event_refs: tuple[str, ...]
    trial_terminal_event_refs: tuple[str, ...]
    quality_decision_event_refs: tuple[str, ...]
    resource_artifact_ref: Mapping[str, str]
    arm_completed_event_hash: str

    def __post_init__(self) -> None:
        _require_hash(self.arm_started_event_hash, "arm started event")
        _require_hash(self.arm_completed_event_hash, "arm completed event")
        for values in (
            self.retriever_decision_event_refs,
            self.trial_terminal_event_refs,
            self.quality_decision_event_refs,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("Activation factory result refs must be sorted and unique")
            for value in values:
                _require_hash(value, "Activation factory result event")
        object.__setattr__(self, "resource_artifact_ref", _require_reference(self.resource_artifact_ref))


@dataclass(frozen=True)
class RecordedProductionActivationFactoryBindingV1:
    event: ResearchEventEnvelope
    binding_hash: str
    blocker_codes: tuple[str, ...]


class ProductionActivationCandidateFactoryV1:
    """One factory for both arms; only the frozen retriever policy may differ."""

    generator_factory = AlphaFoundrySearch
    serial_evaluator_factory = ProductionCandidateEvaluatorFactoryV1
    dag_evaluator_factory = ProductionCandidateDAGEvaluatorFactoryV1
    identity_service_class = FactorIdentityService
    quality_decision_service_class = QualityDecisionV3Service

    def __init__(
        self,
        *,
        store: ResearchEventStore,
        quality_decision_v3: QualityDecisionV3Service,
    ) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation candidate factory requires ResearchEventStore")
        if not isinstance(quality_decision_v3, QualityDecisionV3Service):
            raise TypeError("Activation candidate factory requires QualityDecisionV3Service")
        if quality_decision_v3.store is not store:
            raise ValueError("Activation factory and Decision v3 must share the event store")
        self.store = store
        self.identity = FactorIdentityService(store=store, flags=store.flags)
        # Creating the DAG evaluator also creates ProductionCandidateEvaluatorV1
        # through its sole production factory.  No evaluator injection exists.
        self.evaluator: ProductionCandidateDAGEvaluatorV1 = (
            ProductionCandidateDAGEvaluatorFactoryV1.create(store)
        )
        self.quality_decision_v3 = quality_decision_v3

    def bind(
        self,
        *,
        run_id: str,
        run_input_bundle_event_hash: str,
    ) -> RecordedProductionActivationFactoryBindingV1:
        bundle, event = self._bundle(run_input_bundle_event_hash)
        blockers = self._compatibility_blockers()
        content = {
            "schema_version": "production_activation_candidate_factory_binding.v1",
            "run_input_bundle_event_hash": event.event_hash,
            "run_input_bundle_hash": bundle.bundle_hash,
            "generator_manifest_hash": PRODUCTION_GENERATOR_MANIFEST_HASH,
            "evaluator_factory_manifest_hash": PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH,
            "dag_policy_hash": PRODUCTION_DAG_POLICY_HASH,
            "identity_service": "src.alpha_foundry.dsl.identity.FactorIdentityService",
            "quality_decision_service": (
                "src.alpha_quality.decision_v2.source_v3.QualityDecisionV3Service"
            ),
            "same_factory_both_arms": True,
            "only_arm_difference": "retriever_policy_hash",
            "returns_refs_only": True,
            "blocker_codes": list(blockers),
        }
        binding_hash = canonical_json_hash(content)
        binding_id = "production-activation-factory-" + binding_hash[-24:]
        recorded = self.store._append_producer_event(
            EventDraft(
                event_type="ProductionActivationCandidateFactoryV1Bound",
                entity_id=binding_id,
                run_id=run_id,
                payload_schema_version=(
                    "production_activation_candidate_factory_binding_recorded.v1"
                ),
                idempotency_key="production-activation-factory-v1:" + bundle.bundle_hash,
                payload={
                    "binding_id": binding_id,
                    "binding_hash": binding_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                },
            )
        )
        return RecordedProductionActivationFactoryBindingV1(
            recorded, binding_hash, blockers
        )

    def start_arm(
        self, request: ProductionActivationFactoryArmRequestV1
    ) -> ResearchEventEnvelope:
        """Record the exact pre-outcome arm binding after all source checks."""
        if not isinstance(request, ProductionActivationFactoryArmRequestV1):
            raise TypeError("Activation arm start requires a closed factory request")
        bundle, _ = self._bundle(request.run_input_bundle_event_hash)
        expected_policy = (
            bundle.flat_policy_hash
            if request.arm == "flat"
            else bundle.topology_policy_hash
        )
        execution_arm = "control" if request.arm == "flat" else "treatment"
        expected_run_id = activation_arm_execution_run_id(
            plan_hash=request.plan_hash,
            run_group_id=request.run_group_id,
            arm=execution_arm,
        )
        expected_retrieval_type = (
            "PreArmFlatScheduleFrozen"
            if request.arm == "flat"
            else "RetrieverDecisionV7Recorded"
        )
        retrieval = self._event(
            request.retrieval_authority_event_hash, expected_retrieval_type
        )
        if (
            request.retriever_policy_hash != expected_policy
            or request.execution_run_id != expected_run_id
            or retrieval.payload.get("policy_hash") != expected_policy
            or retrieval.payload.get("plan_hash") != request.plan_hash
            or retrieval.payload.get("pair_id") != request.pair_id
        ):
            raise EventTransitionError("Activation arm sources differ from frozen inputs")
        start_hash = canonical_json_hash(
            {
                "schema_version": "production_activation_arm_started.v1",
                "run_input_bundle_event_hash": request.run_input_bundle_event_hash,
                "plan_hash": request.plan_hash,
                "pair_id": request.pair_id,
                "run_group_id": request.run_group_id,
                "execution_run_id": request.execution_run_id,
                "arm": request.arm,
                "retriever_policy_hash": request.retriever_policy_hash,
                "retrieval_authority_event_hash": retrieval.event_hash,
            }
        )
        start_id = "production-activation-arm-start-" + start_hash[-24:]
        return self.store._append_producer_event(
            EventDraft(
                event_type="ProductionActivationArmStartedV1Recorded",
                entity_id=start_id,
                run_id=request.execution_run_id,
                payload_schema_version="production_activation_arm_started_recorded.v1",
                idempotency_key="production-activation-arm-start-v1:" + start_hash,
                payload={
                    "arm_start_id": start_id,
                    "arm_start_hash": start_hash,
                    "run_input_bundle_event_hash": request.run_input_bundle_event_hash,
                    "plan_hash": request.plan_hash,
                    "pair_id": request.pair_id,
                    "run_group_id": request.run_group_id,
                    "arm": request.arm,
                    "retriever_policy_hash": request.retriever_policy_hash,
                    "retrieval_authority_event_hash": retrieval.event_hash,
                },
            )
        )

    def complete_arm(
        self,
        *,
        request: ProductionActivationFactoryArmRequestV1,
        arm_started_event_hash: str,
        candidate_refs: tuple[ProductionActivationCandidateRefsV1, ...],
    ) -> ProductionActivationFactoryArmResultV1:
        """Close one arm from evaluator-minted refs without deriving metrics."""
        started = self._event(
            arm_started_event_hash, "ProductionActivationArmStartedV1Recorded"
        )
        if (
            started.run_id != request.execution_run_id
            or started.payload["retrieval_authority_event_hash"]
            != request.retrieval_authority_event_hash
        ):
            raise EventTransitionError("Activation arm completion differs from start")
        if not candidate_refs or any(
            not isinstance(item, ProductionActivationCandidateRefsV1)
            for item in candidate_refs
        ):
            raise TypeError("Activation arm completion requires evaluator-minted refs")
        terminals = tuple(sorted(item.trial_terminal_event_hash for item in candidate_refs))
        evaluations = tuple(
            sorted(
                item.evaluation_event_hash
                for item in candidate_refs
                if item.evaluation_event_hash is not None
            )
        )
        decisions = tuple(
            sorted(
                item.quality_decision_v3_event_hash
                for item in candidate_refs
                if item.quality_decision_v3_event_hash is not None
            )
        )
        dossiers = tuple(sorted(item.terminal_dossier_event_hash for item in candidate_refs))
        for event_hash, event_type in (
            *((value, "TrialTerminated") for value in terminals),
            *((value, "EvaluationRecorded") for value in evaluations),
            *((value, "QualityDecisionV3Recorded") for value in decisions),
            *((value, "TrialTerminalDossierRecorded") for value in dossiers),
        ):
            event = self._event(event_hash, event_type)
            if event.run_id != request.execution_run_id:
                raise EventTransitionError("Activation arm refs cross execution runs")
        content = {
            "schema_version": "production_activation_arm_completed.v1",
            "arm_started_event_hash": started.event_hash,
            "run_input_bundle_event_hash": request.run_input_bundle_event_hash,
            "plan_hash": request.plan_hash,
            "pair_id": request.pair_id,
            "run_group_id": request.run_group_id,
            "arm": request.arm,
            "retrieval_authority_event_hashes": [
                request.retrieval_authority_event_hash
            ],
            "trial_terminal_event_hashes": list(terminals),
            "evaluation_event_hashes": list(evaluations),
            "quality_decision_event_hashes": list(decisions),
            "terminal_dossier_event_hashes": list(dossiers),
            "artifact_refs": [dict(request.resource_artifact_ref)],
        }
        completion_hash = canonical_json_hash(content)
        completion_id = "production-activation-arm-complete-" + completion_hash[-24:]
        completed = self.store._append_producer_event(
            EventDraft(
                event_type="ProductionActivationArmCompletedV1Recorded",
                entity_id=completion_id,
                run_id=request.execution_run_id,
                payload_schema_version="production_activation_arm_completed_recorded.v1",
                idempotency_key="production-activation-arm-complete-v1:"
                + completion_hash,
                payload={
                    "arm_completion_id": completion_id,
                    "arm_completion_hash": completion_hash,
                    **{key: value for key, value in content.items() if key != "schema_version"},
                },
            )
        )
        return ProductionActivationFactoryArmResultV1(
            schema_version="production_activation_factory_arm_result.v1",
            arm_started_event_hash=started.event_hash,
            retriever_decision_event_refs=(request.retrieval_authority_event_hash,),
            trial_terminal_event_refs=terminals,
            quality_decision_event_refs=decisions,
            resource_artifact_ref=request.resource_artifact_ref,
            arm_completed_event_hash=completed.event_hash,
        )

    def record_generated_identity(
        self,
        *,
        candidate: CandidateExpression,
        semantics: FactorSpecSemantics,
        trial_id: str,
        run_id: str,
    ) -> FactorIdentityAttempt:
        """Use the canonical production identity producer; no parser is copied."""
        if not isinstance(candidate, CandidateExpression):
            raise TypeError("factory identity input must be a generated candidate")
        return self.identity.record_attempt(
            trial_id=trial_id,
            run_id=run_id,
            candidate_id=candidate.candidate_id,
            formula=candidate.formula,
            semantics=semantics,
        )

    def evaluate_recorded_candidate(
        self,
        *,
        request: ProductionEvaluationRequestV1,
        decision_evidence_refs: DecisionEvidenceRefs,
    ) -> ProductionActivationCandidateRefsV1:
        """Delegate exact refs through DAG evaluator and existing Decision v3.

        ``decision_evidence_refs`` are resolved and frozen by the supplied
        QualityDecisionV3Service.  This adapter never accepts evidence payloads,
        scores, decisions, or reports.
        """
        if not isinstance(request, ProductionEvaluationRequestV1):
            raise TypeError("Activation evaluation requires closed production refs")
        if not isinstance(decision_evidence_refs, DecisionEvidenceRefs):
            raise TypeError("Activation Decision v3 input requires typed evidence refs")
        definitions = [
            event
            for event in self.store.query_events()
            if event.event_hash == request.factor_definition_event_hash
            and event.event_type == "FactorDefinitionRecorded"
        ]
        if len(definitions) != 1:
            raise EventTransitionError("Activation candidate definition is unavailable")
        if definitions[0].entity_id != decision_evidence_refs.factor_spec_id:
            raise EventTransitionError("Activation Decision refs bind another factor")
        evaluated = self.evaluator.evaluate(request).authoritative
        terminal = self._event(evaluated.terminal_event_hash, "TrialTerminated")
        dossier = next(
            (
                event
                for event in self.store.query_events(
                    event_type="TrialTerminalDossierRecorded"
                )
                if event.payload["terminal_dossier_hash"]
                == evaluated.terminal_dossier_hash
            ),
            None,
        )
        if dossier is None:
            raise EventTransitionError("production evaluator omitted terminal dossier")
        evaluation_hash = terminal.payload["evaluation_event_hash"]
        decision: RecordedQualityDecisionV3 | None = None
        if evaluation_hash is not None:
            decision = self.quality_decision_v3.decide_and_record(
                decision_evidence_refs,
                run_id=request.run_id,
            )
        return ProductionActivationCandidateRefsV1(
            trial_terminal_event_hash=terminal.event_hash,
            evaluation_event_hash=(
                None if evaluation_hash is None else str(evaluation_hash)
            ),
            quality_decision_v3_event_hash=(
                None if decision is None else decision.event.event_hash
            ),
            terminal_dossier_event_hash=dossier.event_hash,
        )

    def build_generator(
        self,
        *,
        seed_bank: SeedBank,
        mutator: Any,
        candidate_budget: int,
        compute_budget: int,
    ) -> AlphaFoundrySearch:
        """The single generator construction boundary shared by both policies."""
        return AlphaFoundrySearch(
            seed_bank=seed_bank,
            mutator=mutator,
            max_candidates=candidate_budget,
            trial_budget=compute_budget,
        )

    def _bundle(
        self, event_hash: str
    ) -> tuple[ProductionActivationRunInputBundleV1, ResearchEventEnvelope]:
        _require_hash(event_hash, "run input bundle event")
        event = self._event(
            event_hash, "ProductionActivationRunInputBundleV1Registered"
        )
        raw = event.payload.get("bundle")
        if not isinstance(raw, Mapping):
            raise EventTransitionError("Activation input bundle payload is unavailable")
        bundle = ProductionActivationRunInputBundleV1(
            **{
                key: value
                for key, value in raw.items()
                if key != "canonical_hash_spec"
            },
            canonical_hash_spec=CanonicalHashSpecV1(
                **dict(event.payload["canonical_hash_spec"])
            ),
        )
        return bundle, event

    def _event(self, event_hash: str, event_type: str) -> ResearchEventEnvelope:
        matches = [
            event
            for event in self.store.query_events(event_type=event_type)
            if event.event_hash == event_hash
        ]
        if len(matches) != 1:
            raise EventTransitionError(f"Activation factory requires one {event_type}")
        return matches[0]

    @staticmethod
    def _compatibility_blockers() -> tuple[str, ...]:
        # These are repository facts, not speculative runtime failures.  The
        # current evaluator emits Decision v4 in its dossier while Run Source
        # v3 requires a separately frozen Decision v3 event; duplicate/invalid
        # identity terminals also lack an existing dossier producer.  The
        # adapter can evaluate prepared, producer-bound refs, but formal arm
        # execution must remain blocked until both existing boundaries close.
        return (
            "IDENTITY_TERMINAL_DOSSIER_PRODUCER_UNAVAILABLE",
            "QUALITY_DECISION_V3_EVIDENCE_REF_BRIDGE_NOT_PRODUCER_BOUND",
        )


__all__ = [
    "ProductionActivationCandidateFactoryV1",
    "ProductionActivationCandidateRefsV1",
    "ProductionActivationFactoryArmRequestV1",
    "ProductionActivationFactoryArmResultV1",
    "RecordedProductionActivationFactoryBindingV1",
]
