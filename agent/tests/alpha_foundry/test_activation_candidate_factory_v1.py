from __future__ import annotations

from dataclasses import fields
import inspect
from pathlib import Path

import pytest

from src.alpha_foundry.activation.candidate_factory_v1 import (
    ProductionActivationCandidateFactoryV1,
    ProductionActivationCandidateRefsV1,
    ProductionActivationFactoryArmRequestV1,
    ProductionActivationFactoryArmResultV1,
)
from src.alpha_foundry.candidate_pool import make_candidate
from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.search import AlphaFoundrySearch
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.alpha_quality.decision_v2 import DecisionEvidenceRepository
from src.alpha_quality.decision_v2.policy import DecisionV2Policy
from src.alpha_quality.decision_v2.source_v3 import QualityDecisionV3Service
from src.alpha_quality.evaluator_dag_v1 import (
    ProductionCandidateDAGEvaluatorFactoryV1,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionCandidateEvaluatorV1,
    ProductionEvaluationRequestV1,
    TrialTerminalDossierArtifactStoreV1,
)
from src.research_ledger.events import ResearchEventStore
from src.research_ledger.hash_utils import canonical_json_hash
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _hash(name: str) -> str:
    return canonical_json_hash({"activation-factory-fixture": name})


def _store(tmp_path: Path) -> ResearchEventStore:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_ALPHA_SCORECARD": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_TOPOLOGY_RETRIEVER": "1",
            "VIBE_TRADING_DECISION_V2": "1",
        }
    )
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="activation-candidate-factory-v1-test",
    )


def _service(store: ResearchEventStore, tmp_path: Path) -> QualityDecisionV3Service:
    return QualityDecisionV3Service(
        store=store,
        flags=store.flags,
        policy=DecisionV2Policy(
            schema_version="decision_v2_policy.v1",
            policy_version="activation-factory-v1-test",
        ),
        repository=DecisionEvidenceRepository(
            store.artifact_root / "decision-evidence-v2"
        ),
    )


def _factory(tmp_path: Path) -> ProductionActivationCandidateFactoryV1:
    store = _store(tmp_path)
    return ProductionActivationCandidateFactoryV1(
        store=store,
        quality_decision_v3=_service(store, tmp_path),
    )


def _semantics() -> FactorSpecSemantics:
    return FactorSpecSemantics(
        transform_pipeline_hash=_hash("transform"),
        field_semantics={"close": "pit-adjusted-close"},
        signal_time="close-t",
        order_time="open-t+1",
        entry_price_time="open-t+1",
        execution_lag=1,
        return_horizon=1,
        universe_mask_hash=_hash("universe"),
        tradability_mask_hash=_hash("tradability"),
    )


def test_activation_factory_calls_existing_production_evaluator(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    serial_create = ProductionCandidateEvaluatorFactoryV1.create
    calls = 0

    def spy(store: object) -> ProductionCandidateEvaluatorV1:
        nonlocal calls
        calls += 1
        return serial_create(store)

    monkeypatch.setattr(
        ProductionCandidateEvaluatorFactoryV1, "create", staticmethod(spy)
    )
    factory = _factory(tmp_path)

    assert calls == 1
    assert factory.evaluator.serial.__class__ is ProductionCandidateEvaluatorV1


def test_activation_factory_calls_existing_evaluator_dag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dag_create = ProductionCandidateDAGEvaluatorFactoryV1.create
    calls = 0

    def spy(store: object):
        nonlocal calls
        calls += 1
        return dag_create(store)

    monkeypatch.setattr(
        ProductionCandidateDAGEvaluatorFactoryV1, "create", staticmethod(spy)
    )
    factory = _factory(tmp_path)

    assert calls == 1
    assert factory.dag_evaluator_factory is ProductionCandidateDAGEvaluatorFactoryV1


def test_activation_uses_existing_factor_identity_service(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    candidate = make_candidate("parent", "rank(close)", mutation="rank_wrap")

    result = factory.record_generated_identity(
        candidate=candidate,
        semantics=_semantics(),
        trial_id="activation-factory-trial-1",
        run_id="activation-factory-run-1",
    )

    assert isinstance(factory.identity, FactorIdentityService)
    assert result.status == "recorded"
    assert factory.store.query_events(event_type="FactorDefinitionRecorded")


def test_activation_uses_existing_quality_decision_v3() -> None:
    source = inspect.getsource(
        ProductionActivationCandidateFactoryV1.evaluate_recorded_candidate
    )

    assert "self.quality_decision_v3.decide_and_record(" in source
    assert (
        ProductionActivationCandidateFactoryV1.quality_decision_service_class
        is QualityDecisionV3Service
    )
    assert "decision_evidence_refs" not in inspect.signature(
        ProductionActivationCandidateFactoryV1.evaluate_recorded_candidate
    ).parameters


def test_activation_uses_existing_terminal_and_dossier_builders() -> None:
    source = inspect.getsource(
        ProductionActivationCandidateFactoryV1.evaluate_recorded_candidate
    )

    assert "self.evaluator.evaluate(request).authoritative_result" in source
    assert "TrialTerminalDossierRecorded" in source
    assert TrialTerminalDossierArtifactStoreV1.__module__ == (
        "src.alpha_quality.production_evaluator_v1"
    )
    assert not hasattr(ProductionActivationCandidateFactoryV1, "dossier_writer")


def test_factory_binding_exposes_unresolved_production_authority_blockers(
    tmp_path: Path,
) -> None:
    factory = _factory(tmp_path)

    assert factory._compatibility_blockers() == (
        "IDENTITY_TERMINAL_DOSSIER_PRODUCER_UNAVAILABLE",
        "QUALITY_DECISION_V3_PRODUCER_AUTHORITY_INCOMPLETE",
    )


def test_factory_real_dag_evaluator_decision_and_dossier_chain(
    tmp_path: Path,
) -> None:
    flags, store, contract, snapshot, definition = _setup(
        tmp_path,
        enable_decision=True,
    )
    service = QualityDecisionV3Service(
        store=store,
        flags=flags,
        policy=DecisionV2Policy(
            schema_version="decision_v2_policy.v1",
            policy_version="activation-factory-real-chain-v1",
        ),
        repository=DecisionEvidenceRepository(
            store.artifact_root / "decision-evidence-v2"
        ),
    )
    factory = ProductionActivationCandidateFactoryV1(
        store=store,
        quality_decision_v3=service,
    )
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
        frozen_comparison_pool_hash=None,
    )

    refs = factory.evaluate_recorded_candidate(request=request)

    terminal = next(
        event
        for event in store.query_events(event_type="TrialTerminated")
        if event.event_hash == refs.trial_terminal_event_hash
    )
    decision = next(
        event
        for event in store.query_events(event_type="QualityDecisionV3Recorded")
        if event.event_hash == refs.quality_decision_v3_event_hash
    )
    dossier = next(
        event
        for event in store.query_events(event_type="TrialTerminalDossierRecorded")
        if event.event_hash == refs.terminal_dossier_event_hash
    )
    assert terminal.payload["evaluation_event_hash"] == refs.evaluation_event_hash
    assert decision.payload["factor_spec_id"] == definition.entity_id
    assert decision.payload["decision"] == "research_only"
    assert dossier.payload["terminal_event_hash"] == terminal.event_hash
    assert store.query_events(event_type="ProductionEvaluationNodeRecorded")
    assert store.verify_chain()


def test_flat_and_topology_share_generator_factory(tmp_path: Path) -> None:
    factory = _factory(tmp_path)
    seeds = SeedBank([AlphaSeed("seed", "close", "fixture")])

    flat = factory.build_generator(
        seed_bank=seeds,
        mutator=SeedMutator(max_candidates_per_seed=1),
        candidate_budget=1,
        compute_budget=1,
    )
    topology = factory.build_generator(
        seed_bank=seeds,
        mutator=SeedMutator(max_candidates_per_seed=1),
        candidate_budget=1,
        compute_budget=1,
    )

    assert type(flat) is type(topology) is AlphaFoundrySearch
    assert factory.generator_factory is AlphaFoundrySearch


def test_flat_and_topology_use_same_factory() -> None:
    assert ProductionActivationCandidateFactoryV1.__name__ == (
        "ProductionActivationCandidateFactoryV1"
    )
    assert not hasattr(ProductionActivationCandidateFactoryV1, "flat_factory")
    assert not hasattr(ProductionActivationCandidateFactoryV1, "topology_factory")


def test_flat_and_topology_share_evaluator_factory(tmp_path: Path) -> None:
    factory = _factory(tmp_path)

    assert factory.serial_evaluator_factory is ProductionCandidateEvaluatorFactoryV1
    assert factory.dag_evaluator_factory is ProductionCandidateDAGEvaluatorFactoryV1
    assert not hasattr(factory, "flat_evaluator")
    assert not hasattr(factory, "topology_evaluator")


def test_flat_and_topology_share_contract_snapshot_and_budget() -> None:
    request_fields = {field.name for field in fields(ProductionActivationFactoryArmRequestV1)}

    assert "run_input_bundle_event_hash" in request_fields
    assert request_fields.isdisjoint(
        {
            "contract_hash",
            "snapshot_hash",
            "candidate_budget",
            "compute_budget",
            "generator_factory",
            "evaluator_factory",
        }
    )


def test_only_retriever_policy_differs() -> None:
    request_fields = {field.name for field in fields(ProductionActivationFactoryArmRequestV1)}

    assert "retriever_policy_hash" in request_fields
    assert request_fields.isdisjoint(
        {
            "flat_generator",
            "topology_generator",
            "flat_evaluator",
            "topology_evaluator",
            "flat_contract_hash",
            "topology_contract_hash",
        }
    )


def test_only_retriever_policy_differs_between_arms() -> None:
    test_only_retriever_policy_differs()


def test_factory_returns_refs_not_metrics() -> None:
    public_result_fields = {
        field.name for field in fields(ProductionActivationFactoryArmResultV1)
    }
    candidate_ref_fields = {
        field.name for field in fields(ProductionActivationCandidateRefsV1)
    }
    forbidden = {"ic", "yield", "score", "decision", "success_count", "metrics"}

    assert public_result_fields == {
        "schema_version",
        "arm_started_event_hash",
        "retriever_decision_event_refs",
        "trial_terminal_event_refs",
        "quality_decision_event_refs",
        "resource_artifact_ref",
        "arm_completed_event_hash",
    }
    assert public_result_fields.isdisjoint(forbidden)
    assert candidate_ref_fields.isdisjoint(forbidden)


def test_factory_rejects_callable_import_path_formula_and_decision_truth() -> None:
    base = {
        "schema_version": "production_activation_factory_arm_request.v1",
        "run_input_bundle_event_hash": _hash("bundle-event"),
        "plan_hash": _hash("plan"),
        "pair_id": "pair-1",
        "run_group_id": "group-1",
        "execution_run_id": "execution-1",
        "arm": "flat",
        "retriever_policy_hash": _hash("flat-policy"),
        "retrieval_authority_event_hash": _hash("retrieval"),
        "resource_artifact_ref": {
            "relative_path": "resource/a.json",
            "artifact_hash": _hash("resource"),
            "media_type": "application/json",
        },
    }
    for forbidden in ("callable", "import_path", "formula", "decision", "score"):
        with pytest.raises(ValueError, match="caller truth"):
            ProductionActivationFactoryArmRequestV1.from_mapping(
                {**base, forbidden: "forbidden"}
            )


def test_no_activation_specific_scorecard_exists() -> None:
    import src.alpha_foundry.activation.candidate_factory_v1 as module

    names = set(vars(module))
    assert not {name for name in names if "Scorecard" in name}


def test_no_activation_specific_execution_engine_exists() -> None:
    import src.alpha_foundry.activation.candidate_factory_v1 as module

    names = set(vars(module))
    assert not {name for name in names if "ExecutionEngine" in name}


def test_no_activation_specific_decision_runner_exists() -> None:
    import src.alpha_foundry.activation.candidate_factory_v1 as module

    names = set(vars(module))
    assert not {name for name in names if "DecisionRunner" in name}


def test_no_second_ledger_or_artifact_repository_created() -> None:
    import src.alpha_foundry.activation.candidate_factory_v1 as module

    names = set(vars(module))
    assert "ResearchEventStore" in names
    assert not {
        name
        for name in names
        if name.endswith("Ledger") or name.endswith("ArtifactRepository")
    }
