from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from scripts.validate_execplans import validate_plan, validate_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
VALIDATOR = REPOSITORY_ROOT / "scripts" / "validate_execplans.py"


def _plan(status: str = "PROPOSED", extra: str = "") -> str:
    sections = [
        "Purpose / Big Picture",
        "Implementation Scope Contract",
        "Current System Evidence",
        "Terminology",
        "Dependencies and Prerequisite Gate",
        "Invariants",
        "Deliverables",
        "Non-Goals and Prohibited Changes",
        "Architecture and Data Flow",
        "Interfaces and Schemas",
        "Milestones",
        "Detailed Tasks",
        "Failure Semantics",
        "Security, Privacy, and Threat Model",
        "Test Strategy",
        "Validation and Acceptance",
        "Requirement Traceability Matrix",
        "Concrete Execution Commands",
        "Idempotence, Rollback, and Recovery",
        "Observability and Evidence Artifacts",
        "Risks and Mitigations",
        "Progress",
        "Surprises & Discoveries",
        "Decision Log",
        "Outcomes & Retrospective",
        "Plan Revision Log",
    ]
    rendered = [
        "# Example plan",
        "",
        "**Plan-ID:** AGS-AR-99",
        f"**Status:** {status}",
        "**Stage:** 99",
        "**Owner:** test",
        "**Created:** 2026-08-24",
        "**Last-Updated:** 2026-08-24",
        "**Base-Ref:** test",
        "**Base-SHA:** deadbeef",
        "**Depends-On:** NONE",
        "**Supersedes:** NONE",
        "**Target-Outcome:** test governance",
        "",
    ]
    for section in sections:
        rendered.extend([f"## {section}", ""])
        if section == "Implementation Scope Contract":
            rendered.append("- REQ-TEST-01: deterministic validation")
        elif section == "Invariants":
            rendered.append("- INV-TEST-01: invalid input fails")
        elif section == "Deliverables":
            rendered.append("- DEL-TEST-01: validator")
        elif section == "Non-Goals and Prohibited Changes":
            rendered.append("- NC-TEST-01: no runtime behavior")
        elif section == "Test Strategy":
            rendered.append("- TEST-TEST-01: rejects malformed input")
        elif section == "Validation and Acceptance":
            state = "PASS" if status == "COMPLETE" else "PROPOSED"
            rendered.append(f"- AC-TEST-01: deterministic result. State: {state}")
        elif section == "Requirement Traceability Matrix":
            state = "PASS" if status == "COMPLETE" else "PROPOSED"
            rendered.extend(
                [
                    "| Requirement | Implementation | Test | Evidence | State |",
                    "|---|---|---|---|---|",
                    f"| REQ-TEST-01 | `x.py` | TEST-TEST-01 behavior test | EVID-TEST-01 manifest record | {state} |",
                    f"| INV-TEST-01 | `x.py` | TEST-TEST-01 | EVID-TEST-01 | {state} |",
                    f"| AC-TEST-01 | `x.py` | TEST-TEST-01 | EVID-TEST-01 | {state} |",
                ]
            )
        elif section == "Observability and Evidence Artifacts":
            rendered.append("- EVID-TEST-01: manifest")
        elif section == "Risks and Mitigations":
            rendered.append("- RISK-TEST-01: malformed plan")
        else:
            rendered.append("Required content.")
        rendered.append("")
    return "\n".join(rendered) + extra


def test_repository_stage_plans_validate_without_errors() -> None:
    report = validate_repository(REPOSITORY_ROOT)

    assert len(report.plans) == 8
    assert report.errors == ()


def test_missing_required_section_has_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan().replace("## Detailed Tasks\n\nRequired content.\n\n", ""), encoding="utf-8")

    result = validate_plan(path)

    assert "MISSING_SECTION" in result.errors


def test_duplicate_requirement_definition_has_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(extra="\n- REQ-TEST-01: duplicate\n"), encoding="utf-8")

    result = validate_plan(path)

    assert "DUPLICATE_ID" in result.errors


def test_incomplete_traceability_has_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan().replace("REQ-TEST-01 | `x.py`", "REQ-OTHER-01 | `x.py`"), encoding="utf-8")

    result = validate_plan(path)

    assert "MISSING_TRACEABILITY" in result.errors


def test_missing_invariant_or_acceptance_traceability_has_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan().replace("| INV-TEST-01 |", "| INV-OTHER-01 |").replace("| AC-TEST-01 |", "| AC-OTHER-01 |"), encoding="utf-8")

    result = validate_plan(path)

    assert "MISSING_TRACEABILITY" in result.errors


def test_prose_identifier_is_not_a_traceability_mapping(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan().replace("| INV-TEST-01 |", "| INV-OTHER-01 |").replace("| AC-TEST-01 |", "| AC-OTHER-01 |")
    path.write_text(content + "\nSupplemental linked identifiers: INV-TEST-01, AC-TEST-01.\n", encoding="utf-8")

    result = validate_plan(path)

    assert "MISSING_TRACEABILITY" in result.errors


def test_complete_plan_cannot_retain_non_pass_acceptance(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(status="COMPLETE").replace("| PASS |", "| PROPOSED |", 1), encoding="utf-8")

    result = validate_plan(path)

    assert "FALSE_COMPLETE" in result.errors


def test_complete_plan_with_all_pass_rows_is_valid(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(status="COMPLETE"), encoding="utf-8")

    result = validate_plan(path)

    assert result.errors == ()


def test_complete_plan_accepts_repository_style_without_bullet_state(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan(status="COMPLETE").replace("deterministic result. State: PASS", "deterministic result.")
    path.write_text(content, encoding="utf-8")

    result = validate_plan(path)

    assert result.errors == ()


def test_traceability_requires_populated_typed_columns(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan().replace(
        "| REQ-TEST-01 | `x.py` | TEST-TEST-01 behavior test | EVID-TEST-01 manifest record |",
        "| REQ-TEST-01 |  | TEST-TEST-01 behavior test | EVID-TEST-01 manifest record |",
    )
    path.write_text(content, encoding="utf-8")

    result = validate_plan(path)

    assert "MALFORMED_TRACEABILITY" in result.errors


def test_traceability_rejects_range_shorthand(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan().replace("TEST-TEST-01 behavior test", "TEST-TEST-01..03 behavior test", 1)
    path.write_text(content, encoding="utf-8")

    result = validate_plan(path)

    assert "MALFORMED_TRACEABILITY" in result.errors


def test_traceability_rejects_undeclared_test_and_evidence_entities(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan().replace(
        "| INV-TEST-01 | `x.py` | TEST-TEST-01 | EVID-TEST-01 |",
        "| INV-TEST-01 | `x.py` | TEST-BOGUS-99 | EVID-BOGUS-99 |",
    )
    path.write_text(content, encoding="utf-8")

    result = validate_plan(path)

    assert "UNKNOWN_TRACE_ENTITY" in result.errors


def test_traceability_rejects_matching_but_undefined_test_or_evidence(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    content = _plan().replace("TEST-TEST-01 behavior test", "TEST-TEST-01", 1)
    path.write_text(content, encoding="utf-8")

    result = validate_plan(path)

    assert "UNKNOWN_TRACE_ENTITY" in result.errors


def test_active_plan_rejects_unresolved_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(status="ACTIVE", extra="\nTO_BE_CAPTURED\n"), encoding="utf-8")

    result = validate_plan(path)

    assert "UNRESOLVED_PLACEHOLDER" in result.errors


def test_repository_report_uses_repository_relative_paths() -> None:
    report = validate_repository(REPOSITORY_ROOT)

    assert all(not Path(plan.path).is_absolute() for plan in report.plans)


def test_cross_plan_duplicate_identifier_has_typed_error(tmp_path: Path) -> None:
    plans_root = tmp_path / ".agent" / "execplans"
    for stage in range(8):
        plan = _plan().replace("AGS-AR-99", f"AGS-AR-{stage:02d}").replace("**Stage:** 99", f"**Stage:** {stage:02d}")
        target = plans_root / f"{stage:02d}-stage" / "ExecPlan.md"
        target.parent.mkdir(parents=True)
        target.write_text(plan, encoding="utf-8")

    report = validate_repository(tmp_path)

    assert "CROSS_PLAN_DUPLICATE_ID" in report.errors


def test_validator_output_is_restricted_to_governance_evidence_root(tmp_path: Path) -> None:
    result = subprocess.run(
        [sys.executable, str(VALIDATOR), "--root", str(REPOSITORY_ROOT), "--output", str(REPOSITORY_ROOT / "AGENTS.md")],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "UNCONTROLLED_OUTPUT_PATH" in result.stderr


def test_governance_workflow_is_pinned_offline_and_failure_preserving() -> None:
    workflow = (REPOSITORY_ROOT / ".github" / "workflows" / "agent-governance.yml").read_text(encoding="utf-8")

    assert "pip install" not in workflow
    assert "python scripts/run_governance_tests.py" in workflow
    assert workflow.count("if: always()") == 3
    assert "governance_tests.txt" in workflow
    assert "unlink(missing_ok=True)" in workflow
    assert "agent-governance-plan-validation" in workflow
    assert "agent-governance-tests" in workflow
    assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
    assert "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065" in workflow
    assert "actions/upload-artifact@ea165f8d65b6e75b540449e92b4886f43607fa02" in workflow
