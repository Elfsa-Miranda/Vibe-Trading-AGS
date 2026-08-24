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


def _traceability_ids(text: str) -> set[str]:
    trace_heading = "## Requirement Traceability Matrix"
    start = text.find(trace_heading)
    if start < 0:
        return set()
    remainder = text[start + len(trace_heading) :]
    next_heading = remainder.find("\n## ")
    table = remainder if next_heading < 0 else remainder[:next_heading]
    rows = [line for line in table.splitlines() if line.lstrip().startswith("|") and line.count("|") >= 6]
    data_rows = [line for line in rows if not re.fullmatch(r"[|\s:-]+", line)]
    return set(re.findall(r"\b(?:REQ|INV|DEL|NC|RISK|AC|TEST|EVID)-[A-Z0-9][A-Z0-9-]*\b", "\n".join(data_rows)))


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
    traceability = _traceability_ids(text)
    missing = [identifier for identifier in definitions if identifier.startswith(("REQ-", "INV-", "AC-")) and identifier not in traceability]
    if missing:
        errors.append("MISSING_TRACEABILITY")
    if status in {"ACTIVE", "COMPLETE"} and re.search(r"^\s*(?:[-*]\s*)?(?:TO_BE_CAPTURED|TBD)\s*$", text, re.MULTILINE):
        errors.append("UNRESOLVED_PLACEHOLDER")
    if status == "COMPLETE":
        acceptance = re.findall(r"^\s*[-*]\s+(AC-[A-Z0-9][A-Z0-9-]*).*?State:\s*([A-Z_]+)", text, re.MULTILINE)
        if not acceptance or any(state != "PASS" for _, state in acceptance):
            errors.append("FALSE_COMPLETE")
        if re.search(r"\|\s*(?:REQ|INV|DEL|NC|RISK|AC|TEST|EVID)-[^|]+\|[^\n]*\|\s*(?!PASS\s*\|)", text):
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
        try:
            target.relative_to(args.root.resolve())
        except ValueError:
            print("OUTPUT_PATH_ESCAPE", file=sys.stderr)
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
