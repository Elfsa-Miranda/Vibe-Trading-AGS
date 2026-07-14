from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

import src.alpha_quality.adapters.baostock_eligible_universe_v1 as baostock_module
from src.alpha_foundry.activation.candidate_factory_v1 import (
    ProductionActivationCandidateFactoryV1,
)
from src.alpha_foundry.candidate_pool import make_candidate
from src.alpha_foundry.dsl.identity import FactorSpecSemantics
from src.alpha_foundry.activation.provider_pit_audit_v1 import (
    ProviderPITAuditServiceV1,
    baostock_golden_cohort_field_audits_v1,
)
from src.alpha_foundry.activation.run_input_v1 import (
    ProductionActivationRunInputServiceV1,
    ResearchOnlyActivationRunInputBundleV1,
    ResearchOnlyActivationRunInputServiceV1,
)
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.retrieval.feature_source_v1 import TrainValidSnapshotServiceV1
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.alpha_quality.adapters.baostock_eligible_universe_v1 import (
    BaoStockAshareEligibleUniverseAdapterV1,
)
from src.alpha_quality.decision_v2 import DecisionEvidenceRepository
from src.alpha_quality.decision_v2.policy import DecisionV2Policy
from src.alpha_quality.decision_v2.source_v3 import QualityDecisionV3Service
from src.alpha_quality.evaluation_contract import (
    PIT_SCORECARD_POLICY_REFERENCES,
    ResolvedEvaluationContractServiceV1,
)
from src.alpha_quality.evaluation_registry_v1 import EvaluationPolicyRegistryServiceV1
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.pit_adapter_v1 import AsharePITAdapterRegistryV1
from src.alpha_quality.pit_artifact_v2 import FrozenAsharePITSnapshotArtifactStoreV2
from src.alpha_quality.pit_service_v2 import (
    AsharePITAdapterRegistrationServiceV1,
    AsharePITSnapshotServiceV2,
)
from src.alpha_quality.production_evaluator_v1 import ProductionEvaluationRequestV1
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.research_ledger.events import (
    EventDraft,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import AtomicContentAddressedArtifactWriter
from src.research_ledger.hash_utils import canonical_json_hash
from tests.alpha_quality.test_pit_service_v2 import ServiceFixturePITAdapterV1


def _hash(name: str) -> str:
    return canonical_json_hash({"research-only-activation-fixture": name})


def _factor_semantics() -> FactorSpecSemantics:
    return FactorSpecSemantics(
        transform_pipeline_hash=_hash("transform"),
        field_semantics={"close": "baostock-daily-best-effort"},
        signal_time="close-t",
        order_time="open-t+1",
        entry_price_time="open-t+1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=_hash("universe"),
        tradability_mask_hash=_hash("tradability"),
    )


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_PROCESS_MEMORY": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
            "VIBE_TRADING_DECISION_V2": "1",
        }
    )


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    flags = _flags()
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="research-only-activation-input-v1-test",
    )
    adapter = BaoStockAshareEligibleUniverseAdapterV1(
        client=object(),
        dataset_vintage="baostock-test-vintage",
        source_as_of="2025-01-25T00:00:00+08:00",
        artifact_writer=AtomicContentAddressedArtifactWriter(store.artifact_root),
        provider_version="0.9.3",
        _authority_capability=baostock_module._PRODUCTION_AUTHORITY_CAPABILITY,
    )
    fixture_adapter = ServiceFixturePITAdapterV1()
    monkeypatch.setattr(
        BaoStockAshareEligibleUniverseAdapterV1,
        "load",
        lambda self, request: fixture_adapter.load(request),
    )
    registry = AsharePITAdapterRegistryV1({adapter.descriptor().adapter_id: adapter})
    registration = AsharePITAdapterRegistrationServiceV1(
        store, flags=flags, registry=registry
    ).register(adapter_id=adapter.descriptor().adapter_id, run_id="provider-run")
    dates = tuple(f"2025-01-{day:02d}" for day in range(1, 25))
    policy = EvaluationPolicyRegistryServiceV1(store, flags=flags).register(
        dates=dates,
        return_horizons=(1,),
        execution_horizon=1,
        holding_period=1,
        rebalance_cadence=1,
        train=("2025-01-01", "2025-01-06"),
        valid=("2025-01-09", "2025-01-14"),
        test=("2025-01-17", "2025-01-22"),
        run_id="evaluation-run",
    )
    contract = ResolvedEvaluationContractServiceV1(store, flags=flags).register(
        run_id="evaluation-run",
        evaluation_policy_event_hash=policy.event.event_hash,
        profile_id="production_candidate",
        profile_version="1",
        policy_references=PIT_SCORECARD_POLICY_REFERENCES,
    )
    audit_service = ProviderPITAuditServiceV1(store)
    field_events = tuple(
        audit_service.record_field(
            run_id="provider-run",
            adapter_registration_event_hash=registration.event.event_hash,
            audit=audit,
        ).event
        for audit in baostock_golden_cohort_field_audits_v1(
            adapter_registration_event_hash=registration.event.event_hash,
            typed_receipt_hashes=(_hash("typed-receipt"),),
        )
    )
    interface_events = []
    for interface in sorted({event.payload["interface"] for event in field_events}):
        scoped = tuple(
            event for event in field_events if event.payload["interface"] == interface
        )
        interface_events.append(
            audit_service.record_interface(
                run_id="provider-run",
                adapter_registration_event_hash=registration.event.event_hash,
                field_audit_event_hashes=tuple(sorted(event.event_hash for event in scoped)),
                required_fields=tuple(sorted(event.payload["field_name"] for event in scoped)),
            ).event
        )
    authority = audit_service.decide_authority(
        run_id="provider-run",
        adapter_registration_event_hash=registration.event.event_hash,
        interface_audit_event_hashes=tuple(sorted(event.event_hash for event in interface_events)),
        required_interfaces=tuple(sorted(event.payload["interface"] for event in interface_events)),
    )
    snapshot = AsharePITSnapshotServiceV2(store, flags=flags, registry=registry).record(
        adapter_registration_event_hash=registration.event.event_hash,
        evaluation_policy_event_hash=policy.event.event_hash,
        run_id="evaluation-run",
    )
    source = FrozenAsharePITSnapshotArtifactStoreV2(store.artifact_root).read_bundle(
        snapshot.snapshot
    )
    panel = {
        **source.market_fields,
        "_meta": {
            "pit_contract_present": True,
            "survivorship_bias": False,
            "calendar": "SSE_SZSE_BAOSTOCK",
            "timezone": "Asia/Shanghai",
        },
    }
    train_valid = TrainValidSnapshotServiceV1(store, flags=flags).freeze(
        panel,
        universe="A_SHARE_ELIGIBLE_GOLDEN_COHORT_V1",
        period="2025-01-01/2025-01-14",
        source_config={"pit_snapshot_hash": snapshot.snapshot.snapshot_hash},
        run_id="train-valid-run",
    )
    bundle = ResearchOnlyActivationRunInputBundleV1.create(
        research_cycle_id="research-only-cycle",
        resolved_contract_event_hash=contract.event.event_hash,
        provider_authority_decision_event_hash=authority.event.event_hash,
        pit_snapshot_event_hash=snapshot.event.event_hash,
        train_valid_snapshot_event_hash=train_valid.event.event_hash,
        train_snapshot_hash=_hash("train-snapshot"),
        valid_snapshot_hash=_hash("valid-snapshot"),
        train_valid_split_plan_hash=policy.event.payload["split_plan_hash"],
        flat_policy_hash=FlatControlPolicyV1.create(
            max_candidates_per_seed=5, max_candidates=32, trial_budget=64
        ).policy_hash,
        topology_policy_hash=ActivationRetrieverPolicy().policy_hash,
        candidate_budget=32,
        compute_budget=64,
        source_watermark=store.query_events()[-1].event_hash,
        limitation_codes=(
            "BAOSTOCK_BEST_EFFORT_AUTHORITY",
            "RESEARCH_ONLY_EMPIRICAL_ACTIVATION",
        ),
    )
    return store, authority, snapshot, bundle


def test_baostock_best_effort_is_preserved_without_formal_eligibility(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, authority, snapshot, bundle = _setup(tmp_path, monkeypatch)
    recorded = ResearchOnlyActivationRunInputServiceV1(store).register(
        run_id="research-only-cycle", bundle=bundle
    )

    assert authority.decision.authority_status == "best_effort"
    assert authority.decision.activation_eligible is False
    assert snapshot.snapshot.derived_evidence["decision_grade"] is True
    assert recorded.payload["maximum_promotion"] == "research_only"
    assert recorded.payload["formal_activation_eligible"] is False
    assert recorded.payload["formal_readiness_effect"] == "none"
    assert recorded.payload["official_search_policy_effect"] == "none"
    assert recorded.payload["live_trading_meaning"] == "none"
    assert recorded.payload["test_final_forward_access_count"] == 0
    assert store.verify_chain()


def test_shared_research_bundle_sources_materialize_arm_terminal_dossier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, bundle = _setup(tmp_path, monkeypatch)
    registered = ResearchOnlyActivationRunInputServiceV1(store).register(
        run_id="research-only-cycle", bundle=bundle
    )
    arm_run_id = "research-only-shared-source-arm"
    arm_start_hash = _hash("shared-source-arm-start")
    store._append_producer_event(
        EventDraft(
            event_type="ProductionActivationArmStartedV1Recorded",
            entity_id="production-activation-arm-start-shared-source",
            run_id=arm_run_id,
            payload_schema_version="production_activation_arm_started_recorded.v1",
            idempotency_key="shared-source-arm-start",
            payload={
                "arm_start_id": "production-activation-arm-start-shared-source",
                "arm_start_hash": arm_start_hash,
                "run_input_bundle_event_hash": registered.event_hash,
                "plan_hash": _hash("shared-source-plan"),
                "pair_id": "shared-source-pair",
                "run_group_id": "shared-source-group",
                "arm": "flat",
                "retriever_policy_hash": bundle.flat_policy_hash,
                "retrieval_authority_event_hash": _hash("shared-source-retrieval"),
            },
        )
    )
    factory = ProductionActivationCandidateFactoryV1(
        store=store,
        quality_decision_v3=QualityDecisionV3Service(
            store=store,
            flags=store.flags,
            policy=DecisionV2Policy(
                schema_version="decision_v2_policy.v1",
                policy_version="shared-research-source-test",
            ),
            repository=DecisionEvidenceRepository(
                store.artifact_root / "decision-evidence-v2"
            ),
        ),
    )
    trial_id = "shared-research-source-trial"
    identity = factory.record_generated_identity(
        candidate=make_candidate(
            "shared-source-parent", "rank(close)", mutation="rank_wrap"
        ),
        semantics=_factor_semantics(),
        trial_id=trial_id,
        run_id=arm_run_id,
    )
    definition = next(
        event
        for event in store.query_events(event_type="FactorDefinitionRecorded")
        if event.entity_id == identity.factor_spec_id and event.run_id == arm_run_id
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id=arm_run_id,
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    contract = next(
        event
        for event in store.query_events(event_type="ResolvedEvaluationContractRegistered")
        if event.event_hash == bundle.resolved_contract_event_hash
    )

    refs = factory.evaluate_recorded_candidate(
        request=ProductionEvaluationRequestV1(
            run_id=arm_run_id,
            trial_id=trial_id,
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=str(contract.payload["contract_hash"]),
            snapshot_event_hash=bundle.pit_snapshot_event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
    )

    assert refs.terminal_dossier_event_hash is not None
    assert any(
        event.event_hash == refs.terminal_dossier_event_hash
        for event in store.query_events(event_type="TrialTerminalDossierRecorded")
    )
    assert not store.query_events(event_type="ReportMaterializationFailed")
    assert store.verify_chain()


def test_research_only_ceiling_cannot_be_replaced_or_caller_overridden() -> None:
    values = {
        "research_cycle_id": "research-only-cycle",
        "resolved_contract_event_hash": _hash("contract-event"),
        "provider_authority_decision_event_hash": _hash("provider-event"),
        "pit_snapshot_event_hash": _hash("pit-event"),
        "train_valid_snapshot_event_hash": _hash("train-valid-event"),
        "train_snapshot_hash": _hash("train"),
        "valid_snapshot_hash": _hash("valid"),
        "train_valid_split_plan_hash": _hash("split"),
        "flat_policy_hash": _hash("flat"),
        "topology_policy_hash": _hash("topology"),
        "candidate_budget": 32,
        "compute_budget": 64,
        "source_watermark": _hash("watermark"),
        "limitation_codes": ("RESEARCH_ONLY",),
    }
    expected = ResearchOnlyActivationRunInputBundleV1.create(**values)
    attempted = ResearchOnlyActivationRunInputBundleV1.create(
        **values,
        maximum_promotion="paper_candidate",
        formal_activation_eligible=True,
        formal_readiness_effect="approved",
        official_search_policy_effect="activate",
        live_trading_meaning="approved",
        test_final_forward_access_count=99,
    )

    assert attempted == expected
    with pytest.raises(TypeError):
        replace(expected, formal_activation_eligible=True)
    with pytest.raises(ValueError, match="hash mismatch"):
        replace(expected, bundle_hash=_hash("forged"))


def test_forged_research_only_event_cannot_raise_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, bundle = _setup(tmp_path, monkeypatch)
    good = ResearchOnlyActivationRunInputServiceV1(store).register(
        run_id="research-only-cycle", bundle=bundle
    )
    forged = {**good.payload, "formal_activation_eligible": True}

    with pytest.raises(EventValidationError, match="ceiling differs"):
        store._append_producer_event(
            EventDraft(
                event_type="ResearchOnlyActivationRunInputRegistered",
                entity_id="forged-research-only-input",
                run_id="forged-run",
                payload_schema_version="research_only_activation_run_input_registered.v1",
                payload=forged,
            )
        )
    with pytest.raises(EventValidationError, match="payload schema version"):
        store._append_producer_event(
            EventDraft(
                event_type="ResearchOnlyActivationRunInputRegistered",
                entity_id="forged-research-only-version",
                run_id="forged-run",
                payload_schema_version="production_activation_run_input_registered.v1",
                payload=good.payload,
            )
        )


def test_formal_v1_gates_are_unchanged_and_research_bundle_is_distinct() -> None:
    formal_source = ProductionActivationRunInputServiceV1.register_bundle.__code__
    names = set(formal_source.co_names)
    constants = set(formal_source.co_consts)

    assert "activation_eligible" in constants
    assert "decision_grade" in constants
    assert "ProductionActivationRunInputBundleV1Registered" in constants
    assert "ResearchOnlyActivationRunInputBundleV1" not in names


def test_existing_candidate_factory_reopens_research_only_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, _, _, bundle = _setup(tmp_path, monkeypatch)
    event = ResearchOnlyActivationRunInputServiceV1(store).register(
        run_id="research-only-cycle", bundle=bundle
    )
    service = QualityDecisionV3Service(
        store=store,
        flags=store.flags,
        policy=DecisionV2Policy(
            schema_version="decision_v2_policy.v1",
            policy_version="research-only-activation-test",
        ),
        repository=DecisionEvidenceRepository(store.artifact_root / "decision-evidence-v2"),
    )
    reopened, reopened_event = ProductionActivationCandidateFactoryV1(
        store=store, quality_decision_v3=service
    )._bundle(event.event_hash)

    assert reopened == bundle
    assert reopened_event.event_hash == event.event_hash
