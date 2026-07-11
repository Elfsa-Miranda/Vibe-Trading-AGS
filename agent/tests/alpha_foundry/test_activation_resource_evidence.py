from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationArtifactStore,
    ActivationResourceEvidenceV1,
    ActivationEvidenceService,
    PairedActivationRunner,
)
from test_activation_retriever import manifest, plan
from test_activation_retriever import event_store
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
)


def test_runner_owns_wall_and_cpu_measurement_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = plan()
    artifacts = ActivationArtifactStore(tmp_path)
    runner = PairedActivationRunner(artifacts)
    registered = runner.register(frozen)
    wall = iter((10.0, 12.0, 20.0, 23.0))
    cpu = iter((5.0, 6.0, 8.0, 9.5))
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.perf_counter", lambda: next(wall)
    )
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.process_time", lambda: next(cpu)
    )

    measured = runner.run_pair_measured(
        registered,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=lambda request, scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )

    assert measured.control_resource.wall_seconds == 2.0
    assert measured.control_resource.cpu_seconds == 1.0
    assert measured.treatment_resource.wall_seconds == 3.0
    assert measured.treatment_resource.cpu_seconds == 1.5
    assert measured.control.metrics["resource_wall_seconds"] == 1.0
    assert measured.control_resource.wall_seconds != measured.control.metrics[
        "resource_wall_seconds"
    ]
    for evidence in (measured.control_resource, measured.treatment_resource):
        assert evidence.peak_rss_mb is None
        assert evidence.source_complete is False
        assert evidence.source_failure_codes == (
            "ARM_ORDER_NOT_COUNTERBALANCED",
            "EXECUTOR_TIMEOUT_NOT_ENFORCED",
            "PEAK_RSS_ISOLATED_MEASUREMENT_UNAVAILABLE",
        )
        relative = artifacts.relative_path("resource", evidence.evidence_hash)
        assert artifacts.get("resource", evidence.evidence_hash) == evidence.to_dict()
        assert (tmp_path / relative).exists()


def test_caller_cannot_mint_resource_truth(tmp_path: Path) -> None:
    frozen = plan()
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path))
    registered = runner.register(frozen)
    captured = {}

    def execute(request, scope):
        captured[request.arm] = (request, manifest(frozen, request.run_group_id, request.arm))
        return captured[request.arm][1]

    runner.run_pair_measured(
        registered,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=execute,
    )
    request, run_manifest = captured["control"]
    with pytest.raises(TypeError, match="minted by the paired runner"):
        ActivationResourceEvidenceV1(
            request=request,
            manifest=run_manifest,
            wall_seconds=0.0,
            cpu_seconds=0.0,
            _authority=object(),
        )


def test_decreasing_runner_clock_is_rejected_not_clamped(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = plan()
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path))
    wall = iter((10.0, 9.0))
    cpu = iter((5.0, 6.0))
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.perf_counter", lambda: next(wall)
    )
    monkeypatch.setattr(
        "src.alpha_foundry.activation.runner.time.process_time", lambda: next(cpu)
    )
    with pytest.raises(ValueError, match="finite and non-negative"):
        runner.run_pair_measured(
            runner.register(frozen),
            run_group_id="group-00",
            mechanism_family="momentum",
            dag_region="leaf",
            executor=lambda request, scope: manifest(
                frozen, request.run_group_id, request.arm
            ),
        )


def test_peak_rss_unavailability_cannot_be_changed_after_hash(tmp_path: Path) -> None:
    frozen = plan()
    runner = PairedActivationRunner(ActivationArtifactStore(tmp_path))
    measured = runner.run_pair_measured(
        runner.register(frozen),
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=lambda request, scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )
    payload = measured.control_resource.to_dict()
    payload["peak_rss_mb"] = 1.0
    payload["source_complete"] = True
    payload["source_failure_codes"] = []
    with pytest.raises(ValueError, match="content hash"):
        ActivationArtifactStore(tmp_path).put("resource", payload)


def test_resource_evidence_persists_after_matching_run_and_replays(
    tmp_path: Path,
) -> None:
    frozen = plan()
    store = event_store(tmp_path)
    service = ActivationEvidenceService(store)
    service.register_plan(frozen)
    runner = PairedActivationRunner(service.artifacts)
    measured = runner.run_pair_measured(
        runner.register(frozen),
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
        executor=lambda request, scope: manifest(
            frozen, request.run_group_id, request.arm
        ),
    )
    service.record_run(measured.control)
    relative = service.record_resource_evidence(measured.control_resource)
    events = store.query_events(event_type="ActivationResourceMeasured")
    assert len(events) == 1
    assert events[0].payload["evidence_hash"] == measured.control_resource.evidence_hash
    assert relative.endswith(
        measured.control_resource.evidence_hash.removeprefix("sha256:") + ".json"
    )
    assert store.verify_chain()

    payload = events[0].to_dict()["payload"]
    payload["resource_id"] = "activation-resource-forged"
    payload["source_complete"] = True
    payload["source_failure_codes"] = []
    payload["peak_rss_mb"] = 1.0
    with pytest.raises(EventValidationError, match="retain all runner limitations"):
        store.append_event(
            EventDraft(
                event_type="ActivationResourceMeasured",
                entity_id="activation-resource-forged",
                run_id="group-00",
                payload_schema_version="activation_resource_measured.v1",
                payload=payload,
                idempotency_key="activation-resource-v1:forged",
            )
        )

    valid_payload = events[0].to_dict()["payload"]
    with pytest.raises(EventTransitionError, match="does not match its run group"):
        store.append_event(
            EventDraft(
                event_type="ActivationResourceMeasured",
                entity_id=valid_payload["resource_id"],
                run_id="another-run-group",
                payload_schema_version="activation_resource_measured.v1",
                payload=valid_payload,
                idempotency_key="activation-resource-v1:mismatched-run",
            )
        )

    valid_payload["resource_id"] = "activation-resource-v1-" + "0" * 24
    with pytest.raises(EventValidationError, match="identity must derive"):
        store.append_event(
            EventDraft(
                event_type="ActivationResourceMeasured",
                entity_id=valid_payload["resource_id"],
                run_id="group-00",
                payload_schema_version="activation_resource_measured.v1",
                payload=valid_payload,
                idempotency_key="activation-resource-v1:mismatched-identity",
            )
        )
