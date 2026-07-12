from __future__ import annotations

import inspect
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.alpha_quality.claim_decision_v1 import ClaimDecisionServiceV1
from src.alpha_quality.production_evaluator_v1 import (
    ProductionCandidateEvaluatorFactoryV1,
    ProductionEvaluationRequestV1,
)
from src.alpha_quality.research_dossier_v1 import (
    AUDIENCES,
    CANDIDATE_DOSSIER_MEDIA_TYPE,
    CanonicalDossierResolverV1,
    ResearchDossierArtifactStoreV1,
    ResearchDossierServiceV1,
    render_audience_view,
)
from src.alpha_quality.secondary_evidence_v1 import ComparisonPoolServiceV1
from src.api.alpha_genesis_routes import register_alpha_genesis_routes
from src.research_ledger.events import EventDraft, EventTransitionError, EventValidationError
from tests.alpha_quality.test_predictive_evidence_v4 import _setup


def _completed(tmp_path: Path):
    _, store, contract, snapshot, definition = _setup(
        tmp_path, enable_decision=True, enable_reports=True
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    result = ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(
        ProductionEvaluationRequestV1(
            run_id="predictive-run",
            trial_id="predictive-trial",
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=contract.contract.contract_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
    )
    assert result.completion_status == "completed"
    return store, definition, result


def _record_all(tmp_path: Path):
    store, definition, result = _completed(tmp_path)
    service = ResearchDossierServiceV1(store)
    dossier, dossier_event = service.record_candidate(
        run_id="predictive-run", factor_spec_id=definition.entity_id
    )
    report, report_event = service.record_run_report(run_id="predictive-run")
    manifest, manifest_event = service.record_release_manifest(
        run_id="predictive-run"
    )
    return (
        store,
        definition,
        result,
        service,
        dossier,
        dossier_event,
        report,
        report_event,
        manifest,
        manifest_event,
    )


def test_candidate_dossier_has_professional_minimum_sections(tmp_path: Path) -> None:
    values = _record_all(tmp_path)
    store, definition, result, _, dossier, event, *_ = values
    payload = dossier.payload
    assert payload["research_identity"]["canonical_formula"] == "rank(close)"
    assert payload["data_authority"]["snapshot_hash"].startswith("sha256:")
    assert payload["search_and_selection"]["trial_count"] == 1
    assert payload["predictive_evidence"]["observed"] is not None
    assert payload["execution_evidence"]["availability"] == "unavailable"
    assert payload["claim_matrix"]["claims"]
    assert payload["quality_decision"]["decision"] == "research_only"
    assert payload["final_and_forward"]["final_test"] == "not_opened"
    assert payload["no_live_trading_meaning"] is True
    assert event.payload["quality_decision_event_hash"] == result.quality_decision_event_hash
    assert CanonicalDossierResolverV1(store).candidate(definition.entity_id) == payload
    assert store.verify_chain()


def test_every_audience_view_preserves_required_boundaries(tmp_path: Path) -> None:
    _, _, _, _, dossier, _, *_ = _record_all(tmp_path)
    for audience in AUDIENCES:
        view = render_audience_view(dossier, audience)
        assert view["current_decision"] == "research_only"
        assert view["claim_scope_and_grade"]
        assert view["implementation_blockers"]
        assert view["final_and_forward"]["forward_success_established"] is False
        assert view["research_only_no_live"] is True
        assert view["dossier_hash"] == dossier.dossier_hash
        assert view["material_adverse_findings"]


def test_pm_view_cannot_hide_execution_blocker(tmp_path: Path) -> None:
    _, _, _, _, dossier, _, *_ = _record_all(tmp_path)
    view = render_audience_view(dossier, "pm")
    assert "EXECUTION_EVIDENCE_UNAVAILABLE" in view["implementation_blockers"]
    assert view["detail"]["implementation"]["availability"] == "unavailable"


def test_report_never_becomes_decision_input(tmp_path: Path) -> None:
    store, definition, result, _, _, dossier_event, *_ = _record_all(tmp_path)
    assert "dossier" not in inspect.signature(ClaimDecisionServiceV1.record).parameters
    decision = store.query_events(event_type="QualityDecisionV4Recorded")[0]
    assert dossier_event.event_hash not in decision.payload["evidence_event_hashes"]
    assert result.quality_decision_event_hash == decision.event_hash
    assert definition.entity_id == decision.payload["factor_spec_id"]


def test_manual_unbound_file_is_not_authority(tmp_path: Path) -> None:
    store, definition, *_ = _record_all(tmp_path)
    manual = store.artifact_root / "manual-candidate.json"
    manual.write_text(
        json.dumps({"schema_version": "candidate_research_dossier.v1", "decision": "paper_candidate"}),
        encoding="utf-8",
    )
    resolved = CanonicalDossierResolverV1(store).candidate(definition.entity_id)
    assert resolved["quality_decision"]["decision"] == "research_only"


def test_generic_append_cannot_forge_dossier_or_release(tmp_path: Path) -> None:
    store, _, *_ = _completed(tmp_path)
    for event_type, schema in (
        ("ResearchDossierRecorded", "research_dossier_recorded.v1"),
        ("ResearchReleaseManifestRecorded", "research_release_manifest_recorded.v1"),
    ):
        with pytest.raises(EventValidationError, match="deterministic producer"):
            store.append_event(
                EventDraft(
                    event_type=event_type,
                    entity_id="forged",
                    run_id="predictive-run",
                    payload_schema_version=schema,
                    payload={"decision": "paper_candidate"},
                )
            )


def test_candidate_dossier_requires_analytical_node_and_terminal(tmp_path: Path) -> None:
    _, store, _, _, definition = _setup(
        tmp_path, enable_decision=True, enable_reports=True
    )
    with pytest.raises(EventTransitionError, match="analytical node"):
        ResearchDossierServiceV1(store).record_candidate(
            run_id="predictive-run", factor_spec_id=definition.entity_id
        )


def test_run_report_requires_every_attempt_terminal(tmp_path: Path) -> None:
    _, store, _, _, _ = _setup(
        tmp_path, enable_decision=True, enable_reports=True
    )
    with pytest.raises(EventTransitionError, match="every attempt terminal"):
        ResearchDossierServiceV1(store).record_run_report(run_id="predictive-run")


def test_release_manifest_is_replayable_and_honest(tmp_path: Path) -> None:
    values = _record_all(tmp_path)
    store, _, _, service, _, _, report, _, manifest, manifest_event = values
    second, second_event = service.record_release_manifest(run_id="predictive-run")
    assert second.to_dict() == manifest.to_dict()
    assert second_event.event_hash == manifest_event.event_hash
    assert manifest.payload["engineering_status"] == "implemented_and_event_bound"
    assert manifest.payload["empirical_status"] == "train_valid_only"
    assert manifest.payload["final_artifact_count"] == 0
    assert report.payload["test_access_count"] == 0
    assert store.verify_chain()


def test_artifact_tamper_breaks_chain_verification(tmp_path: Path) -> None:
    store, _, _, _, _, event, *_ = _record_all(tmp_path)
    reference = next(
        ref for ref in event.payload["artifact_refs"]
        if ref["media_type"] == CANDIDATE_DOSSIER_MEDIA_TYPE
    )
    path = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    path.write_text("{}", encoding="utf-8")
    assert store.verify_chain() is False


def test_report_failure_does_not_change_decision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, definition, result = _completed(tmp_path)
    decision_before = store.query_events(event_type="QualityDecisionV4Recorded")[0]
    monkeypatch.setattr(
        ResearchDossierArtifactStoreV1,
        "write_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        ResearchDossierServiceV1(store).record_candidate(
            run_id="predictive-run", factor_spec_id=definition.entity_id
        )
    decision_after = store.query_events(event_type="QualityDecisionV4Recorded")[0]
    assert decision_after.event_hash == decision_before.event_hash
    assert result.quality_decision_event_hash == decision_before.event_hash


def test_automatic_report_failure_is_typed_and_non_decisional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, store, contract, snapshot, definition = _setup(
        tmp_path, enable_decision=True, enable_reports=True
    )
    pool, _ = ComparisonPoolServiceV1(store).freeze(
        run_id="predictive-run",
        source_watermark_event_hash=definition.event_hash,
        members=(),
    )
    monkeypatch.setattr(
        ResearchDossierArtifactStoreV1,
        "write_candidate",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = ProductionCandidateEvaluatorFactoryV1.create(store).evaluate(
        ProductionEvaluationRequestV1(
            run_id="predictive-run",
            trial_id="predictive-trial",
            factor_definition_event_hash=definition.event_hash,
            resolved_contract_hash=contract.contract.contract_hash,
            snapshot_event_hash=snapshot.event.event_hash,
            source_watermark_event_hash=definition.event_hash,
            frozen_comparison_pool_hash=pool.comparison_pool_hash,
        )
    )
    failures = store.query_events(
        event_type="ResearchDossierMaterializationFailed"
    )
    decisions = store.query_events(event_type="QualityDecisionV4Recorded")
    assert result.completion_status == "completed"
    assert len(failures) == len(decisions) == 1
    assert failures[0].payload["decision_effect"] == "none"
    assert result.quality_decision_event_hash == decisions[0].event_hash
    assert store.verify_chain()


def test_dossier_json_contains_no_private_absolute_path_or_nonfinite(tmp_path: Path) -> None:
    store, _, _, _, dossier, event, *_ = _record_all(tmp_path)
    encoded = json.dumps(dossier.to_dict(), allow_nan=False, sort_keys=True)
    assert str(tmp_path) not in encoded
    assert "D:\\" not in encoded
    assert "<script" not in encoded.lower()
    assert len(event.payload["artifact_refs"]) == 6


def test_get_only_canonical_dossier_and_view_routes(tmp_path: Path) -> None:
    store, definition, *_ = _record_all(tmp_path)
    app = FastAPI()
    report_root = tmp_path / "flat-reports"
    report_root.mkdir()
    register_alpha_genesis_routes(
        app,
        require_auth=lambda: None,
        report_root=report_root,
        dossier_resolver=CanonicalDossierResolverV1(store),
    )
    client = TestClient(app)
    dossier = client.get(f"/api/alpha-genesis/dossiers/{definition.entity_id}")
    view = client.get(
        f"/api/alpha-genesis/dossiers/{definition.entity_id}/views/model_risk"
    )
    assert dossier.status_code == view.status_code == 200
    assert dossier.json()["factor_spec_id"] == definition.entity_id
    assert view.json()["audience"] == "model_risk"
    assert client.post(
        f"/api/alpha-genesis/dossiers/{definition.entity_id}"
    ).status_code == 405
