from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationEvidenceService,
    PairedActivationRunner,
)
from src.alpha_foundry.activation.pair_schedule_v1 import (
    ActivationPairExecutionScheduleServiceV1,
)
from src.alpha_foundry.activation.resource_v2 import (
    ActivationResourceEvidenceV2,
    validate_resource_evidence_v2_mapping,
)
from src.research_ledger.events import (
    EventDraft,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json_hash
from test_activation_retriever import manifest, plan
from test_retriever_shadow import _flags


def _setup(tmp_path: Path, run_group_id: str = "group-01"):
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="scheduled-resource-v2-test",
    )
    frozen = plan(groups=8)
    ActivationEvidenceService(store).register_plan(frozen)
    schedules = ActivationPairExecutionScheduleServiceV1(store)
    recorded = schedules.freeze(
        plan_hash=frozen.plan_hash,
        run_group_id=run_group_id,
        mechanism_family="momentum",
        dag_region="leaf",
    )
    claim = schedules.claim(recorded)
    runner = PairedActivationRunner(schedules.artifacts)
    registered = runner.register(frozen)
    return store, frozen, claim, runner, registered


def test_scheduled_resource_removes_only_proven_order_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, frozen, claim, runner, registered = _setup(tmp_path)
    wall = iter((10.0, 12.0, 20.0, 23.0))
    cpu = iter((5.0, 6.0, 8.0, 9.5))
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.perf_counter", lambda: next(wall)
    )
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.process_time", lambda: next(cpu)
    )
    observed: list[str] = []

    def execute(request, _scope):
        observed.append(request.arm)
        return manifest(frozen, request.run_group_id, request.arm)

    measured = runner.run_scheduled_pair_measured(
        registered, execution_claim=claim, executor=execute
    )
    assert observed == ["treatment", "control"]
    assert measured.treatment_resource.arm_order_position == 0
    assert measured.control_resource.arm_order_position == 1
    for evidence in (measured.control_resource, measured.treatment_resource):
        assert evidence.source_failure_codes == (
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        )
        assert "ARM_ORDER_NOT_COUNTERBALANCED" not in evidence.source_failure_codes
        assert evidence.source_complete is False
        assert validate_resource_evidence_v2_mapping(evidence.to_dict())


def test_scheduled_resource_reports_observed_timeout_without_claiming_enforcement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, frozen, claim, runner, registered = _setup(tmp_path, "group-00")
    wall = iter((0.0, 31.0, 40.0, 41.0))
    cpu = iter((0.0, 1.0, 2.0, 3.0))
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.perf_counter", lambda: next(wall)
    )
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.process_time", lambda: next(cpu)
    )
    measured = runner.run_scheduled_pair_measured(
        registered,
        execution_claim=claim,
        executor=lambda request, _scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )
    assert measured.control_resource.source_failure_codes == (
        "EXECUTOR_TIMEOUT_EXCEEDED",
        "EXECUTOR_TIMEOUT_NOT_ENFORCED",
        "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
    )
    assert "EXECUTOR_TIMEOUT_EXCEEDED" not in (
        measured.treatment_resource.source_failure_codes
    )


def test_caller_cannot_mint_or_remove_resource_v2_limitations(tmp_path: Path) -> None:
    _, frozen, claim, _, _ = _setup(tmp_path)
    request = object()
    with pytest.raises(TypeError, match="minted by the scheduled runner"):
        ActivationResourceEvidenceV2(
            claim=claim,
            request=request,  # type: ignore[arg-type]
            manifest=manifest(frozen, "group-01", "control"),
            wall_seconds=0.0,
            cpu_seconds=0.0,
            _authority=object(),
        )


def test_resource_v2_event_binds_schedule_claim_and_run(tmp_path: Path) -> None:
    store, frozen, claim, runner, registered = _setup(tmp_path)
    measured = runner.run_scheduled_pair_measured(
        registered,
        execution_claim=claim,
        executor=lambda request, _scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )
    service = ActivationEvidenceService(store)
    service.record_run(measured.control)
    service.record_run(measured.treatment)
    service.record_resource_evidence_v2(measured.control_resource)
    service.record_resource_evidence_v2(measured.treatment_resource)
    events = store.query_events(event_type="ActivationResourceMeasuredV2")
    assert len(events) == 2
    assert all(event.payload["source_complete"] is False for event in events)
    assert all(
        "ARM_ORDER_NOT_COUNTERBALANCED"
        not in event.payload["source_failure_codes"]
        for event in events
    )
    assert store.verify_chain()


def test_resource_v2_rejects_rehashed_measurement_policy_substitution(
    tmp_path: Path,
) -> None:
    store, frozen, claim, runner, registered = _setup(tmp_path)
    measured = runner.run_scheduled_pair_measured(
        registered,
        execution_claim=claim,
        executor=lambda request, _scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )
    service = ActivationEvidenceService(store)
    service.record_run(measured.control)
    raw = measured.control_resource.to_dict()
    raw["measurement_policy_hash"] = "sha256:" + "f" * 64
    raw["evidence_hash"] = canonical_json_hash(
        raw, exclude_keys=("evidence_hash",)
    )
    relative = service.artifacts.put("resource", raw)
    resource_id = (
        "activation-resource-v2-"
        + raw["evidence_hash"].removeprefix("sha256:")[:24]
    )
    path = store.artifact_root.joinpath(*relative.split("/"))
    payload = {
        "resource_id": resource_id,
        **{key: value for key, value in raw.items() if key != "schema_version"},
        "artifact_refs": [{
            "relative_path": relative,
            "artifact_hash": hash_artifact(path),
            "media_type": "application/vnd.vibe.activation-resource-v2+json",
        }],
    }
    with pytest.raises(EventValidationError, match="artifact is invalid"):
        store.append_event(
            EventDraft(
                event_type="ActivationResourceMeasuredV2",
                entity_id=resource_id,
                run_id=measured.control_resource.run_group_id,
                payload_schema_version="activation_resource_measured.v2",
                payload=payload,
                idempotency_key="activation-resource-v2:forged-policy",
            )
        )
