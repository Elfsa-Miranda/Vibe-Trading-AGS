"""Verify that captured baseline evidence still binds to this checkout."""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path
from typing import Any

if __package__:
    from .capture_agent_baseline import (
        GENERATOR_VERSION,
        _aggregate_result,
        _canonical_json,
        _closed_schema_valid,
        _dependencies,
        _environment,
        _redact,
        _render_summary,
        _strict_json,
        _tree_snapshot,
    )
else:
    from capture_agent_baseline import (
        GENERATOR_VERSION,
        _aggregate_result,
        _canonical_json,
        _closed_schema_valid,
        _dependencies,
        _environment,
        _redact,
        _render_summary,
        _strict_json,
        _tree_snapshot,
    )


SCHEMA_VERSION = "ags.agent-baseline.v1"
EVIDENCE_ROOT = Path("agent/research_evidence/agent_baseline")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_artifact(root: Path, relative: str) -> Path:
    candidate = (root / relative).resolve(strict=False)
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("ARTIFACT_PATH_ESCAPE") from exc
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("ARTIFACT_PATH_ESCAPE")
    return candidate


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True, encoding="utf-8", capture_output=True, check=True).stdout.strip()


def _artifact_error(root: Path, evidence_directory: Path, artifact: Any, check_sensitive_text: bool = False) -> str | None:
    if not isinstance(artifact, dict):
        return "EVIDENCE_HASH_MISMATCH"
    try:
        path = _resolve_artifact(root, artifact["path"])
        path.relative_to(evidence_directory)
        if not path.is_file() or path.stat().st_size != artifact.get("size") or _sha256(path) != artifact.get("sha256"):
            return "EVIDENCE_HASH_MISMATCH"
        if check_sensitive_text:
            content = path.read_text(encoding="utf-8")
            if _redact(content, root)[0] != content:
                return "SENSITIVE_EVIDENCE"
    except (KeyError, OSError, UnicodeError, ValueError):
        return "EVIDENCE_HASH_MISMATCH"
    return None


def _command_semantic_error(command: Any) -> str | None:
    if (
        not isinstance(command, dict)
        or not isinstance(command.get("argv"), list)
        or not command["argv"]
        or not all(isinstance(value, str) for value in command["argv"])
        or not isinstance(command.get("started_at"), str)
        or not isinstance(command.get("duration_ms"), int)
        or command["duration_ms"] < 0
        or not isinstance(command.get("redaction_count"), int)
        or command["redaction_count"] < 0
    ):
        return "INVALID_COMMAND_RECORD"
    state = command.get("state")
    exit_code = command.get("exit_code")
    failure_code = command.get("failure_code")
    if state == "PASS" and exit_code == 0 and failure_code is None:
        return None
    if state == "FAIL" and (
        (isinstance(exit_code, int) and exit_code != 0 and failure_code is None)
        or (exit_code is None and failure_code == "TIMEOUT")
    ):
        return None
    if state == "BLOCKED" and exit_code is None and failure_code in {"TIMEOUT", "MISSING_TOOL"}:
        return None
    return "COMMAND_STATE_MISMATCH"


def verify(root: Path, manifest_path: Path) -> tuple[str, ...]:
    root = root.resolve()
    manifest_path = manifest_path.resolve()
    controlled_root = (root / EVIDENCE_ROOT).resolve()
    try:
        manifest_path.relative_to(controlled_root)
    except ValueError as exc:
        raise ValueError("MANIFEST_PATH_ESCAPE") from exc
    payload = _strict_json(manifest_path)
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION_MISMATCH")
        return tuple(errors)
    if _redact(_canonical_json(payload), root)[0] != _canonical_json(payload):
        errors.append("SENSITIVE_MANIFEST")
    if not _closed_schema_valid(payload):
        errors.append("INVALID_MANIFEST_SHAPE")
        return tuple(dict.fromkeys(errors))
    repository = payload.get("repository")
    commands = payload.get("commands")
    if not isinstance(repository, dict) or not isinstance(commands, list) or not commands or not isinstance(payload.get("dependencies"), list):
        return ("INVALID_MANIFEST_SHAPE",)
    evidence_root = repository.get("evidence_root")
    expected_evidence_root = manifest_path.parent.relative_to(root).as_posix()
    if evidence_root != expected_evidence_root or not expected_evidence_root.startswith(EVIDENCE_ROOT.as_posix() + "/"):
        errors.append("UNCONTROLLED_EVIDENCE_ROOT")
    try:
        if _git(root, "rev-parse", "HEAD") != payload.get("repository", {}).get("commit"):
            errors.append("BASE_SHA_MISMATCH")
        tree_status, tree_diff_hash = _tree_snapshot(root, EVIDENCE_ROOT.as_posix())
        if tree_status != repository.get("tree_status") or tree_diff_hash != repository.get("tree_diff_hash"):
            errors.append("TREE_STATE_MISMATCH")
    except subprocess.SubprocessError:
        errors.append("GIT_STATE_UNAVAILABLE")
    states: list[str] = []
    for command in commands:
        semantic_error = _command_semantic_error(command)
        if semantic_error:
            errors.append(semantic_error)
        if not isinstance(command, dict):
            continue
        if isinstance(command.get("state"), str):
            states.append(command["state"])
        if any(_redact(value, root)[0] != value for value in command.get("argv", []) if isinstance(value, str)):
            errors.append("SENSITIVE_EVIDENCE")
        for field in ("stdout_artifact", "stderr_artifact"):
            artifact_error = _artifact_error(root, manifest_path.parent, command.get(field, {}), check_sensitive_text=True)
            if artifact_error:
                errors.append(artifact_error)
    expected_result = _aggregate_result(states)
    expected_counts = {state: states.count(state) for state in ("PASS", "FAIL", "BLOCKED")}
    if payload.get("result") != expected_result:
        errors.append("RESULT_STATE_MISMATCH")
    if payload.get("summary") != {"command_count": len(commands), "state_counts": expected_counts}:
        errors.append("SUMMARY_MISMATCH")
    if payload.get("redaction_count") != sum(command.get("redaction_count", -1) for command in commands if isinstance(command, dict)):
        errors.append("REDACTION_COUNT_MISMATCH")
    if payload.get("generator_version") != GENERATOR_VERSION:
        errors.append("GENERATOR_VERSION_MISMATCH")
    if repository.get("clean") is not (not repository.get("tree_status")):
        errors.append("CLEAN_STATE_MISMATCH")
    environment = payload.get("environment")
    if environment != _environment():
        errors.append("ENVIRONMENT_MISMATCH")
    capture_policy = payload.get("capture_policy")
    if (
        not isinstance(capture_policy.get("timeout_seconds"), (int, float))
        or capture_policy["timeout_seconds"] <= 0
        or capture_policy.get("timeout_state") not in {"FAIL", "BLOCKED"}
    ):
        errors.append("INVALID_CAPTURE_POLICY")
    summary_error = _artifact_error(root, manifest_path.parent, payload.get("summary_artifact"), check_sensitive_text=True)
    if summary_error:
        errors.append(summary_error)
    elif isinstance(environment, dict):
        summary_path = root / payload["summary_artifact"]["path"]
        expected_summary = _render_summary(repository.get("commit", ""), repository.get("tree_status", []), expected_result, expected_counts, environment)
        if summary_path.read_text(encoding="utf-8") != expected_summary:
            errors.append("SUMMARY_MISMATCH")
    for dependency in payload.get("dependencies", []):
        try:
            path = _resolve_artifact(root, dependency["path"])
            if not path.is_file() or path.stat().st_size != dependency.get("size") or _sha256(path) != dependency.get("sha256"):
                errors.append("DEPENDENCY_HASH_MISMATCH")
        except (KeyError, OSError, ValueError):
            errors.append("DEPENDENCY_HASH_MISMATCH")
    if payload.get("dependencies") != _dependencies(root):
        errors.append("DEPENDENCY_HASH_MISMATCH")
    return tuple(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        errors = verify(args.root, args.manifest)
    except (OSError, ValueError) as exc:
        errors = ("MANIFEST_UNREADABLE",)
        print(f"verification detail: {exc}", file=sys.stderr)
    for error in errors:
        print(error)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
