"""Producer-bound golden-slice readiness and formal Activation run inputs."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

from src.alpha_foundry.activation.protocol_v2 import (
    CanonicalHashSpecV1,
    DEFAULT_ACTIVATION_HASH_SPEC,
)
from src.alpha_foundry.dsl.executable import DEFAULT_EXECUTABLE_GRAMMAR
from src.alpha_quality.evaluator_dag_v1 import EvaluatorDAGSchedulerPolicyV1
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

PRODUCTION_GENERATOR_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "production_activation_generator_manifest.v1",
        "search_class": "src.alpha_foundry.search.AlphaFoundrySearch",
        "mutator_class": "src.alpha_foundry.mutators.SeedMutator",
        "executable_grammar_snapshot_hash": DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash,
        "dynamic_callable": False,
    }
)
PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "production_activation_evaluator_factory_manifest.v1",
        "serial_factory": (
            "src.alpha_quality.production_evaluator_v1."
            "ProductionCandidateEvaluatorFactoryV1"
        ),
        "dag_factory": (
            "src.alpha_quality.evaluator_dag_v1."
            "ProductionCandidateDAGEvaluatorFactoryV1"
        ),
        "evaluator_injection": False,
    }
)
PRODUCTION_DAG_POLICY_HASH = EvaluatorDAGSchedulerPolicyV1().policy_hash


def _hash(value: str, name: str) -> None:
    if _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical hash")


@dataclass(frozen=True)
class ProductionGoldenSliceReadinessV1:
    schema_version: Literal["production_golden_slice_readiness.v1"]
    research_cycle_id: str
    provider_authority_decision_event_hash: str
    pit_snapshot_event_hash: str | None
    factor_output_event_hash: str | None
    evaluation_event_hash: str | None
    terminal_dossier_event_hash: str | None
    adapter_manifest_compatible: bool
    pit_validation_complete: bool
    factor_output_compatible: bool
    production_evaluator_compatible: bool
    evaluator_dag_compatible: bool
    replay_complete: bool
    ready: bool
    blocker_codes: tuple[str, ...]
    canonical_hash_spec: CanonicalHashSpecV1
    readiness_hash: str

    def __post_init__(self) -> None:
        _hash(
            self.provider_authority_decision_event_hash,
            "provider authority decision event",
        )
        for name in (
            "pit_snapshot_event_hash",
            "factor_output_event_hash",
            "evaluation_event_hash",
            "terminal_dossier_event_hash",
        ):
            value = getattr(self, name)
            if value is not None:
                _hash(value, name)
        if self.blocker_codes != tuple(sorted(set(self.blocker_codes))):
            raise ValueError("golden-slice blockers must be sorted and unique")
        checks = (
            self.adapter_manifest_compatible,
            self.pit_validation_complete,
            self.factor_output_compatible,
            self.production_evaluator_compatible,
            self.evaluator_dag_compatible,
            self.replay_complete,
        )
        if self.ready != (all(checks) and not self.blocker_codes):
            raise ValueError("golden-slice readiness must derive from exact checks")
        expected = self.canonical_hash_spec.hash_payload(
            "production-golden-slice-readiness.v1", self._content_dict()
        )
        if self.readiness_hash != expected:
            raise ValueError("golden-slice readiness hash mismatch")

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (
                self.canonical_hash_spec.to_dict()
                if name == "canonical_hash_spec"
                else list(self.blocker_codes)
                if name == "blocker_codes"
                else getattr(self, name)
            )
            for name in self.__dataclass_fields__
            if name != "readiness_hash"
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "readiness_hash": self.readiness_hash}


@dataclass(frozen=True)
class ProductionActivationRunInputBundleV1:
    schema_version: Literal["production_activation_run_input_bundle.v1"]
    research_cycle_id: str
    resolved_contract_event_hash: str
    resolved_contract_hash: str
    provider_authority_decision_event_hash: str
    provider_authority_decision_hash: str
    golden_slice_readiness_event_hash: str
    pit_snapshot_event_hash: str
    pit_snapshot_hash: str
    train_valid_split_plan_hash: str
    generator_manifest_hash: str
    grammar_hash: str
    flat_policy_hash: str
    topology_policy_hash: str
    evaluator_factory_manifest_hash: str
    dag_policy_hash: str
    candidate_budget: int
    compute_budget: int
    source_watermark: str
    artifact_namespace: str
    canonical_hash_spec: CanonicalHashSpecV1
    bundle_hash: str

    def __post_init__(self) -> None:
        if not self.research_cycle_id or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}", self.artifact_namespace
        ):
            raise ValueError("Activation input identity or namespace is invalid")
        for name in self.__dataclass_fields__:
            if name.endswith("_hash") or name in {
                "source_watermark",
                "resolved_contract_event_hash",
                "provider_authority_decision_event_hash",
                "golden_slice_readiness_event_hash",
                "pit_snapshot_event_hash",
            }:
                _hash(str(getattr(self, name)), name)
        if not 1 <= self.candidate_budget <= 100_000:
            raise ValueError("candidate budget is out of bounds")
        if self.compute_budget < self.candidate_budget:
            raise ValueError("compute budget cannot be below candidate budget")
        if self.generator_manifest_hash != PRODUCTION_GENERATOR_MANIFEST_HASH:
            raise ValueError("Activation input generator is not the production generator")
        if self.grammar_hash != DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash:
            raise ValueError("Activation input grammar is not the executable grammar")
        if (
            self.evaluator_factory_manifest_hash
            != PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH
        ):
            raise ValueError("Activation input evaluator factories differ")
        if self.dag_policy_hash != PRODUCTION_DAG_POLICY_HASH:
            raise ValueError("Activation input DAG policy differs")
        expected = self.canonical_hash_spec.hash_payload(
            "production-activation-run-input-bundle.v1", self._content_dict()
        )
        if self.bundle_hash != expected:
            raise ValueError("Activation input bundle hash mismatch")

    @classmethod
    def create(
        cls,
        *,
        research_cycle_id: str,
        resolved_contract_event_hash: str,
        resolved_contract_hash: str,
        provider_authority_decision_event_hash: str,
        provider_authority_decision_hash: str,
        golden_slice_readiness_event_hash: str,
        pit_snapshot_event_hash: str,
        pit_snapshot_hash: str,
        train_valid_split_plan_hash: str,
        flat_policy_hash: str,
        topology_policy_hash: str,
        candidate_budget: int,
        compute_budget: int,
        source_watermark: str,
        artifact_namespace: str,
        hash_spec: CanonicalHashSpecV1 = DEFAULT_ACTIVATION_HASH_SPEC,
    ) -> "ProductionActivationRunInputBundleV1":
        content = {
            "schema_version": "production_activation_run_input_bundle.v1",
            "research_cycle_id": research_cycle_id,
            "resolved_contract_event_hash": resolved_contract_event_hash,
            "resolved_contract_hash": resolved_contract_hash,
            "provider_authority_decision_event_hash": (
                provider_authority_decision_event_hash
            ),
            "provider_authority_decision_hash": provider_authority_decision_hash,
            "golden_slice_readiness_event_hash": golden_slice_readiness_event_hash,
            "pit_snapshot_event_hash": pit_snapshot_event_hash,
            "pit_snapshot_hash": pit_snapshot_hash,
            "train_valid_split_plan_hash": train_valid_split_plan_hash,
            "generator_manifest_hash": PRODUCTION_GENERATOR_MANIFEST_HASH,
            "grammar_hash": DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash,
            "flat_policy_hash": flat_policy_hash,
            "topology_policy_hash": topology_policy_hash,
            "evaluator_factory_manifest_hash": (
                PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH
            ),
            "dag_policy_hash": PRODUCTION_DAG_POLICY_HASH,
            "candidate_budget": candidate_budget,
            "compute_budget": compute_budget,
            "source_watermark": source_watermark,
            "artifact_namespace": artifact_namespace,
            "canonical_hash_spec": hash_spec.to_dict(),
        }
        return cls(
            **{key: value for key, value in content.items() if key != "canonical_hash_spec"},  # type: ignore[arg-type]
            canonical_hash_spec=hash_spec,
            bundle_hash=hash_spec.hash_payload(
                "production-activation-run-input-bundle.v1", content
            ),
        )

    def _content_dict(self) -> dict[str, Any]:
        return {
            name: (
                self.canonical_hash_spec.to_dict()
                if name == "canonical_hash_spec"
                else getattr(self, name)
            )
            for name in self.__dataclass_fields__
            if name != "bundle_hash"
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "bundle_hash": self.bundle_hash}


@dataclass(frozen=True)
class RecordedProductionGoldenSliceReadinessV1:
    readiness: ProductionGoldenSliceReadinessV1
    event: ResearchEventEnvelope


@dataclass(frozen=True)
class RecordedProductionActivationRunInputBundleV1:
    bundle: ProductionActivationRunInputBundleV1
    event: ResearchEventEnvelope


class ProductionActivationRunInputServiceV1:
    """Registers refs-only inputs after all production authority gates pass."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("Activation run inputs require ResearchEventStore")
        required = (
            "VIBE_TRADING_ALPHA_FOUNDRY",
            "VIBE_TRADING_ALPHA_SCORECARD",
            "VIBE_TRADING_RESEARCH_EVENTS",
            "VIBE_TRADING_FACTOR_DAG",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER",
        )
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("Activation run input capability is disabled")
        self.store = store

    def assess_golden_slice(
        self,
        *,
        run_id: str,
        research_cycle_id: str,
        provider_authority_decision_event_hash: str,
        pit_snapshot_event_hash: str | None = None,
        factor_output_event_hash: str | None = None,
        evaluation_event_hash: str | None = None,
        terminal_dossier_event_hash: str | None = None,
    ) -> RecordedProductionGoldenSliceReadinessV1:
        events = {event.event_hash: event for event in self.store.query_events()}
        decision = events.get(provider_authority_decision_event_hash)
        if decision is None or decision.event_type != "ProviderAuthorityDecisionV1Recorded":
            raise EventTransitionError("golden slice requires provider authority decision")
        blockers: set[str] = set()
        adapter_ok = bool(decision.payload["activation_eligible"])
        if not adapter_ok:
            blockers.add("PROVIDER_FIELD_PIT_AUDIT_INSUFFICIENT")
        snapshot = events.get(pit_snapshot_event_hash or "")
        pit_ok = bool(
            snapshot is not None
            and snapshot.event_type == "AsharePITSnapshotRecorded"
            and snapshot.payload["decision_grade"]
            and snapshot.payload["cutoff_status"] == "within_registered_valid_end"
        )
        if not pit_ok:
            blockers.add("PRODUCTION_GOLDEN_SLICE_SNAPSHOT_UNAVAILABLE")
        factor = events.get(factor_output_event_hash or "")
        factor_ok = factor is not None and factor.event_type == "FactorOutputRecordedV3"
        if not factor_ok:
            blockers.add("PRODUCTION_GOLDEN_SLICE_FACTOR_OUTPUT_UNAVAILABLE")
        evaluation = events.get(evaluation_event_hash or "")
        evaluator_ok = evaluation is not None and evaluation.event_type == "EvaluationRecorded"
        if not evaluator_ok:
            blockers.add("PRODUCTION_GOLDEN_SLICE_EVALUATOR_UNVERIFIED")
        dossier = events.get(terminal_dossier_event_hash or "")
        dag_ok = dossier is not None and dossier.event_type == "TrialTerminalDossierRecorded"
        if not dag_ok:
            blockers.add("PRODUCTION_GOLDEN_SLICE_DAG_UNVERIFIED")
        replay_ok = self.store.verify_chain() and all(
            value is not None
            for value in (snapshot, factor, evaluation, dossier)
        )
        if not replay_ok:
            blockers.add("PRODUCTION_GOLDEN_SLICE_REPLAY_INCOMPLETE")
        content = {
            "schema_version": "production_golden_slice_readiness.v1",
            "research_cycle_id": research_cycle_id,
            "provider_authority_decision_event_hash": (
                provider_authority_decision_event_hash
            ),
            "pit_snapshot_event_hash": pit_snapshot_event_hash,
            "factor_output_event_hash": factor_output_event_hash,
            "evaluation_event_hash": evaluation_event_hash,
            "terminal_dossier_event_hash": terminal_dossier_event_hash,
            "adapter_manifest_compatible": adapter_ok,
            "pit_validation_complete": pit_ok,
            "factor_output_compatible": factor_ok,
            "production_evaluator_compatible": evaluator_ok,
            "evaluator_dag_compatible": dag_ok,
            "replay_complete": replay_ok,
            "ready": not blockers,
            "blocker_codes": sorted(blockers),
            "canonical_hash_spec": DEFAULT_ACTIVATION_HASH_SPEC.to_dict(),
        }
        readiness = ProductionGoldenSliceReadinessV1(
            schema_version="production_golden_slice_readiness.v1",
            research_cycle_id=research_cycle_id,
            provider_authority_decision_event_hash=(
                provider_authority_decision_event_hash
            ),
            pit_snapshot_event_hash=pit_snapshot_event_hash,
            factor_output_event_hash=factor_output_event_hash,
            evaluation_event_hash=evaluation_event_hash,
            terminal_dossier_event_hash=terminal_dossier_event_hash,
            adapter_manifest_compatible=adapter_ok,
            pit_validation_complete=pit_ok,
            factor_output_compatible=factor_ok,
            production_evaluator_compatible=evaluator_ok,
            evaluator_dag_compatible=dag_ok,
            replay_complete=replay_ok,
            ready=not blockers,
            blocker_codes=tuple(sorted(blockers)),
            canonical_hash_spec=DEFAULT_ACTIVATION_HASH_SPEC,
            readiness_hash=DEFAULT_ACTIVATION_HASH_SPEC.hash_payload(
                "production-golden-slice-readiness.v1", content
            ),
        )
        readiness_id = "production-golden-slice-" + readiness.readiness_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ProductionGoldenSliceReadinessV1Recorded",
                entity_id=readiness_id,
                run_id=run_id,
                payload_schema_version="production_golden_slice_readiness_recorded.v1",
                idempotency_key="production-golden-slice-v1:" + research_cycle_id,
                payload={
                    "readiness_id": readiness_id,
                    "research_cycle_id": research_cycle_id,
                    "provider_authority_decision_event_hash": (
                        provider_authority_decision_event_hash
                    ),
                    "ready": readiness.ready,
                    "blocker_codes": list(readiness.blocker_codes),
                    "readiness_hash": readiness.readiness_hash,
                    "canonical_hash_spec": readiness.canonical_hash_spec.to_dict(),
                    "readiness": readiness.to_dict(),
                },
            )
        )
        return RecordedProductionGoldenSliceReadinessV1(readiness, event)

    def register_bundle(
        self,
        *,
        run_id: str,
        bundle: ProductionActivationRunInputBundleV1,
    ) -> RecordedProductionActivationRunInputBundleV1:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        contract = by_hash.get(bundle.resolved_contract_event_hash)
        decision = by_hash.get(bundle.provider_authority_decision_event_hash)
        readiness = by_hash.get(bundle.golden_slice_readiness_event_hash)
        snapshot = by_hash.get(bundle.pit_snapshot_event_hash)
        if decision is None or decision.event_type != "ProviderAuthorityDecisionV1Recorded":
            raise EventTransitionError("Activation input provider authority is missing")
        if not decision.payload["activation_eligible"]:
            raise EventTransitionError("Activation input provider authority is blocked")
        if contract is None or contract.event_type != "ResolvedEvaluationContractRegistered":
            raise EventTransitionError("Activation input contract authority is missing")
        if readiness is None or readiness.event_type != "ProductionGoldenSliceReadinessV1Recorded":
            raise EventTransitionError("Activation input golden-slice authority is missing")
        if not readiness.payload["ready"]:
            raise EventTransitionError("Activation input golden slice is not ready")
        if snapshot is None or snapshot.event_type != "AsharePITSnapshotRecorded":
            raise EventTransitionError("Activation input snapshot authority is missing")
        if not snapshot.payload["decision_grade"]:
            raise EventTransitionError("Activation input snapshot is not decision grade")
        if (
            contract.payload["contract_hash"] != bundle.resolved_contract_hash
            or decision.payload["decision_hash"]
            != bundle.provider_authority_decision_hash
            or snapshot.payload["snapshot_hash"] != bundle.pit_snapshot_hash
            or snapshot.payload["split_plan_hash"]
            != bundle.train_valid_split_plan_hash
            or snapshot.payload["evaluation_policy_event_hash"]
            != contract.payload["evaluation_policy_event_hash"]
        ):
            raise EventTransitionError("Activation input source hashes differ")
        order = {event.event_hash: index for index, event in enumerate(events)}
        source_hashes = (
            bundle.resolved_contract_event_hash,
            bundle.provider_authority_decision_event_hash,
            bundle.golden_slice_readiness_event_hash,
            bundle.pit_snapshot_event_hash,
        )
        if bundle.source_watermark not in order or any(
            order[value] > order[bundle.source_watermark] for value in source_hashes
        ):
            raise EventTransitionError("Activation input watermark precedes a source")
        if any(event.run_id == run_id and event.event_type == "TrialStarted" for event in events):
            raise EventTransitionError("Activation inputs must be frozen before candidates")
        bundle_id = "production-activation-input-" + bundle.bundle_hash[-24:]
        event = self.store._append_producer_event(
            EventDraft(
                event_type="ProductionActivationRunInputBundleV1Registered",
                entity_id=bundle_id,
                run_id=run_id,
                payload_schema_version="production_activation_run_input_bundle_registered.v1",
                idempotency_key="production-activation-input-v1:" + bundle.research_cycle_id,
                payload={
                    "bundle_id": bundle_id,
                    "research_cycle_id": bundle.research_cycle_id,
                    "bundle_hash": bundle.bundle_hash,
                    "resolved_contract_event_hash": bundle.resolved_contract_event_hash,
                    "provider_authority_decision_event_hash": (
                        bundle.provider_authority_decision_event_hash
                    ),
                    "golden_slice_readiness_event_hash": (
                        bundle.golden_slice_readiness_event_hash
                    ),
                    "pit_snapshot_event_hash": bundle.pit_snapshot_event_hash,
                    "source_watermark": bundle.source_watermark,
                    "canonical_hash_spec": bundle.canonical_hash_spec.to_dict(),
                    "bundle": bundle.to_dict(),
                },
            )
        )
        return RecordedProductionActivationRunInputBundleV1(bundle, event)


@dataclass(frozen=True)
class ResearchOnlyActivationRunInputBundleV1:
    """Frozen empirical input for a permanently non-formal Activation run.

    This is intentionally *not* a variant of the Formal V1 bundle.  In
    particular, it records the provider's lower authority rather than turning
    a best-effort receipt into strict PIT evidence.
    """

    schema_version: Literal["research_only_activation_run_input_bundle.v1"]
    research_cycle_id: str
    resolved_contract_event_hash: str
    provider_authority_decision_event_hash: str
    pit_snapshot_event_hash: str
    train_valid_snapshot_event_hash: str
    train_snapshot_hash: str
    valid_snapshot_hash: str
    train_valid_split_plan_hash: str
    flat_policy_hash: str
    topology_policy_hash: str
    candidate_budget: int
    compute_budget: int
    source_watermark: str
    limitation_codes: tuple[str, ...]
    bundle_hash: str

    @classmethod
    def create(cls, **values: Any) -> "ResearchOnlyActivationRunInputBundleV1":
        content = {
            "schema_version": "research_only_activation_run_input_bundle.v1",
            **values,
            "limitation_codes": sorted(set(values["limitation_codes"])),
            "maximum_promotion": "research_only",
            "formal_activation_eligible": False,
            "formal_readiness_effect": "none",
            "official_search_policy_effect": "none",
            "live_trading_meaning": "none",
            "test_final_forward_access_count": 0,
        }
        # The immutable ceiling fields above are included in the content hash,
        # but deliberately are not constructor inputs.
        public = {key: value for key, value in content.items() if key not in {
            "maximum_promotion", "formal_activation_eligible",
            "formal_readiness_effect", "official_search_policy_effect",
            "live_trading_meaning", "test_final_forward_access_count",
        }}
        public["limitation_codes"] = tuple(public["limitation_codes"])
        return cls(**public, bundle_hash=canonical_json_hash(content))

    def __post_init__(self) -> None:
        for name in (
            "resolved_contract_event_hash", "provider_authority_decision_event_hash",
            "pit_snapshot_event_hash", "train_valid_snapshot_event_hash",
            "train_snapshot_hash", "valid_snapshot_hash", "train_valid_split_plan_hash",
            "flat_policy_hash", "topology_policy_hash", "source_watermark", "bundle_hash",
        ):
            _hash(str(getattr(self, name)), name)
        if (not self.research_cycle_id or not self.limitation_codes
                or self.limitation_codes != tuple(sorted(set(self.limitation_codes)))
                or not 1 <= self.candidate_budget <= self.compute_budget):
            raise ValueError("research-only Activation input is invalid")
        content = {**self.to_dict(), "maximum_promotion": "research_only",
                   "formal_activation_eligible": False, "formal_readiness_effect": "none",
                   "official_search_policy_effect": "none", "live_trading_meaning": "none",
                   "test_final_forward_access_count": 0}
        content.pop("bundle_hash")
        if self.bundle_hash != canonical_json_hash(content):
            raise ValueError("research-only Activation input hash mismatch")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "research_cycle_id": self.research_cycle_id,
            "resolved_contract_event_hash": self.resolved_contract_event_hash,
            "provider_authority_decision_event_hash": self.provider_authority_decision_event_hash,
            "pit_snapshot_event_hash": self.pit_snapshot_event_hash,
            "train_valid_snapshot_event_hash": self.train_valid_snapshot_event_hash,
            "train_snapshot_hash": self.train_snapshot_hash,
            "valid_snapshot_hash": self.valid_snapshot_hash,
            "train_valid_split_plan_hash": self.train_valid_split_plan_hash,
            "flat_policy_hash": self.flat_policy_hash,
            "topology_policy_hash": self.topology_policy_hash,
            "candidate_budget": self.candidate_budget,
            "compute_budget": self.compute_budget,
            "source_watermark": self.source_watermark,
            "limitation_codes": list(self.limitation_codes),
            "bundle_hash": self.bundle_hash,
        }


class ResearchOnlyActivationRunInputServiceV1:
    """Append-only registration for empirical, train/valid-only Activation."""

    def __init__(self, store: ResearchEventStore) -> None:
        if not isinstance(store, ResearchEventStore):
            raise TypeError("research-only Activation input requires ResearchEventStore")
        required = ("VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_ALPHA_SCORECARD",
                    "VIBE_TRADING_RESEARCH_EVENTS", "VIBE_TRADING_FACTOR_DAG",
                    "VIBE_TRADING_TOPOLOGY_RETRIEVER")
        if any(not store.flags.enabled(name) for name in required):
            raise RuntimeError("research-only Activation input capability is disabled")
        self.store = store

    def register(self, *, run_id: str, bundle: ResearchOnlyActivationRunInputBundleV1) -> ResearchEventEnvelope:
        if not isinstance(bundle, ResearchOnlyActivationRunInputBundleV1):
            raise TypeError("research-only Activation requires its closed input bundle")
        if run_id != bundle.research_cycle_id:
            raise EventTransitionError(
                "research-only Activation run differs from its frozen cycle"
            )
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        provider = by_hash.get(bundle.provider_authority_decision_event_hash)
        snapshot = by_hash.get(bundle.pit_snapshot_event_hash)
        contract = by_hash.get(bundle.resolved_contract_event_hash)
        train_valid = by_hash.get(bundle.train_valid_snapshot_event_hash)
        if (provider is None or provider.event_type != "ProviderAuthorityDecisionV1Recorded"
                or provider.payload.get("authority_status") not in {"best_effort", "verified_strict"}):
            raise EventTransitionError("research-only input requires best-effort-or-higher provider authority")
        if snapshot is None or snapshot.event_type != "AsharePITSnapshotRecorded":
            raise EventTransitionError("research-only input requires an exact PIT snapshot")
        if contract is None or contract.event_type != "ResolvedEvaluationContractRegistered":
            raise EventTransitionError("research-only input requires a frozen resolved contract")
        if snapshot.payload.get("evaluation_policy_event_hash") != contract.payload.get(
            "evaluation_policy_event_hash"
        ):
            raise EventTransitionError(
                "research-only input snapshot and resolved contract policy differ"
            )
        if train_valid is None or train_valid.event_type != "TrainValidDataSnapshotFrozen":
            raise EventTransitionError("research-only input requires a frozen train/valid snapshot")
        if not snapshot.payload.get("artifact_refs") or not train_valid.payload.get("artifact_refs"):
            raise EventTransitionError("research-only input requires content-addressed source artifacts")
        interface_hashes = tuple(provider.payload.get("interface_audit_event_hashes", ()))
        interfaces = [by_hash.get(str(event_hash)) for event_hash in interface_hashes]
        if not interfaces or any(
            event is None
            or event.event_type != "ProviderInterfacePITAuditV1Recorded"
            or event.payload.get("adapter_registration_event_hash")
            != provider.payload.get("adapter_registration_event_hash")
            for event in interfaces
        ):
            raise EventTransitionError("research-only input requires source-bound provider interfaces")
        field_hashes = tuple(
            str(event_hash)
            for interface in interfaces
            if interface is not None
            for event_hash in interface.payload.get("field_audit_event_hashes", ())
        )
        fields = [by_hash.get(event_hash) for event_hash in field_hashes]
        if not fields or any(
            event is None
            or event.event_type != "ProviderFieldPITAuditV1Recorded"
            or event.payload.get("adapter_registration_event_hash")
            != provider.payload.get("adapter_registration_event_hash")
            or not event.payload.get("audit", {}).get("audit_evidence_hashes")
            for event in fields
        ):
            raise EventTransitionError("research-only input requires source-bound field audits and typed receipts")
        if snapshot.payload.get("adapter_registration_event_hash") != provider.payload.get(
            "adapter_registration_event_hash"
        ):
            raise EventTransitionError("research-only input snapshot and provider authority differ")
        if any(
            event.event_type.startswith(("Final", "Forward"))
            for event in events
        ):
            raise EventTransitionError("research-only input forbids final or forward access")
        order = {event.event_hash: index for index, event in enumerate(events)}
        sources = (bundle.resolved_contract_event_hash, bundle.provider_authority_decision_event_hash,
                   bundle.pit_snapshot_event_hash, bundle.train_valid_snapshot_event_hash)
        audit_sources = tuple(interface_hashes) + tuple(field_hashes)
        if bundle.source_watermark not in order or any(
            order[source] > order[bundle.source_watermark]
            for source in sources + audit_sources
        ):
            raise EventTransitionError("research-only input source watermark differs")
        if not self.store.verify_chain():
            raise EventTransitionError("research-only input sources do not replay")
        identifier = "research-only-activation-input-v1-" + bundle.bundle_hash.removeprefix("sha256:")[:24]
        return self.store._append_producer_event(EventDraft(
            event_type="ResearchOnlyActivationRunInputRegistered", entity_id=identifier,
            run_id=run_id, payload_schema_version="research_only_activation_run_input_registered.v1",
            idempotency_key="research-only-activation-input-v1:" + bundle.bundle_hash,
            payload={"bundle_id": identifier, "research_cycle_id": bundle.research_cycle_id,
                     "bundle_hash": bundle.bundle_hash, "maximum_promotion": "research_only",
                     "formal_activation_eligible": False, "formal_readiness_effect": "none",
                     "official_search_policy_effect": "none", "live_trading_meaning": "none",
                     "test_final_forward_access_count": 0, "bundle": bundle.to_dict()},
        ))


__all__ = [
    "PRODUCTION_DAG_POLICY_HASH",
    "PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH",
    "PRODUCTION_GENERATOR_MANIFEST_HASH",
    "ProductionActivationRunInputBundleV1",
    "ProductionActivationRunInputServiceV1",
    "ResearchOnlyActivationRunInputBundleV1",
    "ResearchOnlyActivationRunInputServiceV1",
    "ProductionGoldenSliceReadinessV1",
    "RecordedProductionActivationRunInputBundleV1",
    "RecordedProductionGoldenSliceReadinessV1",
]
