from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.dag import FactorDAGQuery
from src.alpha_foundry.retrieval.evidence_v3 import RetrieverInputArtifactStoreV3
from src.alpha_foundry.retrieval.service_v3 import RetrieverDecisionV3Service
from src.alpha_quality.scope import DiscoveryEvidenceProjector
from src.research_ledger.events import EventDraft, EventValidationError
from src.research_ledger.hash_utils import canonical_json_hash
from test_retriever_shadow import _candidate, _views


def _record(tmp_path: Path):
    store, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    service = RetrieverDecisionV3Service(store)
    recorded = service.record(
        official_candidate_ids=("flat-official",),
        candidates=(candidate,),
        data_snapshot_hash=evidence.data_snapshot_hash,
        seed=41,
        candidate_budget=1,
        run_id="retriever-v3-run",
    )
    return store, query, evidence, candidate, service, recorded


def test_source_bound_v3_service_rebuilds_components_propensity_and_selection(
    tmp_path: Path,
) -> None:
    store, _, evidence, candidate, service, recorded = _record(tmp_path)
    event = recorded.event
    assert event.event_type == "RetrieverDecisionV3Recorded"
    assert event.payload["input_bundle_hash"] == recorded.input_bundle.bundle_hash
    assert event.payload["shadow_decision_hash"] == recorded.decision.decision_hash
    assert event.payload["selected_factor_spec_ids"] == (candidate.factor_spec_id,)
    assert event.payload["components"][0]["selection_propensity"] == 1.0
    assert event.payload["eligible_event_watermark"] == evidence.source_watermark
    assert store.verify_chain()
    assert store.replay().watermark_event_hash == event.event_hash

    retry = service.record(
        official_candidate_ids=("flat-official",),
        candidates=(candidate,),
        data_snapshot_hash=evidence.data_snapshot_hash,
        seed=41,
        candidate_budget=1,
        run_id="retriever-v3-run",
    )
    assert retry.event.event_hash == event.event_hash


def test_caller_rehashed_fabricated_v3_component_is_rejected(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    payload = recorded.event.to_dict()["payload"]
    payload["decision_id"] = "retriever-v3-forged"
    payload["components"][0]["action_score"] += 100.0
    content = {
        "schema_version": "retriever_source_bound_decision.v3",
        **{
            key: value
            for key, value in payload.items()
            if key not in {"decision_id", "decision_hash", "artifact_refs"}
        },
    }
    payload["decision_hash"] = canonical_json_hash(content)
    with pytest.raises(EventValidationError, match="deterministically rebuilt"):
        store.append_event(
            EventDraft(
                event_type="RetrieverDecisionV3Recorded",
                entity_id="retriever-v3-forged",
                run_id="forged-run",
                payload_schema_version="retriever_decision_recorded.v3",
                payload=payload,
                idempotency_key="retriever-v3:forged",
            )
        )


def test_bundle_tampering_breaks_chain_verification(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    target = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    target.write_text("{}\n", encoding="utf-8")
    assert store.verify_chain() is False
    with pytest.raises((EventValidationError, ValueError)):
        RetrieverInputArtifactStoreV3(store.artifact_root).read(
            str(reference["relative_path"]),
            expected_bundle_hash=recorded.input_bundle.bundle_hash,
        )


def test_bundle_reader_rejects_duplicate_keys_and_nonfinite_json(tmp_path: Path) -> None:
    store, _, _, _, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    relative = str(reference["relative_path"])
    target = store.artifact_root.joinpath(*relative.split("/"))
    original = target.read_text(encoding="utf-8")
    duplicate = original.replace(
        '{"bundle_hash"',
        '{"bundle_hash":"sha256:' + '0' * 64 + '","bundle_hash"',
        1,
    )
    target.write_text(duplicate, encoding="utf-8")
    reader = RetrieverInputArtifactStoreV3(store.artifact_root)
    with pytest.raises(ValueError, match="duplicate"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)
    target.write_text(original.replace("0.8", "NaN", 1), encoding="utf-8")
    with pytest.raises(ValueError, match="non-finite"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)
    target.write_text(
        original.replace("shadow-action", "api_key=secret-value", 1),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="secret or private path"):
        reader.read(relative, expected_bundle_hash=recorded.input_bundle.bundle_hash)


def test_historical_discovery_projection_excludes_v3_decision(tmp_path: Path) -> None:
    store, _, evidence, _, _, recorded = _record(tmp_path)
    historical = DiscoveryEvidenceProjector(flags=store.flags).project_at_watermark(
        store,
        data_snapshot_hash=evidence.data_snapshot_hash,
        watermark_event_hash=evidence.source_watermark,
    )
    assert historical.source_watermark == evidence.source_watermark
    assert historical.factual == evidence.factual
    assert historical.episodic == evidence.episodic
    assert recorded.event.event_hash not in {
        event.event_hash for event in historical._verified_subsequence.events
    }


def test_v3_service_rejects_secret_shaped_source_before_event_append(tmp_path: Path) -> None:
    store, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    unsafe = type(candidate)(
        factor_spec_id=candidate.factor_spec_id,
        action_id="api_key=secret-value",
        motif=candidate.motif,
        parent_context_hash=candidate.parent_context_hash,
        base_ledger_score=candidate.base_ledger_score,
        output_panel=candidate.output_panel,
        reference_panels=candidate.reference_panels,
        canonical_ast=candidate.canonical_ast,
        reference_asts=candidate.reference_asts,
        semantic=candidate.semantic,
        estimated_cost=candidate.estimated_cost,
        cost_evidence_hash=candidate.cost_evidence_hash,
    )
    with pytest.raises(ValueError, match="secret or private path"):
        RetrieverDecisionV3Service(store).record(
            official_candidate_ids=("flat",),
            candidates=(unsafe,),
            data_snapshot_hash=evidence.data_snapshot_hash,
            seed=1,
            candidate_budget=1,
            run_id="unsafe-v3",
        )
    assert not any(
        event.event_type == "RetrieverDecisionV3Recorded"
        for event in store.query_events()
    )
    assert not list(store.artifact_root.glob("retriever-input-v3/**/*.json"))


def test_v3_service_owns_evidence_and_accepts_no_caller_decision(tmp_path: Path) -> None:
    store, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    service = RetrieverDecisionV3Service(store)
    with pytest.raises(TypeError):
        service.record(  # type: ignore[call-arg]
            official_candidate_ids=("flat",),
            candidates=(candidate,),
            data_snapshot_hash=evidence.data_snapshot_hash,
            seed=1,
            candidate_budget=1,
            run_id="caller-decision",
            decision={"selected": "caller"},
        )


def test_v3_candidate_ast_remains_bound_to_authoritative_dag(tmp_path: Path) -> None:
    store, query, evidence = _views(tmp_path)
    candidate = _candidate(query, evidence)
    forged = type(candidate)(
        factor_spec_id=candidate.factor_spec_id,
        action_id=candidate.action_id,
        motif=candidate.motif,
        parent_context_hash=candidate.parent_context_hash,
        base_ledger_score=candidate.base_ledger_score,
        output_panel=candidate.output_panel,
        reference_panels=candidate.reference_panels,
        canonical_ast={"kind": "field", "name": "close"},
        reference_asts=candidate.reference_asts,
        semantic=candidate.semantic,
        estimated_cost=candidate.estimated_cost,
        cost_evidence_hash=candidate.cost_evidence_hash,
    )
    with pytest.raises(ValueError, match="canonical AST"):
        RetrieverDecisionV3Service(store).record(
            official_candidate_ids=("flat",),
            candidates=(forged,),
            data_snapshot_hash=evidence.data_snapshot_hash,
            seed=1,
            candidate_budget=1,
            run_id="forged-ast",
        )
    assert not list(store.artifact_root.glob("retriever-input-v3/**/*.json"))
    assert FactorDAGQuery(evidence.factual.dag).projection.projection_hash
