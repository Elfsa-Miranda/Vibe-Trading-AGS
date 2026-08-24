"""Validate the committed Agent Reliability execution plans.

The validator intentionally uses only the standard library so it can run in a
fresh checkout before the application dependencies are installed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path


REQUIRED_SECTIONS = (
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
)
REQUIRED_METADATA = (
    "Plan-ID",
    "Status",
    "Stage",
    "Owner",
    "Created",
    "Last-Updated",
    "Base-Ref",
    "Base-SHA",
    "Depends-On",
    "Supersedes",
    "Target-Outcome",
)
VALID_STATUSES = {"PROPOSED", "ACTIVE", "BLOCKED", "COMPLETE", "SUPERSEDED"}
ID_PATTERN = re.compile(r"\b(?:REQ|INV|DEL|NC|RISK|AC|TEST|EVID)-[A-Z0-9][A-Z0-9-]*\b")
METADATA_PATTERN = re.compile(r"^\*\*(?P<key>[^*]+):\*\*\s*(?P<value>.*)\s*$", re.MULTILINE)


@dataclass(frozen=True)
class PlanValidationResult:
    path: str
    plan_id: str | None
    status: str | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryValidationResult:
    plans: tuple[PlanValidationResult, ...]
    errors: tuple[str, ...]
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class TraceabilityRow:
    requirements: tuple[str, ...]
    implementation: str
    tests: tuple[str, ...]
    evidence: tuple[str, ...]
    test_definition: str
    evidence_definition: str
    state: str


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _metadata(text: str) -> dict[str, str]:
    return {match.group("key").strip(): match.group("value").strip() for match in METADATA_PATTERN.finditer(text)}


def _defined_ids(text: str) -> list[str]:
    trace_heading = "## Requirement Traceability Matrix"
    start = text.find(trace_heading)
    if start >= 0:
        remainder = text[start + len(trace_heading) :]
        next_heading = remainder.find("\n## ")
        text = text[:start] + ("" if next_heading < 0 else remainder[next_heading:])
    pattern = r"^\s*(?:[-*]\s+)?`?((?:REQ|INV|DEL|NC|RISK|AC|TEST|EVID)-[A-Z0-9][A-Z0-9-]*)`?\s*:"
    return [match.group(1) for match in re.finditer(pattern, text, re.MULTILINE)]


def _traceability_rows(text: str) -> tuple[tuple[TraceabilityRow, ...], bool]:
    trace_heading = "## Requirement Traceability Matrix"
    start = text.find(trace_heading)
    if start < 0:
        return (), False
    remainder = text[start + len(trace_heading) :]
    next_heading = remainder.find("\n## ")
    table = remainder if next_heading < 0 else remainder[:next_heading]
    rows: list[TraceabilityRow] = []
    malformed = False
    for line in table.splitlines():
        if not line.lstrip().startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Requirement":
            continue
        if cells and all(re.fullmatch(r"\s*:?-+:?\s*", cell) for cell in cells):
            continue
        if len(cells) != 5:
            malformed = True
            continue
        requirement_cell, implementation, test_cell, evidence_cell, state = cells
        requirement_ids = tuple(ID_PATTERN.findall(requirement_cell))
        tests = tuple(ID_PATTERN.findall(test_cell))
        evidence = tuple(ID_PATTERN.findall(evidence_cell))
        if (
            not requirement_ids
            or any(not identifier.startswith(("REQ-", "INV-", "AC-")) for identifier in requirement_ids)
            or not implementation
            or not tests
            or any(not identifier.startswith("TEST-") for identifier in tests)
            or not evidence
            or any(not identifier.startswith("EVID-") for identifier in evidence)
            or state not in {"PASS", "FAIL", "BLOCKED", "INCONCLUSIVE", "PROPOSED"}
            or ".." in line
        ):
            malformed = True
        rows.append(TraceabilityRow(requirement_ids, implementation, tests, evidence, test_cell, evidence_cell, state))
    return tuple(rows), malformed


def validate_plan(path: Path) -> PlanValidationResult:
    """Return deterministic typed validation codes for one plan."""
    text = path.read_text(encoding="utf-8")
    metadata = _metadata(text)
    errors: list[str] = []
    warnings: list[str] = []
    for name in REQUIRED_METADATA:
        if not metadata.get(name):
            errors.append("MISSING_METADATA")
    for section in REQUIRED_SECTIONS:
        count = len(re.findall(rf"^##\s+{re.escape(section)}\s*$", text, re.MULTILINE))
        if count == 0:
            errors.append("MISSING_SECTION")
        elif count > 1:
            errors.append("DUPLICATE_SECTION")

    plan_id = metadata.get("Plan-ID")
    status = metadata.get("Status")
    stage = metadata.get("Stage")
    if plan_id and not re.fullmatch(r"AGS-AR-\d{2}", plan_id):
        errors.append("INVALID_PLAN_ID")
    if plan_id and stage and plan_id[-2:] != stage.zfill(2):
        errors.append("STAGE_ID_MISMATCH")
    if status and status not in VALID_STATUSES:
        errors.append("INVALID_STATUS")

    definitions = _defined_ids(text)
    if len(definitions) != len(set(definitions)):
        errors.append("DUPLICATE_ID")
    traceability_rows, malformed_traceability = _traceability_rows(text)
    traceability = {identifier for row in traceability_rows for identifier in row.requirements}
    missing = [identifier for identifier in definitions if identifier.startswith(("REQ-", "INV-", "AC-")) and identifier not in traceability]
    if missing:
        errors.append("MISSING_TRACEABILITY")
    if malformed_traceability:
        errors.append("MALFORMED_TRACEABILITY")
    declared_tests: set[str] = set()
    declared_evidence: set[str] = set()
    for row in traceability_rows:
        requirement_ids = [identifier for identifier in row.requirements if identifier.startswith("REQ-")]
        if len(requirement_ids) == 1 and len(row.requirements) == 1:
            suffix = requirement_ids[0][len("REQ-") :]
            expected_test = f"TEST-{suffix}"
            expected_evidence = f"EVID-{suffix}"
            test_defined = bool(re.fullmatch(rf"{re.escape(expected_test)}\s+\S.*", row.test_definition))
            evidence_defined = bool(re.fullmatch(rf"{re.escape(expected_evidence)}\s+\S.*", row.evidence_definition))
            if set(row.tests) != {expected_test} or set(row.evidence) != {expected_evidence} or not test_defined or not evidence_defined:
                errors.append("UNKNOWN_TRACE_ENTITY")
            if test_defined:
                declared_tests.add(expected_test)
            if evidence_defined:
                declared_evidence.add(expected_evidence)
    if any(
        not set(row.tests).issubset(declared_tests) or not set(row.evidence).issubset(declared_evidence)
        for row in traceability_rows
    ):
        errors.append("UNKNOWN_TRACE_ENTITY")
    if status in {"ACTIVE", "COMPLETE"} and re.search(r"^\s*(?:[-*]\s*)?(?:TO_BE_CAPTURED|TBD)\s*$", text, re.MULTILINE):
        errors.append("UNRESOLVED_PLACEHOLDER")
    if status == "COMPLETE":
        acceptance_rows = [row for row in traceability_rows if any(identifier.startswith("AC-") for identifier in row.requirements)]
        if not acceptance_rows or any(row.state != "PASS" for row in traceability_rows):
            errors.append("FALSE_COMPLETE")
    return PlanValidationResult(str(path), plan_id, status, _unique(errors), _unique(warnings))


def validate_repository(root: Path) -> RepositoryValidationResult:
    root = root.resolve()
    plans = tuple(
        PlanValidationResult(path.relative_to(root).as_posix(), result.plan_id, result.status, result.errors, result.warnings)
        for path in sorted((root / ".agent" / "execplans").glob("*/ExecPlan.md"))
        for result in (validate_plan(path),)
    )
    errors: list[str] = []
    warnings: list[str] = []
    if len(plans) != 8:
        errors.append("STAGE_PLAN_COUNT_MISMATCH")
    identifiers = [plan.plan_id for plan in plans if plan.plan_id]
    if len(identifiers) != len(set(identifiers)):
        errors.append("DUPLICATE_PLAN_ID")
    definition_owners: dict[str, str] = {}
    for plan in plans:
        source = root / plan.path
        for identifier in _defined_ids(source.read_text(encoding="utf-8")):
            owner = definition_owners.setdefault(identifier, plan.path)
            if owner != plan.path:
                errors.append("CROSS_PLAN_DUPLICATE_ID")
    for plan in plans:
        errors.extend(f"{Path(plan.path).parent.name}:{code}" for code in plan.errors)
        warnings.extend(f"{Path(plan.path).parent.name}:{code}" for code in plan.warnings)
    return RepositoryValidationResult(plans, _unique(errors), _unique(warnings))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--format", choices=("text", "json"), default="text")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_repository(args.root.resolve())
    serialized = json.dumps(asdict(report), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    if args.output:
        target = args.output.resolve(strict=False)
        controlled_root = (args.root.resolve() / "agent" / "research_evidence" / "governance").resolve()
        try:
            target.relative_to(controlled_root)
        except ValueError:
            print("UNCONTROLLED_OUTPUT_PATH", file=sys.stderr)
            return 2
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(serialized + "\n", encoding="utf-8")
    if args.format == "json":
        print(serialized)
    else:
        for plan in report.plans:
            print(f"{plan.path}: {'PASS' if not plan.errors else ', '.join(plan.errors)}")
        print(f"repository: {'PASS' if not report.errors else ', '.join(report.errors)}")
    return 0 if not report.errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
