from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.dsl.identity import FactorIdentityService, FactorSpecSemantics
from src.alpha_quality.decision_v2.evidence_v3 import (
    DecisionEvidenceRecordV3,
    DecisionLedgerEvidenceServiceV3,
    LEDGER_EVIDENCE_MEDIA_TYPE,
)
from src.alpha_quality.flags import ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventAppendError,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, utc_now_iso


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_ALPHA_FOUNDRY": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
            "VIBE_TRADING_FACTOR_DAG": "1",
            "VIBE_TRADING_DECISION_V2": "1",
        }
    )


def _semantics() -> FactorSpecSemantics:
    digest = canonical_json_hash({"fixture": "decision-evidence-v3"})
    return FactorSpecSemantics(
        transform_pipeline_hash=digest,
        field_semantics={"close": "pit_eod"},
        signal_time="close",
        order_time="next_open",
        entry_price_time="next_open",
        execution_lag=1,
        return_horizon=5,
        universe_mask_hash=digest,
        tradability_mask_hash=digest,
    )


def _record(
    tmp_path: Path,
    *,
    infrastructure_failure: bool = False,
    mint_evidence: bool = True,
):
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="decision-evidence-v3-test",
    )
    identity = FactorIdentityService(store=store, flags=store.flags).record_attempt(
        trial_id="trial-evidence-v3",
        run_id="run-evidence-v3",
        candidate_id="candidate-evidence-v3",
        formula="rank(close)",
        semantics=_semantics(),
    )
    assert identity.factor_spec_id is not None

    scorecard_path = store.artifact_root / "scorecards" / "source.json"
    scorecard_path.parent.mkdir(parents=True, exist_ok=True)
    scorecard_path.write_text(
        canonical_json(
            {
                "schema_version": "scorecard-test-source.v1",
                "factor_spec_id": identity.factor_spec_id,
                "scope": "valid",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    scorecard_hash = hash_artifact(scorecard_path)
    evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-evidence-v3",
            run_id="run-evidence-v3",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-evidence-v3",
                "trial_id": "trial-evidence-v3",
                "factor_spec_id": identity.factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": scorecard_hash,
                "artifact_refs": [
                    {
                        "relative_path": "scorecards/source.json",
                        "artifact_hash": scorecard_hash,
                        "media_type": "application/json",
                    }
                ],
                "metadata": {},
            },
        )
    )
    terminal = store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="trial-evidence-v3",
            run_id="run-evidence-v3",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "trial-evidence-v3",
                "status": "success",
                "reason_codes": [],
                "decision": "research_only",
                "evaluation_event_hash": evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )
    failure_events = ()
    if infrastructure_failure:
        store.append_event(
            EventDraft(
                event_type="TrialStarted",
                entity_id="trial-infrastructure",
                run_id="run-evidence-v3",
                payload_schema_version="trial_started.v1",
                payload={
                    "trial_id": "trial-infrastructure",
                    "candidate_id": "candidate-infrastructure",
                    "data_scope": "train_valid",
                    "objective": "failure-accounting",
                    "started_at": utc_now_iso(),
                },
            )
        )
        failure = store.append_event(
            EventDraft(
                event_type="GenerationFailureRecorded",
                entity_id="trial-infrastructure",
                run_id="run-evidence-v3",
                payload_schema_version="generation_failure_recorded.v1",
                payload={
                    "trial_id": "trial-infrastructure",
                    "failure_code": "WORKER_UNAVAILABLE",
                    "failure_kind": "infrastructure_failure",
                    "message": "fixture infrastructure failure",
                    "occurred_at": utc_now_iso(),
                },
            )
        )
        infrastructure_terminal = store.append_event(
            EventDraft(
                event_type="TrialTerminated",
                entity_id="trial-infrastructure",
                run_id="run-evidence-v3",
                payload_schema_version="trial_terminated.v1",
                payload={
                    "trial_id": "trial-infrastructure",
                    "status": "infrastructure_failure",
                    "reason_codes": ["WORKER_UNAVAILABLE"],
                    "decision": "research_only",
                    "evaluation_event_hash": None,
                    "terminated_at": utc_now_iso(),
                },
            )
        )
        failure_events = (failure, infrastructure_terminal)
    recorded = None
    if mint_evidence:
        recorded = DecisionLedgerEvidenceServiceV3(store).record(
            factor_spec_id=identity.factor_spec_id,
            run_id="run-evidence-v3",
        )
    return (
        store,
        scorecard_path,
        evaluation,
        terminal,
        recorded,
        failure_events,
    )


def test_evidence_requires_authoritative_producer_event(tmp_path: Path) -> None:
    store, _, evaluation, terminal, recorded, _ = _record(tmp_path)

    assert recorded.event.event_type == "DecisionEvidenceV3Recorded"
    assert recorded.record.evidence_kind == "ledger"
    assert recorded.event.previous_event_hash == terminal.event_hash
    assert evaluation.event_hash in recorded.record.source_event_hashes
    assert recorded.artifact.media_type == LEDGER_EVIDENCE_MEDIA_TYPE
    assert store.verify_chain()
    assert store.replay().event_count == len(store.query_events())

    retry = DecisionLedgerEvidenceServiceV3(store).record(
        factor_spec_id=recorded.record.factor_spec_id,
        run_id="run-evidence-v3",
    )
    assert retry.event.event_hash == recorded.event.event_hash


def test_generic_event_append_cannot_forge_decision_evidence_v3(
    tmp_path: Path,
) -> None:
    store, _, _, _, recorded, _ = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]

    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="DecisionEvidenceV3Recorded",
                entity_id=str(payload["evidence_id"]),
                run_id="run-evidence-v3",
                payload_schema_version="decision_evidence_recorded.v3",
                payload=payload,
            )
        )


def test_producer_rejects_caller_cherry_pick_when_run_has_two_terminals(
    tmp_path: Path,
) -> None:
    store, _, evaluation, _, _, _ = _record(tmp_path, mint_evidence=False)
    factor_spec_id = str(evaluation.payload["factor_spec_id"])
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="trial-second-terminal",
            run_id="run-evidence-v3",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "trial-second-terminal",
                "candidate_id": "candidate-second-terminal",
                "data_scope": "valid",
                "objective": "ambiguity-negative-test",
                "started_at": utc_now_iso(),
            },
        )
    )
    second_evaluation = store.append_event(
        EventDraft(
            event_type="EvaluationRecorded",
            entity_id="evaluation-second-terminal",
            run_id="run-evidence-v3",
            payload_schema_version="evaluation_recorded.v1",
            payload={
                "evaluation_id": "evaluation-second-terminal",
                "trial_id": "trial-second-terminal",
                "factor_spec_id": factor_spec_id,
                "data_scope": "valid",
                "scorecard_hash": evaluation.payload["scorecard_hash"],
                "artifact_refs": evaluation.payload["artifact_refs"],
                "metadata": {},
            },
        )
    )
    store.append_event(
        EventDraft(
            event_type="TrialTerminated",
            entity_id="trial-second-terminal",
            run_id="run-evidence-v3",
            payload_schema_version="trial_terminated.v1",
            payload={
                "trial_id": "trial-second-terminal",
                "status": "reject",
                "reason_codes": ["QUALITY_REJECTED"],
                "decision": "reject",
                "evaluation_event_hash": second_evaluation.event_hash,
                "terminated_at": utc_now_iso(),
            },
        )
    )

    with pytest.raises(
        EventValidationError,
        match="exactly one eligible factor terminal",
    ):
        DecisionLedgerEvidenceServiceV3(store).record(
            factor_spec_id=factor_spec_id,
            run_id="run-evidence-v3",
        )

    assert store.query_events(event_type="DecisionEvidenceV3Recorded") == []
    assert store.verify_chain()


def test_disabled_decision_capability_cannot_construct_producer_or_write(
    tmp_path: Path,
) -> None:
    flags = ResolvedAGSFlags.from_settings(
        {
            "VIBE_TRADING_AGS_ENABLED": "1",
            "VIBE_TRADING_RESEARCH_EVENTS": "1",
        }
    )
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="decision-evidence-v3-disabled-test",
    )
    before = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    with pytest.raises(RuntimeError, match="capability is disabled"):
        DecisionLedgerEvidenceServiceV3(store)

    after = {
        path.relative_to(tmp_path).as_posix()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before
    assert store.query_events() == []


def test_intervening_event_invalidates_ledger_evidence_watermark(
    tmp_path: Path,
) -> None:
    store, _, _, _, recorded, _ = _record(tmp_path)
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="intervening-trial",
            run_id="intervening-run",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "intervening-trial",
                "candidate_id": "intervening-candidate",
                "data_scope": "train_valid",
                "objective": "watermark-race",
                "started_at": utc_now_iso(),
            },
        )
    )
    payload = recorded.event.to_dict()["payload"]

    with pytest.raises(EventTransitionError, match="watermark is stale"):
        store._append_producer_event(
            EventDraft(
                event_type="DecisionEvidenceV3Recorded",
                entity_id=str(payload["evidence_id"]),
                run_id="run-evidence-v3",
                payload_schema_version="decision_evidence_recorded.v3",
                payload=payload,
            )
        )

    assert len(store.query_events(event_type="DecisionEvidenceV3Recorded")) == 1
    assert store.verify_chain()


def test_existing_evidence_rejects_later_source_event_in_same_run(
    tmp_path: Path,
) -> None:
    store, _, _, _, recorded, _ = _record(tmp_path)
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="late-same-run-trial",
            run_id="run-evidence-v3",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "late-same-run-trial",
                "candidate_id": "late-same-run-candidate",
                "data_scope": "valid",
                "objective": "stale-evidence-negative-test",
                "started_at": utc_now_iso(),
            },
        )
    )

    with pytest.raises(EventTransitionError, match="run changed"):
        DecisionLedgerEvidenceServiceV3(store).record(
            factor_spec_id=recorded.record.factor_spec_id,
            run_id="run-evidence-v3",
        )

    assert store.verify_chain()


def test_decision_evidence_v3_has_no_public_payload_factory() -> None:
    with pytest.raises(TypeError, match="loaded or minted"):
        DecisionEvidenceRecordV3(
            schema_version="decision_evidence_record.v3",
            evidence_kind="ledger",
            factor_spec_id="caller-factor",
            evidence_run_id="caller-run",
            producer_schema_version="decision_ledger_evidence_service.v3",
            producer_policy_hash=canonical_json_hash({"caller": "policy"}),
            source_event_hashes=(canonical_json_hash({"caller": "event"}),),
            source_artifact_hashes=(),
            evidence_payload={},
            evidence_hash=canonical_json_hash({"caller": "evidence"}),
            _authority=object(),
        )


def test_changed_source_artifact_breaks_decision_replay(tmp_path: Path) -> None:
    store, scorecard_path, _, _, recorded, _ = _record(tmp_path)
    assert store.verify_chain()

    scorecard_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(EventValidationError, match="artifact hash mismatch"):
        DecisionLedgerEvidenceServiceV3(store).record(
            factor_spec_id=recorded.record.factor_spec_id,
            run_id="run-evidence-v3",
        )
    assert store.verify_chain() is False
    with pytest.raises(ResearchEventAppendError):
        store.replay()


def test_rehashed_forged_source_binding_is_rejected(tmp_path: Path) -> None:
    store, _, _, _, recorded, _ = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    raw = target.read_text(encoding="utf-8")
    target.write_text(
        raw.replace(
            str(recorded.record.evidence_payload["terminal_event_hash"]),
            canonical_json_hash({"forged": "terminal"}),
        ),
        encoding="utf-8",
    )

    assert store.verify_chain() is False


def test_ledger_producer_derives_infrastructure_failures_from_exact_run(
    tmp_path: Path,
) -> None:
    store, _, _, _, recorded, failures = _record(
        tmp_path,
        infrastructure_failure=True,
    )
    expected = tuple(sorted(event.event_hash for event in failures))

    assert recorded.record.evidence_payload["complete"] is True
    assert (
        recorded.record.evidence_payload["infrastructure_failure_event_hashes"]
        == expected
    )
    assert set(expected).issubset(recorded.record.source_event_hashes)
    assert store.verify_chain()
