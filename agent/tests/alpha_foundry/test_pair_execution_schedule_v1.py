from __future__ import annotations

from pathlib import Path

import pytest

from src.alpha_foundry.activation import (
    ActivationArtifactStore,
    ActivationEvidenceService,
    PairedActivationRunner,
)
from src.alpha_foundry.activation.pair_schedule_v1 import (
    ActivationPairExecutionScheduleServiceV1,
    RecordedActivationPairExecutionScheduleV1,
)
from src.alpha_foundry.activation.runner import activation_arm_execution_run_id
from src.research_ledger.events import EventDraft, EventTransitionError, ResearchEventStore
from test_activation_retriever import manifest, plan
from test_retriever_shadow import _flags


def _store(tmp_path: Path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="pair-schedule-test",
    )


def test_schedule_counterbalances_frozen_group_index_and_runner_order(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    frozen = plan(groups=8)
    ActivationEvidenceService(store).register_plan(frozen)
    service = ActivationPairExecutionScheduleServiceV1(store)
    first = service.freeze(
        plan_hash=frozen.plan_hash,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
    )
    second = service.freeze(
        plan_hash=frozen.plan_hash,
        run_group_id="group-01",
        mechanism_family="momentum",
        dag_region="leaf",
    )
    assert first.schedule.arm_order == ("control", "treatment")
    assert second.schedule.arm_order == ("treatment", "control")
    claim = service.claim(second)
    with pytest.raises(EventTransitionError, match="duplicate"):
        service.claim(second)

    runner = PairedActivationRunner(service.artifacts)
    registered = runner.register(frozen)
    observed: list[str] = []

    def execute(request, _scope):
        observed.append(request.arm)
        return manifest(frozen, request.run_group_id, request.arm)

    control, treatment = runner.run_scheduled_pair(
        registered, execution_claim=claim, executor=execute
    )
    assert observed == ["treatment", "control"]
    assert (control.arm, treatment.arm) == ("control", "treatment")
    with pytest.raises(RuntimeError, match="one-shot"):
        runner.run_scheduled_pair(
            registered, execution_claim=claim, executor=execute
        )
    assert store.verify_chain()


def test_schedule_rejects_odd_confirmatory_group_set(tmp_path: Path) -> None:
    store = _store(tmp_path)
    frozen = plan(groups=3)
    ActivationEvidenceService(store).register_plan(frozen)
    with pytest.raises(ValueError, match="exactly counterbalanceable"):
        ActivationPairExecutionScheduleServiceV1(store).freeze(
            plan_hash=frozen.plan_hash,
            run_group_id="group-00",
            mechanism_family="momentum",
            dag_region="leaf",
        )


def test_schedule_must_precede_either_arm_outcome(tmp_path: Path) -> None:
    store = _store(tmp_path)
    frozen = plan(groups=8)
    ActivationEvidenceService(store).register_plan(frozen)
    run_id = activation_arm_execution_run_id(
        plan_hash=frozen.plan_hash,
        run_group_id="group-01",
        arm="control",
    )
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="trial-before-pair-schedule",
            run_id=run_id,
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "trial-before-pair-schedule",
                "candidate_id": "candidate-before-pair-schedule",
                "data_scope": "train_valid",
                "objective": "rank_ic",
                "started_at": "2026-07-12T00:00:00Z",
            },
        )
    )
    with pytest.raises(EventTransitionError, match="follows arm outcomes"):
        ActivationPairExecutionScheduleServiceV1(store).freeze(
            plan_hash=frozen.plan_hash,
            run_group_id="group-01",
            mechanism_family="momentum",
            dag_region="leaf",
        )


def test_recorded_schedule_authority_cannot_be_caller_minted(tmp_path: Path) -> None:
    store = _store(tmp_path)
    frozen = plan(groups=8)
    ActivationEvidenceService(store).register_plan(frozen)
    recorded = ActivationPairExecutionScheduleServiceV1(store).freeze(
        plan_hash=frozen.plan_hash,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
    )
    with pytest.raises(TypeError, match="recorded by the schedule service"):
        RecordedActivationPairExecutionScheduleV1(
            recorded.schedule, recorded.event, _authority=object()
        )


def test_runner_rejects_schedule_from_another_artifact_root(tmp_path: Path) -> None:
    store = _store(tmp_path)
    frozen = plan(groups=8)
    ActivationEvidenceService(store).register_plan(frozen)
    recorded = ActivationPairExecutionScheduleServiceV1(store).freeze(
        plan_hash=frozen.plan_hash,
        run_group_id="group-00",
        mechanism_family="momentum",
        dag_region="leaf",
    )
    other = PairedActivationRunner(ActivationArtifactStore(tmp_path / "other-artifacts"))
    registered = other.register(frozen)
    claim = ActivationPairExecutionScheduleServiceV1(store).claim(recorded)
    with pytest.raises(ValueError, match="runner artifact root"):
        other.run_scheduled_pair(
            registered,
            execution_claim=claim,
            executor=lambda *_: pytest.fail("executor must remain unopened"),
        )
