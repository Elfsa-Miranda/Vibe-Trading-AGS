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
from src.alpha_quality.decision_v2.model import (
    DecisionEvidenceRecord,
    DecisionEvidenceRefs,
)
from src.alpha_quality.decision_v2.repository import DecisionEvidenceRepository
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
        )


@dataclass(frozen=True)
class ProductionActivationCandidateRefsV1:
    """Refs emitted by the existing identity/evaluator/Decision chain."""

    trial_terminal_event_hash: str
    evaluation_event_hash: str | None
    production_quality_decision_event_hash: str | None
    quality_decision_v3_event_hash: str | None
    terminal_dossier_event_hash: str | None

    def __post_init__(self) -> None:
        _require_hash(self.trial_terminal_event_hash, "trial terminal event")
        for name in (
            "evaluation_event_hash",
            "production_quality_decision_event_hash",
            "quality_decision_v3_event_hash",
            "terminal_dossier_event_hash",
        ):
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
    evaluation_event_refs: tuple[str, ...]
    quality_decision_event_refs: tuple[str, ...]
    terminal_dossier_event_refs: tuple[str, ...]
    arm_completed_event_hash: str

    def __post_init__(self) -> None:
        _require_hash(self.arm_started_event_hash, "arm started event")
        _require_hash(self.arm_completed_event_hash, "arm completed event")
        for values in (
            self.retriever_decision_event_refs,
            self.trial_terminal_event_refs,
            self.evaluation_event_refs,
            self.quality_decision_event_refs,
            self.terminal_dossier_event_refs,
        ):
            if values != tuple(sorted(set(values))):
                raise ValueError("Activation factory result refs must be sorted and unique")
            for value in values:
                _require_hash(value, "Activation factory result event")


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
        if not isinstance(
            quality_decision_v3.repository, DecisionEvidenceRepository
        ):
            raise TypeError(
                "Activation factory requires the existing writable Decision evidence repository"
            )
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
        """Close one evaluator arm from producer events without deriving metrics.

        Whole-arm resource evidence is runner-owned and is only available after
        this executor boundary returns.  The pair coordinator joins that later
        protected event reference before invoking Run Source v3.
        """
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
                item.production_quality_decision_event_hash
                for item in candidate_refs
                if item.production_quality_decision_event_hash is not None
            )
        )
        compatibility_decisions = tuple(
            sorted(
                item.quality_decision_v3_event_hash
                for item in candidate_refs
                if item.quality_decision_v3_event_hash is not None
            )
        )
        dossiers = tuple(
            sorted(
                item.terminal_dossier_event_hash
                for item in candidate_refs
                if item.terminal_dossier_event_hash is not None
            )
        )
        for event_hash, event_type in (
            *((value, "TrialTerminated") for value in terminals),
            *((value, "EvaluationRecorded") for value in evaluations),
            *((value, "QualityDecisionV4Recorded") for value in decisions),
            *((value, "QualityDecisionV3Recorded") for value in compatibility_decisions),
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
            "artifact_refs": [],
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
            evaluation_event_refs=evaluations,
            quality_decision_event_refs=decisions,
            terminal_dossier_event_refs=dossiers,
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
    ) -> ProductionActivationCandidateRefsV1:
        """Delegate exact producer refs through DAG evaluator and Decision v3."""
        if not isinstance(request, ProductionEvaluationRequestV1):
            raise TypeError("Activation evaluation requires closed production refs")
        definitions = [
            event
            for event in self.store.query_events()
            if event.event_hash == request.factor_definition_event_hash
            and event.event_type == "FactorDefinitionRecorded"
        ]
        if len(definitions) != 1:
            raise EventTransitionError("Activation candidate definition is unavailable")
        evaluated = self.evaluator.evaluate(request).authoritative_result
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
        production_decision_hash = evaluated.quality_decision_event_hash
        if dossier.payload["quality_decision_event_hash"] != production_decision_hash:
            raise EventTransitionError(
                "production evaluator decision differs from terminal dossier"
            )
        if production_decision_hash is not None:
            production_decision = self._event(
                production_decision_hash, "QualityDecisionV4Recorded"
            )
            if (
                production_decision.run_id != request.run_id
                or production_decision.payload["factor_spec_id"]
                != definitions[0].entity_id
            ):
                raise EventTransitionError(
                    "production quality decision differs from evaluated candidate"
                )
        decision: RecordedQualityDecisionV3 | None = None
        if evaluation_hash is not None:
            decision_evidence_refs = self._producer_bound_decision_refs(
                request=request,
                factor_definition=definitions[0],
                terminal=terminal,
                evidence_event_hashes=evaluated.evidence_event_hashes,
            )
            decision = self.quality_decision_v3.decide_and_record(
                decision_evidence_refs,
                run_id=request.run_id,
            )
        return ProductionActivationCandidateRefsV1(
            trial_terminal_event_hash=terminal.event_hash,
            evaluation_event_hash=(
                None if evaluation_hash is None else str(evaluation_hash)
            ),
            production_quality_decision_event_hash=(
                None
                if production_decision_hash is None
                else str(production_decision_hash)
            ),
            quality_decision_v3_event_hash=(
                None if decision is None else decision.event.event_hash
            ),
            terminal_dossier_event_hash=dossier.event_hash,
        )

    def identity_terminal_refs(
        self,
        *,
        attempt: FactorIdentityAttempt,
        trial_id: str,
        run_id: str,
    ) -> ProductionActivationCandidateRefsV1:
        """Expose the canonical identity producer's terminal without fabrication.

        Invalid and duplicate attempts intentionally have no factor definition,
        evaluator decision, or terminal dossier.  The factory returns that exact
        absence instead of inventing a factor identifier to satisfy a dossier
        schema that applies only after production evaluation.
        """

        if not isinstance(attempt, FactorIdentityAttempt):
            raise TypeError("identity terminal refs require FactorIdentityAttempt")
        if attempt.status not in {"invalid", "duplicate"}:
            raise ValueError("identity terminal refs require an identity-terminal attempt")
        terminals = [
            event
            for event in self.store.query_events(
                event_type="TrialTerminated", entity_id=trial_id
            )
            if event.run_id == run_id and event.payload["status"] == attempt.status
        ]
        if len(terminals) != 1 or terminals[0].payload["evaluation_event_hash"] is not None:
            raise EventTransitionError("identity attempt lacks one exact terminal")
        return ProductionActivationCandidateRefsV1(
            trial_terminal_event_hash=terminals[0].event_hash,
            evaluation_event_hash=None,
            production_quality_decision_event_hash=None,
            quality_decision_v3_event_hash=None,
            terminal_dossier_event_hash=None,
        )

    def _producer_bound_decision_refs(
        self,
        *,
        request: ProductionEvaluationRequestV1,
        factor_definition: ResearchEventEnvelope,
        terminal: ResearchEventEnvelope,
        evidence_event_hashes: tuple[str, ...],
    ) -> DecisionEvidenceRefs:
        """Adapt existing producer events into the existing Decision v3 schema.

        This is deliberately a provenance bridge, not a scorecard or decision
        implementation.  It reads only exact evaluator/source events and uses
        the existing DecisionEvidenceRecord validation plus repository.
        """

        by_hash = {event.event_hash: event for event in self.store.query_events()}
        evidence = [by_hash[value] for value in evidence_event_hashes]
        factor_spec_id = factor_definition.entity_id
        scorecards = [
            event
            for event in evidence
            if event.event_type == "ScorecardDecisionEvidenceV4Recorded"
            and event.payload.get("factor_spec_id") == factor_spec_id
        ]
        if len(scorecards) != 1:
            raise EventTransitionError(
                "Decision v3 bridge requires one production scorecard event"
            )
        snapshot = self._event(request.snapshot_event_hash, "AsharePITSnapshotRecorded")
        if snapshot.run_id != request.run_id:
            raise EventTransitionError("Decision v3 snapshot crosses evaluation runs")
        scorecard = scorecards[0]
        scorecard_codes = tuple(
            sorted(
                {
                    *(str(item) for item in scorecard.payload["caps"]),
                    *(str(item) for item in scorecard.payload["warnings"]),
                }
            )
        )
        snapshot_codes = tuple(
            sorted(
                {
                    *(str(item) for item in snapshot.payload["hard_failures"]),
                    *(str(item) for item in snapshot.payload["caps"]),
                    *(str(item) for item in snapshot.payload["warnings"]),
                }
            )
        )
        records: dict[str, DecisionEvidenceRecord] = {}
        records["scorecard"] = DecisionEvidenceRecord.create(
            evidence_kind="scorecard",
            factor_spec_id=factor_spec_id,
            payload={
                "formula_valid": True,
                "formula_ambiguous": False,
                "lookahead_detected": any(
                    "LOOKAHEAD" in code or "CUTOFF_VIOLATION" in code
                    for code in snapshot_codes
                ),
                "train_valid_terminal": terminal.payload["status"]
                in {"success", "reject", "skip"},
                "reproducible": self.store.verify_chain(),
                "bounded": not any("BOUNDS" in code for code in scorecard_codes),
                "validation_rank_ic": None,
                "regime_dependent": "REGIME_DEPENDENT" in scorecard_codes,
                "limitations": list(scorecard_codes),
            },
        )
        starts = {
            str(event.payload["trial_id"])
            for event in self.store.query_events(event_type="TrialStarted")
            if event.run_id == request.run_id
        }
        terminals = {
            str(event.payload["trial_id"])
            for event in self.store.query_events(event_type="TrialTerminated")
            if event.run_id == request.run_id
        }
        infrastructure = tuple(
            sorted(
                event.event_hash
                for event in self.store.query_events(event_type="TrialTerminated")
                if event.run_id == request.run_id
                and event.payload["status"] == "infrastructure_failure"
            )
        )
        records["ledger"] = DecisionEvidenceRecord.create(
            evidence_kind="ledger",
            factor_spec_id=factor_spec_id,
            payload={
                "complete": starts == terminals,
                "terminal_train_valid": terminal.payload["status"]
                in {"success", "reject", "skip"},
                "reduced_durability": any(
                    "REDUCED_DURABILITY" in event.warnings
                    for event in (factor_definition, terminal)
                ),
                "limitations": [],
                "ledger_schema_version": "decision_ledger_evidence.v2",
                "infrastructure_failure_event_hashes": list(infrastructure),
            },
        )
        records["snapshot"] = DecisionEvidenceRecord.create(
            evidence_kind="snapshot",
            factor_spec_id=factor_spec_id,
            payload={
                "pit_available": bool(snapshot.payload["decision_grade"]),
                "survivorship_bias": snapshot.payload["survivorship_status"]
                != "controlled_by_daily_membership",
                "limitations": list(snapshot_codes),
            },
        )
        execution = self._single_optional(evidence, "ExecutionEvidenceRecorded")
        if execution is not None:
            execution_codes = tuple(
                sorted(
                    {
                        *(str(item) for item in execution.payload["caps"]),
                        "EXECUTION_NUMERIC_DECISION_BRIDGE_UNAVAILABLE",
                    }
                )
            )
            records["execution"] = DecisionEvidenceRecord.create(
                evidence_kind="execution",
                factor_spec_id=factor_spec_id,
                payload={
                    "available": False,
                    "execution_alpha": None,
                    "total_cost": None,
                    "economically_nonnegative": None,
                    "limitations": list(execution_codes),
                },
            )
        secondary = self._single_optional(evidence, "SecondaryEvidenceRecorded")
        if secondary is not None:
            mechanism_status = str(secondary.payload["mechanism_status"])
            ordinal = (
                mechanism_status
                if mechanism_status
                in {"falsified", "inconclusive", "partial_support", "supported"}
                else "inconclusive"
            )
            records["mechanism"] = DecisionEvidenceRecord.create(
                evidence_kind="mechanism",
                factor_spec_id=factor_spec_id,
                payload={
                    "contract_registered": bool(
                        secondary.payload.get("applicability_event_hash")
                    ),
                    "decisive_available": ordinal != "inconclusive",
                    "ordinal_state": ordinal,
                    "limitations": [],
                },
            )
            if bool(secondary.payload["duplicate_detected"]):
                complement_status = "duplicate"
            else:
                complement_status = {
                    "complementary": "complementary",
                    "nonpositive": "nonpositive_marginal_value",
                    "inconclusive": "insufficient",
                    "unavailable": "unavailable",
                }.get(str(secondary.payload["portfolio_status"]), "unavailable")
            records["complement"] = DecisionEvidenceRecord.create(
                evidence_kind="complement",
                factor_spec_id=factor_spec_id,
                payload={"status": complement_status, "limitations": []},
            )
        repository = self.quality_decision_v3.repository
        for record in records.values():
            repository.put(record)
        return DecisionEvidenceRefs(
            factor_spec_id=factor_spec_id,
            scorecard_hash=records["scorecard"].evidence_hash,
            execution_hash=(
                None if "execution" not in records else records["execution"].evidence_hash
            ),
            snapshot_hash=records["snapshot"].evidence_hash,
            ledger_watermark_hash=records["ledger"].evidence_hash,
            mechanism_evidence_hash=(
                None if "mechanism" not in records else records["mechanism"].evidence_hash
            ),
            complement_evidence_hash=(
                None if "complement" not in records else records["complement"].evidence_hash
            ),
            final_test_artifact_hash=None,
            forward_plan_hash=None,
        )

    @staticmethod
    def _single_optional(
        events: list[ResearchEventEnvelope], event_type: str
    ) -> ResearchEventEnvelope | None:
        matches = [event for event in events if event.event_type == event_type]
        if len(matches) > 1:
            raise EventTransitionError(f"Decision v3 bridge has multiple {event_type}")
        return None if not matches else matches[0]

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
        # Invalid/duplicate identity attempts now preserve their real terminal
        # without fabricating a dossier.  Evaluated candidates use the existing
        # production evaluator's dossier-bound QualityDecisionV4 event as the
        # endpoint authority.  The requested existing Decision v3 service still
        # runs as a compatibility/replay path, but its deliberately capped
        # legacy evidence cannot override the production decision.
        return ()


__all__ = [
    "ProductionActivationCandidateFactoryV1",
    "ProductionActivationCandidateRefsV1",
    "ProductionActivationFactoryArmRequestV1",
    "ProductionActivationFactoryArmResultV1",
    "RecordedProductionActivationFactoryBindingV1",
]
