from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.activation import ActivationEvidenceService, ActivationExperimentPlan
from src.alpha_foundry.control_evidence import FlatControlPolicyV1
from src.alpha_foundry.flat_schedule_v1 import (
    PreArmFlatScheduleArtifactStoreV1,
    PreArmFlatScheduleServiceV1,
)
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank
from src.research_ledger.events import EventValidationError, ResearchEventStore
from test_activation_retriever import plan
from test_retriever_shadow import _flags


def _record(tmp_path: Path):
    store = ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="prearm-schedule-test",
    )
    policy = FlatControlPolicyV1.create(
        max_candidates_per_seed=3, max_candidates=3, trial_budget=3
    )
    base = plan()
    frozen = ActivationExperimentPlan.create(
        experiment_id="prearm-flat-schedule-fixture",
        phase="confirmatory",
        registered_at=base.registered_at,
        provenance=replace(base.provenance, control_policy_hash=policy.policy_hash),
        design=replace(base.design, candidate_budget=3, compute_budget=3),
        analysis=base.analysis,
        readiness_requirements=base.readiness_requirements,
        decision_policy_hash=base.decision_policy_hash,
        truth_table_hash=base.truth_table_hash,
    )
    ActivationEvidenceService(store).register_plan(frozen)
    recorded = PreArmFlatScheduleServiceV1(store).freeze(
        plan_hash=frozen.plan_hash,
        pair_id="group-00:momentum:leaf",
        run_group_id="group-00",
        data_snapshot_hash=frozen.provenance.train_snapshot_hash,
        seed_bank=SeedBank([AlphaSeed("flat", "close", "fixture")]),
        mutator=SeedMutator(max_candidates_per_seed=3),
        max_candidates=3,
        trial_budget=3,
    )
    return store, frozen, recorded


def test_schedule_freezes_candidates_before_any_arm_outcome(tmp_path: Path) -> None:
    store, _, recorded = _record(tmp_path)
    assert recorded.schedule.replay() == tuple(
        item["candidate_id"] for item in recorded.schedule.candidates
    )
    assert not store.query_events(event_type="TrialStarted")
    assert not store.query_events(event_type="TrialTerminated")
    assert recorded.event.payload["output_hash"] == recorded.schedule.output_hash
    assert store.verify_chain()


def test_schedule_rejects_plan_policy_substitution(tmp_path: Path) -> None:
    store, frozen, _ = _record(tmp_path)
    with pytest.raises(EventValidationError, match="cannot replay"):
        PreArmFlatScheduleServiceV1(store).freeze(
            plan_hash=frozen.plan_hash,
            pair_id="group-01:momentum:leaf",
            run_group_id="group-01",
            data_snapshot_hash=frozen.provenance.train_snapshot_hash,
            seed_bank=SeedBank([AlphaSeed("flat-2", "open", "fixture")]),
            mutator=SeedMutator(max_candidates_per_seed=1),
            max_candidates=1,
            trial_budget=1,
        )


def test_schedule_artifact_rejects_duplicate_keys(tmp_path: Path) -> None:
    store, _, recorded = _record(tmp_path)
    reference = recorded.event.payload["artifact_refs"][0]
    path = store.artifact_root.joinpath(*str(reference["relative_path"]).split("/"))
    original = path.read_text(encoding="utf-8")
    path.write_text(original.replace('{"candidates"', '{"candidates":[],"candidates"', 1), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate pre-arm schedule key"):
        PreArmFlatScheduleArtifactStoreV1(store.artifact_root).read(
            str(reference["relative_path"]), recorded.schedule.schedule_hash
        )
