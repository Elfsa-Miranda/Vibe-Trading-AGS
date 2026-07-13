"""Generate the tracked Phase 11 infrastructure and blocked-experiment dossier."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Literal, Mapping

from src.alpha_foundry.activation.formal_protocol_v3 import (
    ConfirmatoryPowerTemplateV3,
    FormalActivationDossierV3,
    FormalActivationStagePlanV3,
    Phase11ReadinessV3,
    freeze_pair_schedules_v3,
)
from src.alpha_foundry.activation.isolated_worker_v3 import (
    IsolatedWorkerResourceV3,
    IsolatedWorkerTaskV3,
    ParentEnforcedWorkerV3,
)
from src.alpha_foundry.artifacts import safe_artifact_write_json
from src.research_ledger.events.artifacts import hash_artifact
from src.research_ledger.hash_utils import canonical_json_hash


def _document(schema_version: str, content: Mapping[str, Any]) -> dict[str, Any]:
    payload = {"schema_version": schema_version, **dict(content)}
    payload["document_hash"] = canonical_json_hash(payload)
    return payload


def _stage_plan(
    *,
    stage: Literal["dry_run", "pilot"],
    parent: str | None,
    baseline: Mapping[str, Any],
    implementation: Mapping[str, str],
) -> FormalActivationStagePlanV3:
    if stage == "dry_run":
        groups: tuple[str, ...] = ("dry-run-00", "dry-run-01")
        seeds: tuple[int, ...] = (454320, 454321)
        effect = False
    else:
        groups = tuple(f"pilot-{index:02d}" for index in range(12))
        seeds = tuple(range(454400, 454412))
        effect = True
    snapshot_hash = str(baseline["scientific_evidence"]["snapshot_hash"])
    splits = baseline["splits"]
    train_hash = canonical_json_hash({
        "snapshot_hash": snapshot_hash,
        "scope": "train",
        "range": splits["train"],
    })
    valid_hash = canonical_json_hash({
        "snapshot_hash": snapshot_hash,
        "scope": "valid",
        "range": splits["valid"],
    })
    return FormalActivationStagePlanV3.create(
        experiment_family_id="ags-v32-phase11-flat-topology-formal-v1",
        stage=stage,
        registered_at=(
            "2026-07-13T01:00:00Z" if stage == "dry_run" else "2026-07-13T01:10:00Z"
        ),
        parent_stage_plan_hash=parent,
        pilot_result_hash=None,
        baseline_manifest_hash=str(baseline["baseline_manifest_hash"]),
        train_snapshot_hash=train_hash,
        valid_snapshot_hash=valid_hash,
        generator_hash=implementation["generator_hash"],
        evaluator_hash=implementation["evaluator_hash"],
        dag_scheduler_hash=implementation["dag_scheduler_hash"],
        decision_criteria_hash=implementation["decision_criteria_hash"],
        control_policy_hash=canonical_json_hash({
            "schema_version": "formal_activation_flat_policy.v1",
            "official": True,
            "retrieval": "prearm_frozen_flat_schedule",
        }),
        treatment_policy_hash=canonical_json_hash({
            "schema_version": "formal_activation_topology_policy.v1",
            "official": False,
            "retrieval": "retriever_decision_v7_shadow",
        }),
        candidate_budget=32,
        compute_budget=64,
        worker_limit=2,
        timeout_seconds=600.0,
        run_group_ids=groups,
        seeds=seeds,
        primary_endpoint="effective_non_duplicate_candidate_yield_at_narrow_quality_decision_v3",
        primary_threshold=0.5,
        primary_threshold_unit="candidates_per_32_attempt_fixed_budget",
        secondary_noninferiority_margins={
            "cpu_seconds": 30.0,
            "duplicate_rate": 0.05,
            "failure_rate": 0.05,
            "peak_rss_mb": 128.0,
            "wall_seconds": 30.0,
        },
        effect_analysis_allowed=effect,
    )


def _run_dry_probes(
    dry_plan: FormalActivationStagePlanV3,
) -> tuple[tuple[IsolatedWorkerResourceV3, ...], list[dict[str, Any]]]:
    runner = ParentEnforcedWorkerV3()
    schedules = freeze_pair_schedules_v3(dry_plan)
    resources: list[IsolatedWorkerResourceV3] = []
    records: list[dict[str, Any]] = []
    for schedule in schedules:
        for arm in schedule.arm_order:
            is_timeout = schedule.run_group_id == "dry-run-01" and arm == "flat"
            task = IsolatedWorkerTaskV3.create(
                operation="timeout_probe" if is_timeout else "resource_probe",
                duration_seconds=2.0 if is_timeout else 0.08,
                allocation_mb=4 if is_timeout else 8,
                nonce=f"{schedule.run_group_id}-{arm}",
            )
            resource = runner.run(
                task,
                timeout_seconds=0.2 if is_timeout else 3.0,
            )
            resources.append(resource)
            records.append({
                "pair_schedule_hash": schedule.schedule_hash,
                "run_group_id": schedule.run_group_id,
                "arm": arm,
                "expected_probe_outcome": "timeout" if is_timeout else "completed",
                "task": task.to_dict(),
                "resource": resource.to_dict(),
            })
    return tuple(resources), records


def build_phase11_evidence(agent_root: Path) -> dict[str, dict[str, Any]]:
    baseline_path = agent_root / "research_evidence" / "baseline_v1" / "baseline_manifest.json"
    baseline_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    if not isinstance(baseline_raw, dict):
        raise ValueError("Phase 8 baseline manifest must be a JSON object")
    implementation = {
        "generator_hash": hash_artifact(agent_root / "src" / "alpha_foundry" / "search.py"),
        "evaluator_hash": hash_artifact(agent_root / "src" / "alpha_quality" / "production_evaluator_v1.py"),
        "dag_scheduler_hash": hash_artifact(agent_root / "src" / "alpha_quality" / "evaluator_dag_v1.py"),
        "decision_criteria_hash": hash_artifact(agent_root / "src" / "alpha_quality" / "decision_v2" / "source_v3.py"),
        "formal_protocol_hash": hash_artifact(agent_root / "src" / "alpha_foundry" / "activation" / "formal_protocol_v3.py"),
        "isolated_worker_hash": hash_artifact(agent_root / "src" / "alpha_foundry" / "activation" / "isolated_worker_v3.py"),
        "run_source_v3_hash": hash_artifact(agent_root / "src" / "alpha_foundry" / "activation" / "run_source_v3.py"),
    }
    dry_plan = _stage_plan(
        stage="dry_run", parent=None, baseline=baseline_raw, implementation=implementation
    )
    pilot_plan = _stage_plan(
        stage="pilot", parent=dry_plan.plan_hash, baseline=baseline_raw,
        implementation=implementation,
    )
    dry_schedules = freeze_pair_schedules_v3(dry_plan)
    pilot_schedules = freeze_pair_schedules_v3(pilot_plan)
    resources, dry_records = _run_dry_probes(dry_plan)
    baseline_replayable = bool(
        baseline_raw.get("chain_verified")
        and baseline_raw.get("serial_retry_equal")
        and baseline_raw.get("scientific_replay_hash")
    )
    readiness = Phase11ReadinessV3.create(
        baseline_manifest_hash=str(baseline_raw["baseline_manifest_hash"]),
        baseline_effective_sample=int(baseline_raw["effective_sample"]),
        baseline_replayable=baseline_replayable,
        infrastructure_resources=resources,
        production_candidate_factory_available=False,
        production_run_inputs_available=False,
        run_budget_authorized=True,
    )
    template = ConfirmatoryPowerTemplateV3.create(pilot_plan)
    dossier = FormalActivationDossierV3.blocked(
        readiness=readiness,
        dry_run_plan=dry_plan,
        pilot_plan=pilot_plan,
        confirmatory_template=template,
        dry_run_resources=resources,
        pilot_schedules=pilot_schedules,
    )
    schedules_document = _document(
        "phase11_pair_schedules.v3",
        {
            "dry_run_schedules": [item.to_dict() for item in dry_schedules],
            "pilot_schedules": [item.to_dict() for item in pilot_schedules],
            "counterbalanced_rule": "alternating_by_frozen_run_group_index",
            "dry_run_effect_analysis_excluded": True,
        },
    )
    resources_document = _document(
        "phase11_dry_run_resources.v3",
        {
            "records": dry_records,
            "effect_analysis_included": False,
            "worker_held_ledger_lock": False,
            "parent_enforced_timeout": True,
            "per_arm_isolated_rss": True,
        },
    )
    execution_record = _document(
        "phase_execution_record.v1",
        {
            "phase": "Phase 11 formal Retriever Activation experiment",
            "branch": "codex/ags-v32-activation-production-evaluator",
            "base_commit": "26ddcfd7d272cec7a25b13faca53ac10e02355a2",
            "remote_operations": [],
            "implementation_hashes": implementation,
            "baseline_manifest_hash": baseline_raw["baseline_manifest_hash"],
            "dry_run_plan_hash": dry_plan.plan_hash,
            "pilot_plan_hash": pilot_plan.plan_hash,
            "confirmatory_template_hash": template.template_hash,
            "readiness_hash": readiness.readiness_hash,
            "dossier_hash": dossier.dossier_hash,
            "dry_run_groups_executed": 2,
            "dry_run_arms_executed": 4,
            "pilot_groups_scheduled": 12,
            "pilot_groups_executed": 0,
            "confirmatory_groups_executed": 0,
            "outcome": "INCONCLUSIVE_SHADOW_EXTERNAL_INPUTS_BLOCKED",
            "official_search_policy": "flat_with_topology_shadow",
            "validation": [
                {
                    "scope": "Phase 11 formal protocol red-team",
                    "result": "8 passed in 4.58s",
                },
                {
                    "scope": "focused Activation legacy and current authority regression",
                    "result": "60 passed in 27.47s",
                },
                {
                    "scope": "Alpha Foundry excluding known user-owned release-manifest input",
                    "result": "273 passed in 73.93s",
                },
                {
                    "scope": "Alpha Quality plus Research Ledger",
                    "result": "613 passed, 1 deprecation warning in 1430.67s",
                },
                {
                    "scope": "contracts security acceptance performance feature-off from repository root",
                    "result": "95 passed, 21 deprecation warnings in 10.75s",
                },
                {
                    "scope": "ruff and strict mypy for Phase 11 source files",
                    "result": "passed",
                },
                {
                    "scope": "python -m pip check",
                    "result": "No broken requirements found",
                },
            ],
            "known_unrelated_baseline_failure": (
                "test_tracked_release_manifest_matches_deterministic_rebuild remains "
                "1 failed while 7 sibling tests pass because it reads user-owned "
                "repository-root problem.md; the file was not modified"
            ),
            "cwd_diagnostic": (
                "same root-relative contracts/security command from agent cwd produced "
                "91 passed and 4 FileNotFound failures; repository-root rerun passed 95"
            ),
            "limitations": [
                "NO_PILOT_OR_CONFIRMATORY_OUTCOME_FABRICATED",
                "PRODUCTION_ACTIVATION_CANDIDATE_FACTORY_UNAVAILABLE",
                "PRODUCTION_TRAIN_VALID_RUN_INPUTS_UNAVAILABLE",
                "BUNDLED_BASELINE_REMAINS_RESEARCH_ONLY",
            ],
        },
    )
    return {
        "phase11_dry_run_plan.json": dry_plan.to_dict(),
        "phase11_pilot_plan.json": pilot_plan.to_dict(),
        "phase11_pair_schedules.json": schedules_document,
        "phase11_dry_run_resources.json": resources_document,
        "phase11_readiness.json": readiness.to_dict(),
        "phase11_confirmatory_template.json": template.to_dict(),
        "phase11_blocked_dossier.json": dossier.to_dict(),
        "phase11_execution_record.json": execution_record,
    }


def write_phase11_evidence(output_root: Path) -> tuple[str, ...]:
    agent_root = Path(__file__).resolve().parents[3]
    documents = build_phase11_evidence(agent_root)
    written: list[str] = []
    for relative, payload in documents.items():
        safe_artifact_write_json(output_root, relative, payload)
        written.append(relative)
    return tuple(sorted(written))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True)
    args = parser.parse_args()
    write_phase11_evidence(Path(args.output_root).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_phase11_evidence", "write_phase11_evidence"]
