from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from src.alpha_foundry.activation.formal_protocol_v3 import (
    ConfirmatoryPowerTemplateV3,
    FormalActivationDossierV3,
    FormalActivationStagePlanV3,
    Phase11ReadinessV3,
    freeze_pair_schedules_v3,
)
from src.alpha_foundry.activation.isolated_worker_v3 import (
    IsolatedWorkerTaskV3,
    ParentEnforcedWorkerV3,
)
from src.alpha_foundry.activation.run_source_v3 import (
    FormalActivationRunSourceAuditorV3,
)
from src.research_ledger.events import ResearchEventEnvelope
from src.research_ledger.hash_utils import canonical_json_hash


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _plan(
    stage: str,
    *,
    parent: str | None = None,
    pilot_result: str | None = None,
) -> FormalActivationStagePlanV3:
    if stage == "dry_run":
        groups = ("dry-00", "dry-01")
        seeds = (9000, 9001)
        effect = False
    elif stage == "pilot":
        groups = tuple(f"pilot-{index:02d}" for index in range(12))
        seeds = tuple(range(9100, 9112))
        effect = True
    else:
        groups = tuple(f"confirmatory-{index:02d}" for index in range(14))
        seeds = tuple(range(9200, 9214))
        effect = True
    return FormalActivationStagePlanV3.create(
        experiment_family_id="phase11-formal-flat-topology-v1",
        stage=stage,  # type: ignore[arg-type]
        registered_at="2026-07-13T00:00:00Z",
        parent_stage_plan_hash=parent,
        pilot_result_hash=pilot_result,
        baseline_manifest_hash=_hash("baseline"),
        train_snapshot_hash=_hash("train"),
        valid_snapshot_hash=_hash("valid"),
        generator_hash=_hash("generator"),
        evaluator_hash=_hash("evaluator"),
        dag_scheduler_hash=_hash("dag"),
        decision_criteria_hash=_hash("criteria"),
        control_policy_hash=_hash("flat"),
        treatment_policy_hash=_hash("topology"),
        candidate_budget=32,
        compute_budget=64,
        worker_limit=2,
        timeout_seconds=10.0,
        run_group_ids=groups,
        seeds=seeds,
        primary_endpoint="effective_non_duplicate_candidate_yield",
        primary_threshold=0.5,
        primary_threshold_unit="candidates_per_32_attempts",
        secondary_noninferiority_margins={
            "duplicate_rate": 0.05,
            "failure_rate": 0.05,
            "peak_rss_mb": 128.0,
            "wall_seconds": 30.0,
        },
        effect_analysis_allowed=effect,
    )


def test_parent_enforces_timeout_and_measures_isolated_rss() -> None:
    runner = ParentEnforcedWorkerV3()
    completed = runner.run(
        IsolatedWorkerTaskV3.create(
            operation="resource_probe",
            duration_seconds=0.08,
            allocation_mb=8,
            nonce="completed-probe",
        ),
        timeout_seconds=3.0,
    )
    timed_out = runner.run(
        IsolatedWorkerTaskV3.create(
            operation="timeout_probe",
            duration_seconds=3.0,
            allocation_mb=4,
            nonce="timeout-probe",
        ),
        timeout_seconds=0.2,
    )
    assert completed.status == "completed"
    assert completed.timeout_enforced is True
    assert completed.peak_rss_mb > 0.0
    assert timed_out.status == "timeout"
    assert timed_out.timeout_enforced is True
    assert timed_out.peak_rss_mb > 0.0
    assert "WORKER_TIMEOUT_ENFORCED" in timed_out.failure_codes


def test_dry_run_and_pilot_are_strictly_separated_and_counterbalanced() -> None:
    dry = _plan("dry_run")
    pilot = _plan("pilot", parent=dry.plan_hash)
    dry_schedules = freeze_pair_schedules_v3(dry)
    pilot_schedules = freeze_pair_schedules_v3(pilot)
    assert len(dry_schedules) == 2
    assert dry.effect_analysis_allowed is False
    assert len(pilot_schedules) == 12
    assert sum(item.arm_order[0] == "flat" for item in pilot_schedules) == 6
    assert all(
        item.flat_rng_namespace != item.topology_rng_namespace
        and item.flat_cache_namespace != item.topology_cache_namespace
        for item in pilot_schedules
    )


def test_dry_run_cannot_be_relabelled_as_effect_analysis() -> None:
    dry = _plan("dry_run")
    with pytest.raises(ValueError, match="infrastructure-only"):
        replace(dry, effect_analysis_allowed=True)


def test_confirmatory_power_is_preregistered_and_threshold_is_immutable() -> None:
    dry = _plan("dry_run")
    pilot = _plan("pilot", parent=dry.plan_hash)
    template = ConfirmatoryPowerTemplateV3.create(pilot)
    required = template.required_pairs(
        pilot_mean_effect=0.35,
        pilot_paired_sd=1.2,
        preregistered_mde=0.8,
    )
    assert required >= 12
    assert required % 2 == 0
    assert template.primary_threshold == pilot.primary_threshold
    confirmatory = _plan(
        "confirmatory",
        parent=pilot.plan_hash,
        pilot_result=_hash("pilot-result"),
    )
    assert confirmatory.primary_threshold == pilot.primary_threshold
    with pytest.raises(ValueError, match="prior pilot result"):
        _plan("confirmatory", parent=pilot.plan_hash)


def test_blocked_external_inputs_generate_inconclusive_shadow_dossier() -> None:
    runner = ParentEnforcedWorkerV3()
    resources = (
        runner.run(
            IsolatedWorkerTaskV3.create(
                operation="resource_probe",
                duration_seconds=0.05,
                allocation_mb=4,
                nonce="dossier-complete",
            ),
            timeout_seconds=3.0,
        ),
        runner.run(
            IsolatedWorkerTaskV3.create(
                operation="timeout_probe",
                duration_seconds=2.0,
                allocation_mb=2,
                nonce="dossier-timeout",
            ),
            timeout_seconds=0.15,
        ),
    )
    readiness = Phase11ReadinessV3.create(
        baseline_manifest_hash=_hash("baseline"),
        baseline_effective_sample=21,
        baseline_replayable=True,
        infrastructure_resources=resources,
        production_candidate_factory_available=False,
        production_run_inputs_available=False,
        run_budget_authorized=False,
    )
    dry = _plan("dry_run")
    pilot = _plan("pilot", parent=dry.plan_hash)
    schedules = freeze_pair_schedules_v3(pilot)
    dossier = FormalActivationDossierV3.blocked(
        readiness=readiness,
        dry_run_plan=dry,
        pilot_plan=pilot,
        confirmatory_template=ConfirmatoryPowerTemplateV3.create(pilot),
        dry_run_resources=resources,
        pilot_schedules=schedules,
    )
    assert readiness.ready_for_pilot_outcome_access is False
    assert dossier.verdict == "inconclusive"
    assert dossier.active_research_only is False
    assert dossier.official_search_policy == "flat_with_topology_shadow"
    assert dossier.pilot_complete_pairs == 0
    assert len(dossier.pilot_schedule_hashes) == 12


def test_zero_sample_approved_dossier_is_rejected() -> None:
    runner = ParentEnforcedWorkerV3()
    resources = (
        runner.run(
            IsolatedWorkerTaskV3.create(
                operation="resource_probe",
                duration_seconds=0.04,
                allocation_mb=1,
                nonce="zero-sample-complete",
            ),
            timeout_seconds=3.0,
        ),
        runner.run(
            IsolatedWorkerTaskV3.create(
                operation="timeout_probe",
                duration_seconds=2.0,
                allocation_mb=1,
                nonce="zero-sample-timeout",
            ),
            timeout_seconds=0.12,
        ),
    )
    readiness = Phase11ReadinessV3.create(
        baseline_manifest_hash=_hash("baseline"),
        baseline_effective_sample=21,
        baseline_replayable=True,
        infrastructure_resources=resources,
        production_candidate_factory_available=False,
        production_run_inputs_available=False,
        run_budget_authorized=False,
    )
    dry = _plan("dry_run")
    pilot = _plan("pilot", parent=dry.plan_hash)
    dossier = FormalActivationDossierV3.blocked(
        readiness=readiness,
        dry_run_plan=dry,
        pilot_plan=pilot,
        confirmatory_template=ConfirmatoryPowerTemplateV3.create(pilot),
        dry_run_resources=resources,
        pilot_schedules=freeze_pair_schedules_v3(pilot),
    )
    with pytest.raises(ValueError, match="formal source authority gate"):
        replace(
            dossier,
            verdict="approved",
            active_research_only=True,
            reasons=(),
            dossier_hash=_hash("forged"),
        )


def test_formal_source_auditor_rejects_legacy_retriever_decision() -> None:
    legacy = ResearchEventEnvelope(
        schema_version="research_event.v1",
        event_id="legacy-event",
        event_type="RetrieverDecisionV3Recorded",
        entity_id="legacy-decision",
        run_id="legacy-run",
        payload_schema_version="retriever_decision_recorded.v3",
        payload={},
        payload_hash=_hash("legacy-payload"),
        idempotency_key=None,
        previous_event_hash=None,
        event_hash=_hash("legacy-event"),
        created_at="2026-07-13T00:00:00Z",
        code_version="test",
        feature_flags={},
        warnings=(),
        hard_failures=(),
    )
    failures: set[str] = set()
    resolved = FormalActivationRunSourceAuditorV3._resolve(
        {legacy.event_hash: legacy},
        (legacy.event_hash,),
        {"RetrieverDecisionV7Recorded"},
        "CURRENT_RETRIEVAL_AUTHORITY_MISSING",
        failures,
    )
    assert resolved == []
    assert failures == {"CURRENT_RETRIEVAL_AUTHORITY_MISSING"}


def test_tracked_phase11_evidence_is_hash_bound_and_has_no_outcomes() -> None:
    root = Path(__file__).resolve().parents[2] / "research_evidence" / "activation_phase11"
    pilot = json.loads((root / "phase11_pilot_plan.json").read_text(encoding="utf-8"))
    schedules = json.loads((root / "phase11_pair_schedules.json").read_text(encoding="utf-8"))
    resources = json.loads((root / "phase11_dry_run_resources.json").read_text(encoding="utf-8"))
    readiness = json.loads((root / "phase11_readiness.json").read_text(encoding="utf-8"))
    dossier = json.loads((root / "phase11_blocked_dossier.json").read_text(encoding="utf-8"))
    assert canonical_json_hash(pilot, exclude_keys=("plan_hash",)) == pilot["plan_hash"]
    assert canonical_json_hash(schedules, exclude_keys=("document_hash",)) == schedules["document_hash"]
    assert canonical_json_hash(resources, exclude_keys=("document_hash",)) == resources["document_hash"]
    assert canonical_json_hash(readiness, exclude_keys=("readiness_hash",)) == readiness["readiness_hash"]
    assert canonical_json_hash(dossier, exclude_keys=("dossier_hash",)) == dossier["dossier_hash"]
    assert len(pilot["run_group_ids"]) == 12
    assert len(schedules["pilot_schedules"]) == 12
    assert len(resources["records"]) == 4
    assert resources["effect_analysis_included"] is False
    assert dossier["pilot_complete_pairs"] == 0
    assert dossier["confirmatory_complete_pairs"] == 0
    assert dossier["verdict"] == "inconclusive"
    assert dossier["official_search_policy"] == "flat_with_topology_shadow"
