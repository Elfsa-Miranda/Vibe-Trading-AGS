from __future__ import annotations

from pathlib import Path

from scripts.validate_execplans import validate_plan, validate_repository


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


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
                    f"| REQ-TEST-01 | `x.py` | TEST-TEST-01 | EVID-TEST-01 | {state} |",
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


def test_complete_plan_cannot_retain_non_pass_acceptance(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(status="COMPLETE").replace("State: PASS", "State: PROPOSED"), encoding="utf-8")

    result = validate_plan(path)

    assert "FALSE_COMPLETE" in result.errors


def test_active_plan_rejects_unresolved_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "ExecPlan.md"
    path.write_text(_plan(status="ACTIVE", extra="\nTO_BE_CAPTURED\n"), encoding="utf-8")

    result = validate_plan(path)

    assert "UNRESOLVED_PLACEHOLDER" in result.errors


def test_repository_report_uses_repository_relative_paths() -> None:
    report = validate_repository(REPOSITORY_ROOT)

    assert all(not Path(plan.path).is_absolute() for plan in report.plans)
