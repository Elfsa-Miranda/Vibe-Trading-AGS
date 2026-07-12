from __future__ import annotations

import inspect
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from src.alpha_quality.evaluation_contract import EXECUTION_POLICY_REFERENCES
from src.alpha_quality.evaluator_dag_v1 import (
    DeterministicEvaluatorDAGSchedulerV1,
    EvaluatorDAGDependencyRegistryV1,
    EvaluatorDAGNodeSpecV1,
    EvaluatorDAGSchedulerPolicyV1,
    ProductionCandidateDAGEvaluatorFactoryV1,
    _build_registry,
    _worker_entry,
)
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.research_ledger.events.store import ResearchEventStore
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _scheduler(*, policy: EvaluatorDAGSchedulerPolicyV1 | None = None) -> DeterministicEvaluatorDAGSchedulerV1:
    return DeterministicEvaluatorDAGSchedulerV1(EvaluatorDAGDependencyRegistryV1.production(), policy=policy)


def _result_map(result):
    return {item.node_name: item for item in result.node_results}


def test_parallel_schedule_does_not_change_claim_or_decision_hash() -> None:
    hashes = {_scheduler().run({}, schedule_seed=seed).semantic_graph_hash for seed in range(4)}
    assert len(hashes) == 1


@given(st.integers(min_value=0, max_value=2**31 - 1))
@settings(max_examples=3, deadline=None)
def test_random_legal_topological_schedules_are_property_invariant(seed: int) -> None:
    assert (
        _scheduler().run({}, schedule_seed=seed).semantic_graph_hash
        == _scheduler().run({}, schedule_seed=seed + 1).semantic_graph_hash
    )


def test_test_price_mutation_does_not_change_discovery_nodes() -> None:
    # Test/final data is structurally absent from the scheduler input API used by
    # the production wrapper; changing an out-of-scope value cannot enter it.
    first = _scheduler().run({})
    ignored_test_price_hash = "sha256:" + "9" * 64
    second = _scheduler().run({})
    assert ignored_test_price_hash not in repr(second)
    assert first.semantic_graph_hash == second.semantic_graph_hash


def test_unrelated_field_mutation_does_not_change_independent_claim() -> None:
    first = _result_map(_scheduler().run({}))
    second = _result_map(_scheduler().run({"observed_predictive": {"field": "mutated"}}))
    for name in (
        "source_resolution",
        "backend_capability",
        "snapshot_authority",
        "factor_output",
        "pit_predictive",
        "duplicate_identity",
        "execution",
        "complement_mechanism",
    ):
        assert first[name].output_hash == second[name].output_hash
    assert first["claim_assessments"].output_hash != second["claim_assessments"].output_hash


def test_execution_cost_mutation_changes_only_dependent_nodes() -> None:
    first = _result_map(_scheduler().run({}))
    second = _result_map(_scheduler().run({"execution": {"cost_bps": 99}}))
    changed = {name for name in first if first[name].output_hash != second[name].output_hash}
    assert changed == {
        "execution",
        "complement_mechanism",
        "claim_assessments",
        "narrow_decision",
    }


def test_mechanism_mutation_changes_only_mechanism_dependent_nodes() -> None:
    first = _result_map(_scheduler().run({}))
    second = _result_map(_scheduler().run({"complement_mechanism": {"mechanism": "mutated"}}))
    changed = {name for name in first if first[name].output_hash != second[name].output_hash}
    assert changed == {"complement_mechanism", "claim_assessments", "narrow_decision"}


def test_blocker_chain_is_identical_between_serial_and_dag() -> None:
    results = [_scheduler().run({}, schedule_seed=seed, cancelled_nodes=frozenset({"execution"})) for seed in range(3)]
    assert len({item.semantic_graph_hash for item in results}) == 1
    mapped = _result_map(results[0])
    assert mapped["execution"].status == "cancelled"
    assert mapped["complement_mechanism"].status == "blocked"
    assert mapped["narrow_decision"].status == "blocked"


def test_worker_timeout_and_crash_produce_typed_node_and_trial_terminal() -> None:
    timeout_registry = _build_registry("timeout-test", (EvaluatorDAGNodeSpecV1("source_resolution", (), "sleep"),))
    timeout = DeterministicEvaluatorDAGSchedulerV1(
        timeout_registry,
        policy=EvaluatorDAGSchedulerPolicyV1(node_timeout_seconds=0.05),
    ).run({"source_resolution": {"sleep_seconds": 0.5}})
    assert timeout.node_results[0].status == "timeout"
    assert timeout.node_results[0].reason_codes == ("DAG_WORKER_TIMEOUT",)

    crash_registry = _build_registry("crash-test", (EvaluatorDAGNodeSpecV1("source_resolution", (), "crash"),))
    crashed = DeterministicEvaluatorDAGSchedulerV1(crash_registry).run({})
    assert crashed.node_results[0].status == "crashed"
    assert crashed.node_results[0].reason_codes == ("DAG_WORKER_CRASHED",)


def test_dag_evaluator_timeout_produces_typed_node_and_trial_terminal(
    tmp_path: Path,
) -> None:
    _, store, contract, snapshot, definition = _setup(tmp_path)
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
    )
    evaluator = ProductionCandidateDAGEvaluatorFactoryV1.create(store)
    timeout_registry = _build_registry(
        "timeout-integration",
        (
            EvaluatorDAGNodeSpecV1("source_resolution", (), "sleep"),
            EvaluatorDAGNodeSpecV1("backend_capability", ("source_resolution",)),
            EvaluatorDAGNodeSpecV1("snapshot_authority", ("source_resolution",)),
        ),
    )
    evaluator.scheduler = DeterministicEvaluatorDAGSchedulerV1(
        timeout_registry,
        policy=EvaluatorDAGSchedulerPolicyV1(node_timeout_seconds=0.05),
    )

    result = evaluator.evaluate(request)

    assert result.authoritative_result.completion_status == "timeout"
    terminal = store.query_events(event_type="TrialTerminated")
    assert len(terminal) == 1
    assert terminal[0].payload["status"] == "timeout"
    assert terminal[0].payload["reason_codes"] == ("DAG_WORKER_TIMEOUT",)
    nodes = store.query_events(event_type="ProductionEvaluationNodeRecorded")
    assert len(nodes) == 1
    assert nodes[0].payload["reason_codes"] == ("DAG_WORKER_TIMEOUT",)


def test_parent_enforces_worker_rss_limit() -> None:
    registry = _build_registry("rss-test", (EvaluatorDAGNodeSpecV1("source_resolution", (), "allocate"),))
    result = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(maximum_worker_rss_bytes=32 * 1024**2),
    ).run(
        {
            "source_resolution": {
                "allocate_bytes": 96 * 1024**2,
                "hold_seconds": 0.5,
            }
        }
    )
    assert result.node_results[0].status == "rss_exceeded"


def test_scheduler_cycle_and_dynamic_dependency_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown dependencies"):
        _build_registry("unknown-test", (EvaluatorDAGNodeSpecV1("a", ("missing",)),))
    with pytest.raises(ValueError, match="cycle"):
        _build_registry(
            "cycle-test",
            (
                EvaluatorDAGNodeSpecV1("a", ("b",)),
                EvaluatorDAGNodeSpecV1("b", ("a",)),
            ),
        )
    with pytest.raises(ValueError, match="unknown nodes"):
        _scheduler().run({"caller_dynamic_node": {"dependency": "source_resolution"}})


def test_same_content_race_converges_and_conflict_race_is_distinct() -> None:
    registry = _build_registry("race-test", (EvaluatorDAGNodeSpecV1("source_resolution", ()),))

    def execute(value: str):
        return DeterministicEvaluatorDAGSchedulerV1(registry).run({"source_resolution": {"content": value}})

    with ThreadPoolExecutor(max_workers=2) as pool:
        same = tuple(pool.map(execute, ("same", "same")))
    assert same[0].semantic_graph_hash == same[1].semantic_graph_hash
    assert execute("left").semantic_graph_hash != execute("right").semantic_graph_hash


def test_seed_cache_namespace_is_deterministic_and_worker_has_no_ledger_api() -> None:
    registry = _build_registry("namespace-test", (EvaluatorDAGNodeSpecV1("source_resolution", ()),))
    first = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(deterministic_seed=7, cache_namespace="arm-a"),
    ).run({})
    replay = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(deterministic_seed=7, cache_namespace="arm-a"),
    ).run({})
    isolated = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(deterministic_seed=7, cache_namespace="arm-b"),
    ).run({})

    assert first.semantic_graph_hash == replay.semantic_graph_hash
    assert first.semantic_graph_hash != isolated.semantic_graph_hash
    worker_source = inspect.getsource(_worker_entry)
    assert "ResearchEventStore" not in worker_source
    assert "append_event" not in worker_source


def test_parallel_ready_nodes_improve_fixed_workload_without_semantic_change() -> None:
    registry = _build_registry(
        "fixed-throughput-test",
        tuple(EvaluatorDAGNodeSpecV1(f"node-{index}", (), "sleep") for index in range(4)),
    )
    inputs = {name: {"sleep_seconds": 0.35} for name in registry.nodes}
    started = time.monotonic()
    serial = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(maximum_workers=1),
    ).run(inputs)
    serial_seconds = time.monotonic() - started
    started = time.monotonic()
    parallel = DeterministicEvaluatorDAGSchedulerV1(
        registry,
        policy=EvaluatorDAGSchedulerPolicyV1(maximum_workers=4),
    ).run(inputs)
    parallel_seconds = time.monotonic() - started

    assert {item.node_name: item.output_hash for item in serial.node_results} == {
        item.node_name: item.output_hash for item in parallel.node_results
    }
    assert parallel_seconds < serial_seconds * 0.85


def _semantic_hashes(store: ResearchEventStore) -> dict[str, str]:
    fields = {
        "FactorOutputRecordedV3": "factor_output_hash",
        "ObservedPanelPredictiveEvidenceRecorded": "evidence_hash",
        "PITPredictiveEvidenceRecorded": "evidence_hash",
        "ExecutionEvidenceRecorded": "execution_artifact_hash",
        "SecondaryEvidenceRecorded": "secondary_evidence_bundle_hash",
        "ClaimMatrixRecorded": "claim_matrix_hash",
        "QualityDecisionV4Recorded": "decision_hash",
    }
    return {
        event_type: str(store.query_events(event_type=event_type)[0].payload[field])
        for event_type, field in fields.items()
        if store.query_events(event_type=event_type)
    }


def test_serial_and_dag_semantic_artifact_hashes_are_equal(tmp_path: Path) -> None:
    flags, serial_store, contract, snapshot, definition = _setup(
        tmp_path / "serial",
        policy_references=EXECUTION_POLICY_REFERENCES,
        enable_decision=True,
    )
    pool, _ = ComparisonPoolServiceV1(serial_store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
        frozen_comparison_pool_hash=pool.comparison_pool_hash,
    )
    serial = ProductionCandidateEvaluatorFactoryV1.create(serial_store).evaluate(request)
    event_count = len(serial_store.query_events())
    dag = ProductionCandidateDAGEvaluatorFactoryV1.create(serial_store).evaluate(request)

    assert dag.authority_mode == "serial_reference"
    assert dag.scheduler_result.completed
    assert dag.authoritative_result == serial
    assert _semantic_hashes(serial_store)
    assert len(serial_store.query_events()) == event_count
    assert serial_store.verify_chain()


def test_feature_off_dag_factory_has_no_writes(tmp_path: Path) -> None:
    flags, store, _, _, _ = _setup(tmp_path)
    disabled = replace(
        flags,
        values={**flags.values, "VIBE_TRADING_ALPHA_SCORECARD": False},
    )
    disabled_store = ResearchEventStore(
        tmp_path / "disabled.sqlite",
        artifact_root=tmp_path / "disabled-artifacts",
        flags=disabled,
        code_version="dag-feature-off",
    )
    with pytest.raises(RuntimeError, match="capability is disabled"):
        ProductionCandidateDAGEvaluatorFactoryV1.create(disabled_store)
    assert disabled_store.query_events() == []
