from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_quality.decision_v2 import DecisionEvidenceRepository
from src.alpha_quality.decision_v2.source_v3 import (
    QualityDecisionInputArtifactStoreV3,
    QualityDecisionV3Service,
)
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash
from test_decision_v2 import _flags, _policy, _refs


def _define(store: ResearchEventStore) -> None:
    digest = canonical_json_hash({"fixture": "definition"})
    store.append_event(
        EventDraft(
            event_type="FactorDefinitionRecorded",
            entity_id="factor-1",
            run_id="quality-v3-run",
            payload_schema_version="factor_definition_recorded.v1",
            payload={
                "factor_spec_id": "factor-1",
                "expression_id": "expression-1",
                "canonical_ast_hash": digest,
                "grammar_version": "1.0.0",
                "grammar_hash": digest,
                "metadata": {},
                "artifact_refs": [],
            },
        )
    )


def _record(tmp_path: Path, *, unresolved_scorecard: bool = False):
    flags = _flags(events=True)
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="quality-decision-v3-test",
    )
    _define(store)
    repository = DecisionEvidenceRepository(tmp_path / "evidence")
    refs = _refs(repository)
    if unresolved_scorecard:
        refs = replace(
            refs,
            scorecard_hash=canonical_json_hash({"missing": "scorecard"}),
        )
    service = QualityDecisionV3Service(
        store=store,
        flags=flags,
        policy=_policy(),
        repository=repository,
    )
    return store, repository, refs, service, service.decide_and_record(
        refs, run_id="quality-v3-run"
    )


def test_source_bound_v3_rebuilds_decision_and_replays(tmp_path: Path) -> None:
    store, _, refs, service, recorded = _record(tmp_path)
    assert recorded.event.event_type == "QualityDecisionV3Recorded"
    assert recorded.decision.decision == "candidate_zoo"
    assert recorded.event.payload["quality_decision_hash"] == recorded.decision.decision_hash
    assert recorded.event.payload["input_bundle_hash"] == recorded.input_bundle.bundle_hash
    assert store.verify_chain()
    assert store.replay().event_count == 2

    retry = service.decide_and_record(refs, run_id="quality-v3-run")
    assert retry.event.event_hash == recorded.event.event_hash


def test_rehashed_fabricated_candidate_tier_is_rejected(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["decision_id"] = "quality-decision-v3-forged"
    payload["quality_decision_hash"] = canonical_json_hash({"forged": "underlying"})
    payload["decision"] = "research_only"
    payload["tier"] = 1
    payload["reasons"] = []
    payload["caps"] = ["FORGED_CAP"]
    payload["within_tier_score"] = 1.0
    content = {
        "schema_version": "quality_decision_source_bound.v3",
        **{
            key: value
            for key, value in payload.items()
            if key not in {"decision_id", "decision_hash", "artifact_refs"}
        },
    }
    payload["decision_hash"] = canonical_json_hash(content)
    with pytest.raises(EventValidationError, match="deterministic rebuild"):
        store.append_event(
            EventDraft(
                event_type="QualityDecisionV3Recorded",
                entity_id="quality-decision-v3-forged",
                run_id="forged-run",
                payload_schema_version="quality_decision_recorded.v3",
                payload=payload,
                idempotency_key="quality-decision-v3:forged",
            )
        )


def test_unresolved_source_is_frozen_and_caps_at_research_only(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path, unresolved_scorecard=True)
    assert recorded.decision.decision == "research_only"
    assert "EVIDENCE_REFERENCE_UNRESOLVED" in recorded.decision.caps
    assert len(recorded.input_bundle.evidence_records) == 5
    assert store.verify_chain()


def test_input_bundle_tampering_breaks_chain_verification(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")
    assert store.verify_chain() is False


def test_bundle_reader_rejects_duplicate_keys_and_nonfinite_json(tmp_path: Path) -> None:
    store, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    relative = str(reference["relative_path"])
    target = store.artifact_root.joinpath(*relative.split("/"))
    original = target.read_text(encoding="utf-8")
    duplicate = original.replace(
        '{"bundle_hash"',
        '{"bundle_hash":"sha256:' + "0" * 64 + '","bundle_hash"',
        1,
    )
    target.write_text(duplicate, encoding="utf-8")
    reader = QualityDecisionInputArtifactStoreV3(store.artifact_root)
    with pytest.raises(ValueError, match="duplicate"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)
    target.write_text(original.replace("0.1", "NaN", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)
    target.write_text(
        original.replace("TRAIN_VALID_ONLY", "api_key=secret-value", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="secret or private path"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)


def test_service_rejects_secret_source_before_event_append(tmp_path: Path) -> None:
    flags = _flags(events=True)
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="quality-decision-v3-test",
    )
    repository = DecisionEvidenceRepository(tmp_path / "evidence")
    _define(store)
    refs = _refs(
        repository,
        scorecard={"limitations": ["api_key=secret-value"]},
    )
    service = QualityDecisionV3Service(
        store=store,
        flags=flags,
        policy=_policy(),
        repository=repository,
    )
    with pytest.raises(ValueError, match="secret or private path"):
        service.decide_and_record(refs, run_id="unsafe-v3")
    assert not store.query_events(event_type="QualityDecisionV3Recorded")
    assert not list(store.artifact_root.glob("quality-decision-input-v3/**/*.json"))


def test_missing_factor_definition_creates_no_source_artifact(tmp_path: Path) -> None:
    flags = _flags(events=True)
    store = ResearchEventStore(
        tmp_path / "research.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=flags,
        code_version="quality-decision-v3-test",
    )
    repository = DecisionEvidenceRepository(tmp_path / "evidence")
    refs = _refs(repository)
    service = QualityDecisionV3Service(
        store=store,
        flags=flags,
        policy=_policy(),
        repository=repository,
    )
    with pytest.raises(EventTransitionError, match="prior factor definition"):
        service.decide_and_record(refs, run_id="orphan-v3")
    assert not store.query_events()
    assert not list(store.artifact_root.glob("quality-decision-input-v3/**/*.json"))


def test_service_accepts_no_caller_decision_and_feature_off_writes_nothing(
    tmp_path: Path,
) -> None:
    store, repository, refs, service, _ = _record(tmp_path)
    with pytest.raises(TypeError):
        service.decide_and_record(  # type: ignore[call-arg]
            refs,
            run_id="caller-truth",
            decision={"decision": "candidate_zoo"},
        )
    before_events = len(store.query_events())
    before_artifacts = list(store.artifact_root.rglob("*.json"))
    with pytest.raises(RuntimeError, match="disabled"):
        QualityDecisionV3Service(
            store=store,
            flags=_flags(enabled=False, events=True),
            policy=_policy(),
            repository=repository,
        )
    assert len(store.query_events()) == before_events
    assert list(store.artifact_root.rglob("*.json")) == before_artifacts
