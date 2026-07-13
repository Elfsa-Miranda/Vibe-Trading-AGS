from __future__ import annotations

import inspect
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_quality.predictive_evidence_v4 import (
    PITPredictiveEvidenceServiceV4,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.alpha_quality.production_evaluator_v1 import (
    PRODUCTION_NODE_EVENT_TYPE,
    REPORT_FAILURE_EVENT_TYPE,
    TERMINAL_DOSSIER_EVENT_TYPE,
    ProductionCandidateEvaluatorFactoryV1,
    ProductionCandidateEvaluatorV1,
    ProductionEvaluationNodeV1,
    ProductionEvaluationRequestV1,
    ProductionProviderUnavailable,
)
from src.research_ledger.events import EventDraft, EventTransitionError
from src.research_ledger.events.model import EventValidationError
from src.research_ledger.events.store import ResearchEventStore
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _prepared(tmp_path: Path):
    flags, store, contract, snapshot, definition = _setup(tmp_path)
    request = ProductionEvaluationRequestV1(
        run_id="predictive-run",
        trial_id="predictive-trial",
        factor_definition_event_hash=definition.event_hash,
        resolved_contract_hash=contract.contract.contract_hash,
        snapshot_event_hash=snapshot.event.event_hash,
        source_watermark_event_hash=definition.event_hash,
        frozen_comparison_pool_hash=None,
    )
    evaluator = ProductionCandidateEvaluatorFactoryV1.create(store)
    return flags, store, request, evaluator


def test_production_evaluator_accepts_only_closed_refs(tmp_path: Path) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    with pytest.raises(TypeError, match="closed request refs"):
        evaluator.evaluate(  # type: ignore[arg-type]
            {name: getattr(request, name) for name in request.__dataclass_fields__}
        )
    raw = {name: getattr(request, name) for name in request.__dataclass_fields__}
    with pytest.raises(ValueError, match="schema is closed"):
        ProductionEvaluationRequestV1.from_mapping({**raw, "decision": "candidate_zoo"})
    parameters = set(inspect.signature(evaluator.evaluate).parameters)
    assert parameters == {"request"}
    with pytest.raises(TypeError, match="factory"):
        ProductionCandidateEvaluatorV1(store, _token=object())  # type: ignore[arg-type]


def test_subproducer_cannot_return_decision_truth() -> None:
    fields = set(ProductionEvaluationNodeV1.__dataclass_fields__)
    parameters = set(
        inspect.signature(PITPredictiveEvidenceServiceV4.record).parameters
    )
    assert fields.isdisjoint({"decision", "score", "caps", "warnings"})
    assert parameters.isdisjoint({"decision", "score", "caps", "warnings"})


def test_production_factory_uniqueness_and_legacy_yield_exclusion(
    tmp_path: Path,
) -> None:
    _, _, request, evaluator = _prepared(tmp_path)
    result = evaluator.evaluate(request)
    assert not ProductionCandidateEvaluatorFactoryV1.is_formal_yield(result)
    with pytest.raises(TypeError, match="production evaluation result"):
        ProductionCandidateEvaluatorFactoryV1.is_formal_yield(  # type: ignore[arg-type]
            {"decision": "candidate_zoo", "status": "success"}
        )
    assert set(
        inspect.signature(ProductionCandidateEvaluatorFactoryV1.create).parameters
    ) == {"store"}


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("partial", "partially_completed"),
        ("invalid", "invalid"),
        ("unavailable", "unavailable"),
        ("timeout", "timeout"),
        ("infrastructure", "infrastructure_failure"),
    ],
)
def test_every_path_has_exactly_one_terminal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected: str,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    if mode == "invalid":
        monkeypatch.setattr(
            PITPredictiveEvidenceServiceV4,
            "record",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                EventValidationError("invalid backend")
            ),
        )
    elif mode == "unavailable":
        monkeypatch.setattr(
            PITPredictiveEvidenceServiceV4,
            "record",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                ProductionProviderUnavailable("provider unavailable")
            ),
        )
    elif mode == "infrastructure":
        monkeypatch.setattr(
            PITPredictiveEvidenceServiceV4,
            "record",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("infrastructure failed")
            ),
        )
    elif mode == "timeout":
        values = iter((0.0, 301.0))
        monkeypatch.setattr(
            "src.alpha_quality.production_evaluator_v1.time.monotonic",
            lambda: next(values),
        )
    result = evaluator.evaluate(request)
    terminals = store.query_events(
        event_type="TrialTerminated",
        entity_id=request.trial_id,
    )
    assert result.completion_status == expected
    assert len(terminals) == 1
    assert terminals[0].payload["decision"] == "none"
    assert (
        len(
            store.query_events(
                event_type=TERMINAL_DOSSIER_EVENT_TYPE,
                entity_id=f"terminal-dossier-{request.trial_id}",
            )
        )
        == 1
    )


def test_infrastructure_failure_has_decision_none_not_research_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    monkeypatch.setattr(
        PITPredictiveEvidenceServiceV4,
        "record",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("disk failed")),
    )
    result = evaluator.evaluate(request)
    terminal = store.query_events(event_type="TrialTerminated")[0]
    assert result.completion_status == "infrastructure_failure"
    assert terminal.payload["decision"] == "none"
    assert terminal.payload["status"] == "infrastructure_failure"


def test_partial_evaluation_preserves_available_independent_evidence(
    tmp_path: Path,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    result = evaluator.evaluate(request)
    observed = store.query_events(event_type="ObservedPanelPredictiveEvidenceRecorded")
    pit = store.query_events(event_type="PITPredictiveEvidenceRecorded")
    terminal = store.query_events(event_type="TrialTerminated")[0]
    assert result.completion_status == "partially_completed"
    assert len(observed) == len(pit) == 1
    assert observed[0].payload["availability"] == "available"
    assert pit[0].payload["availability"] == "unavailable"
    assert terminal.payload["decision"] == "none"
    assert terminal.payload["status"] == "skip"


def test_repeated_request_is_idempotent_and_revalidated(tmp_path: Path) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    first = evaluator.evaluate(request)
    event_count = len(store.query_events())
    second = evaluator.evaluate(request)
    assert second == first
    assert len(store.query_events()) == event_count
    dossier_event = store.query_events(event_type=TERMINAL_DOSSIER_EVENT_TYPE)[0]
    relative = dossier_event.payload["artifact_refs"][0]["relative_path"]
    store.artifact_root.joinpath(*str(relative).split("/")).write_bytes(b"tampered")
    with pytest.raises((EventValidationError, ValueError)):
        evaluator.evaluate(request)


def test_cross_run_or_cross_trial_source_binding_is_rejected(tmp_path: Path) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    before = len(store.query_events())
    with pytest.raises(EventTransitionError, match="mix runs"):
        evaluator.evaluate(replace(request, run_id="another-run"))
    with pytest.raises(EventTransitionError, match="trial binding"):
        evaluator.evaluate(replace(request, trial_id="another-trial"))
    contract_event = store.query_events(
        event_type="ResolvedEvaluationContractRegistered"
    )[0]
    with pytest.raises(EventTransitionError, match="watermark"):
        evaluator.evaluate(
            replace(request, source_watermark_event_hash=contract_event.event_hash)
        )
    assert len(store.query_events()) == before


def test_artifact_success_event_failure_does_not_create_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    original = store._append_producer_event

    def fail_factor_event(draft: EventDraft):
        if draft.event_type == "FactorOutputRecordedV3":
            raise RuntimeError("simulated append failure")
        return original(draft)

    monkeypatch.setattr(store, "_append_producer_event", fail_factor_event)
    result = evaluator.evaluate(request)
    assert result.completion_status == "infrastructure_failure"
    assert not store.query_events(event_type="FactorOutputRecordedV3")
    assert list(store.artifact_root.rglob("*.parquet"))
    assert store.verify_chain()


@pytest.mark.parametrize("mode", ["invalid", "timeout", "unavailable"])
def test_terminal_dossier_is_emitted_for_invalid_timeout_and_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    if mode == "invalid":
        error: Exception = EventValidationError("invalid")
    elif mode == "unavailable":
        error = ProductionProviderUnavailable("unavailable")
    else:
        values = iter((0.0, 301.0))
        monkeypatch.setattr(
            "src.alpha_quality.production_evaluator_v1.time.monotonic",
            lambda: next(values),
        )
        error = RuntimeError("unused")
    if mode != "timeout":
        monkeypatch.setattr(
            PITPredictiveEvidenceServiceV4,
            "record",
            lambda *args, **kwargs: (_ for _ in ()).throw(error),
        )
    result = evaluator.evaluate(request)
    dossier = store.query_events(event_type=TERMINAL_DOSSIER_EVENT_TYPE)
    assert len(dossier) == 1
    assert dossier[0].payload["completion_status"] == result.completion_status
    assert dossier[0].payload["terminal_dossier_hash"] == result.terminal_dossier_hash


def test_serial_replay_produces_identical_evidence_hashes(tmp_path: Path) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    first = evaluator.evaluate(request)
    second = evaluator.evaluate(request)
    assert second.evidence_bundle_hash == first.evidence_bundle_hash
    assert second.evidence_event_hashes == first.evidence_event_hashes
    assert second.terminal_dossier_hash == first.terminal_dossier_hash
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())


def test_event_order_is_nodes_then_evaluation_terminal_and_dossier(
    tmp_path: Path,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    evaluator.evaluate(request)
    events = store.query_events()
    positions = {event.event_type: index for index, event in enumerate(events)}
    node_positions = [
        index
        for index, event in enumerate(events)
        if event.event_type == PRODUCTION_NODE_EVENT_TYPE
    ]
    assert max(node_positions) < positions["EvaluationRecorded"]
    assert positions["EvaluationRecorded"] < positions["TrialTerminated"]
    assert positions["TrialTerminated"] < positions[TERMINAL_DOSSIER_EVENT_TYPE]


def test_feature_off_production_factory_is_no_write(tmp_path: Path) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="feature-off-production-evaluator",
    )
    before = len(store.query_events())
    with pytest.raises(RuntimeError, match="capability is disabled"):
        ProductionCandidateEvaluatorFactoryV1.create(store)
    assert len(store.query_events()) == before


def test_concurrent_duplicate_evaluators_produce_one_terminal(tmp_path: Path) -> None:
    flags, store, request, first_evaluator = _prepared(tmp_path)
    second_store = ResearchEventStore(
        store.db_path,
        artifact_root=store.artifact_root,
        flags=flags,
        code_version=store.code_version,
    )
    second_evaluator = ProductionCandidateEvaluatorFactoryV1.create(second_store)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = tuple(
            pool.map(
                lambda evaluator: evaluator.evaluate(request),
                (first_evaluator, second_evaluator),
            )
        )
    assert results[0] == results[1]
    assert len(store.query_events(event_type="TrialTerminated")) == 1
    assert len(store.query_events(event_type=TERMINAL_DOSSIER_EVENT_TYPE)) == 1
    assert store.verify_chain()


def test_dossier_event_failure_is_recorded_and_retry_recovers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    original = store._append_producer_event
    failed = False

    def fail_dossier_once(draft: EventDraft):
        nonlocal failed
        if draft.event_type == TERMINAL_DOSSIER_EVENT_TYPE and not failed:
            failed = True
            raise RuntimeError("dossier append failed")
        return original(draft)

    monkeypatch.setattr(store, "_append_producer_event", fail_dossier_once)
    first = evaluator.evaluate(request)
    assert not store.query_events(event_type=TERMINAL_DOSSIER_EVENT_TYPE)
    assert len(store.query_events(event_type=REPORT_FAILURE_EVENT_TYPE)) == 1
    monkeypatch.setattr(store, "_append_producer_event", original)
    recovered = evaluator.evaluate(request)
    assert recovered == first
    assert len(store.query_events(event_type=TERMINAL_DOSSIER_EVENT_TYPE)) == 1
    assert store.verify_chain()


def test_production_events_cannot_be_appended_generically(tmp_path: Path) -> None:
    _, store, request, evaluator = _prepared(tmp_path)
    evaluator.evaluate(request)
    node = store.query_events(event_type=PRODUCTION_NODE_EVENT_TYPE)[0]
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type=PRODUCTION_NODE_EVENT_TYPE,
                entity_id=node.entity_id,
                run_id=node.run_id,
                payload_schema_version=node.payload_schema_version,
                payload=dict(node.payload),
            )
        )
