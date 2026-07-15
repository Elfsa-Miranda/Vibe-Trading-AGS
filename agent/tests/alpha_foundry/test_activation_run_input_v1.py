from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.activation.run_input_v1 import (
    PRODUCTION_DAG_POLICY_HASH,
    PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH,
    PRODUCTION_GENERATOR_MANIFEST_HASH,
    ProductionActivationRunInputBundleV1,
    ProductionActivationRunInputServiceV1,
)
from src.alpha_foundry.dsl.executable import DEFAULT_EXECUTABLE_GRAMMAR
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import EventDraft, EventTransitionError, ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _store(tmp_path: Path) -> ResearchEventStore:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
        }
    )
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="activation-input-v1-test",
    )


def _blocked_provider_decision(store: ResearchEventStore):
    decision_hash = _hash("provider-decision")
    decision_id = "provider-authority-" + decision_hash[-24:]
    return store._append_producer_event(
        EventDraft(
            event_type="ProviderAuthorityDecisionV1Recorded",
            entity_id=decision_id,
            run_id="activation-cycle",
            payload_schema_version="provider_authority_decision_recorded.v1",
            payload={
                "decision_id": decision_id,
                "provider": "fixture-provider",
                "adapter_id": "fixture-adapter",
                "adapter_registration_event_hash": _hash("registration"),
                "authority_status": "blocked",
                "claim_scope_ceiling": "best_effort",
                "activation_eligible": False,
                "decision_hash": decision_hash,
                "interface_audit_event_hashes": [_hash("interface-audit")],
                "canonical_hash_spec": {"spec_hash": _hash("hash-spec")},
                "decision": {"schema_version": "provider_authority_decision.v1"},
            },
        )
    )


def _bundle(provider_event_hash: str) -> ProductionActivationRunInputBundleV1:
    return ProductionActivationRunInputBundleV1.create(
        research_cycle_id="activation-cycle",
        resolved_contract_event_hash=_hash("contract-event"),
        resolved_contract_hash=_hash("contract"),
        provider_authority_decision_event_hash=provider_event_hash,
        provider_authority_decision_hash=_hash("provider-decision"),
        golden_slice_readiness_event_hash=_hash("golden-event"),
        pit_snapshot_event_hash=_hash("snapshot-event"),
        pit_snapshot_hash=_hash("snapshot"),
        train_valid_split_plan_hash=_hash("split"),
        flat_policy_hash=_hash("flat"),
        topology_policy_hash=_hash("topology"),
        candidate_budget=32,
        compute_budget=64,
        source_watermark=_hash("watermark"),
        artifact_namespace="activation-cycle-pair",
    )


def test_production_input_bundle_contains_refs_not_metrics_or_dataframes() -> None:
    fields = set(ProductionActivationRunInputBundleV1.__dataclass_fields__)
    forbidden = {
        "dataframe",
        "ic",
        "score",
        "decision",
        "success_count",
        "test_event_hash",
        "final_event_hash",
        "forward_event_hash",
    }

    assert not fields.intersection(forbidden)
    assert {
        "resolved_contract_event_hash",
        "provider_authority_decision_event_hash",
        "pit_snapshot_event_hash",
        "source_watermark",
    }.issubset(fields)


def test_bundle_binds_existing_generator_grammar_evaluator_and_dag() -> None:
    bundle = _bundle(_hash("provider-event"))

    assert bundle.generator_manifest_hash == PRODUCTION_GENERATOR_MANIFEST_HASH
    assert bundle.grammar_hash == DEFAULT_EXECUTABLE_GRAMMAR.snapshot_hash
    assert (
        bundle.evaluator_factory_manifest_hash
        == PRODUCTION_EVALUATOR_FACTORY_MANIFEST_HASH
    )
    assert bundle.dag_policy_hash == PRODUCTION_DAG_POLICY_HASH


def test_blocked_provider_produces_typed_unavailable_golden_slice(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    decision = _blocked_provider_decision(store)

    recorded = ProductionActivationRunInputServiceV1(store).assess_golden_slice(
        run_id="activation-cycle",
        research_cycle_id="activation-cycle",
        provider_authority_decision_event_hash=decision.event_hash,
    )

    assert recorded.readiness.ready is False
    assert "PROVIDER_FIELD_PIT_AUDIT_INSUFFICIENT" in recorded.readiness.blocker_codes
    assert "PRODUCTION_GOLDEN_SLICE_SNAPSHOT_UNAVAILABLE" in (
        recorded.readiness.blocker_codes
    )
    assert recorded.event.event_type == "ProductionGoldenSliceReadinessV1Recorded"
    assert store.verify_chain()


def test_blocked_provider_cannot_register_formal_activation_inputs(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    decision = _blocked_provider_decision(store)

    with pytest.raises(EventTransitionError, match="provider authority is blocked"):
        ProductionActivationRunInputServiceV1(store).register_bundle(
            run_id="activation-cycle",
            bundle=_bundle(decision.event_hash),
        )
    assert not store.query_events(
        event_type="ProductionActivationRunInputBundleV1Registered"
    )


def test_golden_slice_verifier_accepts_only_exact_event_sources(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(EventTransitionError, match="provider authority decision"):
        ProductionActivationRunInputServiceV1(store).assess_golden_slice(
            run_id="activation-cycle",
            research_cycle_id="activation-cycle",
            provider_authority_decision_event_hash=_hash("missing"),
        )
