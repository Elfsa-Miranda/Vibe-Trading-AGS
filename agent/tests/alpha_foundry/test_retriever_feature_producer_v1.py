from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

import src.alpha_foundry.retrieval.feature_producer_v1 as feature_producer_v1
from src.alpha_foundry.dsl.identity import FactorIdentityService
from src.alpha_foundry.retrieval.action_template_v1 import (
    RetrieverActionTemplateServiceV1,
)
from src.alpha_foundry.retrieval.feature_producer_v1 import (
    RetrieverFeatureSourceArtifactStoreV1,
    RetrieverFeatureSourceServiceV1,
)
from src.alpha_foundry.retrieval.feature_source_v1 import TrainValidSnapshotServiceV1
from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.hash_utils import utc_now_iso
from test_process_memory import _semantics
from test_retriever_shadow import _flags


def _panel():
    dates = pd.date_range("2025-01-01", periods=4)
    return {
        "open": pd.DataFrame(
            {"AAA": [1.0, 2.0, 3.0, 4.0], "BBB": [2.0, 1.0, 4.0, 3.0]},
            index=dates,
        ),
        "close": pd.DataFrame(
            {"AAA": [2.0, 1.0, 4.0, 3.0], "BBB": [1.0, 2.0, 3.0, 4.0]},
            index=dates,
        ),
        "_meta": {
            "pit_contract_present": True,
            "survivorship_bias": False,
            "calendar": "SSE_SZSE",
            "timezone": "Asia/Shanghai",
        },
    }


def _scorecard(
    store: ResearchEventStore,
    factor_id: str,
    snapshot_hash: str,
    *,
    utility: float,
    cost_bps: float,
) -> dict[str, str]:
    relative = f"feature-scorecards/{factor_id}.json"
    path = store.artifact_root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "alpha_quality_scorecard.v1",
        "factor_id": factor_id,
        "scope": "discovery",
        "data_snapshot_ref": snapshot_hash,
        "predictive": {
            "by_horizon": {
                "1": {"by_split": {"valid": {"rank_icir": utility}}}
            }
        },
        "execution": {
            "uses_execution_return": True,
            "cost_bps_mean": cost_bps,
        },
    }
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    return {
        "relative_path": relative,
        "artifact_hash": store.hash_artifact(path),
        "media_type": "application/vnd.vibe.alpha-quality-scorecard+json",
    }


def _eligible_factor(
    store: ResearchEventStore,
    identity: FactorIdentityService,
    *,
    trial_id: str,
    formula: str,
    snapshot_hash: str,
    utility: float,
    cost_bps: float,
):
    factor = identity.record_attempt(
        trial_id=trial_id,
        run_id="discovery-run",
        candidate_id=trial_id,
        formula=formula,
        semantics=_semantics(),
    )
    assert factor.factor_spec_id
    reference = _scorecard(
        store,
        factor.factor_spec_id,
        snapshot_hash,
        utility=utility,
        cost_bps=cost_bps,
    )
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-" + trial_id,
            run_id="discovery-run",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-" + trial_id,
                "trial_id": trial_id,
                "factor_spec_id": factor.factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": reference["artifact_hash"],
                "artifact_refs": [reference],
                "metadata": {"source": "feature-producer-fixture"},
            },
            idempotency_key="evaluation:" + trial_id,
        )
    )
    terminal = store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id=trial_id,
            run_id="discovery-run",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": trial_id,
                "status": "success",
                "reason_codes": ["EVALUATED"],
                "decision": "candidate_zoo",
                "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
            idempotency_key="terminal:" + trial_id,
        )
    )
    return factor, terminal


def _record(tmp_path: Path):
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="retriever-feature-producer-test",
    )
    snapshot = TrainValidSnapshotServiceV1(store, flags=store.flags).freeze(
        _panel(),
        universe="fixture",
        period="2025-01-01/2025-01-04",
        source_config={"provider": "fixture"},
        run_id="snapshot-run",
    )
    identity = FactorIdentityService(store=store, flags=store.flags)
    target, _ = _eligible_factor(
        store,
        identity,
        trial_id="target",
        formula="rank(close)",
        snapshot_hash=snapshot.snapshot.snapshot_hash,
        utility=0.30,
        cost_bps=10.0,
    )
    reference, watermark_event = _eligible_factor(
        store,
        identity,
        trial_id="reference",
        formula="zscore(open)",
        snapshot_hash=snapshot.snapshot.snapshot_hash,
        utility=0.10,
        cost_bps=5.0,
    )
    assert target.factor_spec_id and reference.factor_spec_id
    policy = ActivationRetrieverPolicy()
    action = RetrieverActionTemplateServiceV1(
        store=store,
        flags=store.flags,
    ).freeze(
        execution_run_id="treatment-run",
        parent_factor_spec_id=target.factor_spec_id,
        template_id="rank_wrap",
        eligible_event_watermark=watermark_event.event_hash,
        data_snapshot_hash=snapshot.snapshot.snapshot_hash,
        retrieval_policy_hash=policy.policy_hash,
    )
    recorded = RetrieverFeatureSourceServiceV1(
        store,
        flags=store.flags,
    ).record(
        execution_run_id="treatment-run",
        snapshot_event_hash=snapshot.event.event_hash,
        action_event_hashes=(action.event.event_hash,),
        eligible_event_watermark=watermark_event.event_hash,
        retrieval_policy=policy,
    )
    return store, snapshot, target, reference, action, recorded


def test_feature_source_rebuilds_outputs_base_cost_and_missing_semantic(
    tmp_path: Path,
) -> None:
    store, snapshot, target, reference, action, recorded = _record(tmp_path)
    candidate = recorded.source.candidates[0]
    assert candidate.factor_spec_id == target.factor_spec_id
    assert candidate.action_id == action.action.action_id
    assert candidate.output_panel.data_snapshot_hash == snapshot.snapshot.snapshot_hash
    assert candidate.reference_panels[0].factor_spec_id == reference.factor_spec_id
    assert candidate.base_ledger_score == pytest.approx(math.exp(0.30))
    assert candidate.estimated_cost == pytest.approx(0.001)
    assert candidate.semantic.candidate_vector is None
    assert candidate.semantic.missing_reason == "NO_REGISTERED_SEMANTIC_PROVIDER"
    valid_values = [point.value for point in candidate.output_panel.points if point.valid]
    assert set(valid_values) == {0.5, 1.0}
    assert recorded.event.payload["semantic_state"] == "unavailable"
    assert store.verify_chain()


def test_feature_source_reuses_immutable_output_panels_within_service(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    service = RetrieverFeatureSourceServiceV1(store, flags=store.flags)
    calls = 0
    projection_calls = 0
    original = feature_producer_v1.evaluate_formula
    original_project = service.projector.project_at_watermark

    def counted_evaluate_formula(formula, frames):
        nonlocal calls
        calls += 1
        return original(formula, frames)

    def counted_project(*args, **kwargs):
        nonlocal projection_calls
        projection_calls += 1
        return original_project(*args, **kwargs)

    monkeypatch.setattr(
        feature_producer_v1,
        "evaluate_formula",
        counted_evaluate_formula,
    )
    monkeypatch.setattr(service.projector, "project_at_watermark", counted_project)
    source = recorded.source
    kwargs = {
        "execution_run_id": source.execution_run_id,
        "snapshot_event_hash": source.snapshot_event_hash,
        "action_event_hashes": source.action_event_hashes,
        "eligible_event_watermark": source.eligible_event_watermark,
        "retrieval_policy": ActivationRetrieverPolicy(),
    }
    first = service.rebuild(**kwargs)
    first_call_count = calls
    second = service.rebuild(**kwargs)

    assert first_call_count > 0
    assert calls == first_call_count
    assert projection_calls == 1
    assert second.source_hash == first.source_hash == source.source_hash


def test_feature_source_rejects_missing_execution_cost(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    scorecard_event = next(
        event
        for event in store.query_events(event_type="EvaluationRecorded")
        if event.event_hash in recorded.source.scorecard_event_hashes
    )
    reference = scorecard_event.payload["artifact_refs"][0]
    path = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("execution")
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert store.verify_chain() is False


def test_feature_source_reader_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    relative = str(reference["relative_path"])
    path = store.artifact_root.joinpath(*relative.split("/"))
    original = path.read_text(encoding="utf-8")
    duplicate = original.replace(
        '{"action_event_hashes"',
        '{"action_event_hashes":[],"action_event_hashes"',
        1,
    )
    path.write_text(duplicate, encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate Retriever feature source key"):
        RetrieverFeatureSourceArtifactStoreV1(store.artifact_root).read(
            relative,
            recorded.source.source_hash,
        )
