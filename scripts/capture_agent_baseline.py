"""Capture reproducible, integrity-bound local baseline evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ags.agent-baseline.v1"
GENERATOR_VERSION = "capture_agent_baseline.v1"
EVIDENCE_ROOT = Path("agent/research_evidence/agent_baseline")
SECRET_PATTERN = re.compile(
    r"(?ix)\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)\b"
    r"[\"']?\s*(?:=|:|\s)\s*(?:bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
WINDOWS_PATH_PATTERN = re.compile(r"(?im)(?<![\w])(?:[A-Z]:[\\/]|\\\\)[^\r\n]*")
POSIX_PATH_PATTERN = re.compile(r"(?m)(?<![:/\w.])/(?!/)[^\r\n]*")
SENSITIVE_OPTION = re.compile(
    r"(?i)^--?(?:api[_-]?key|access[_-]?token|refresh[_-]?token|token|password|passwd|secret|authorization)$"
)
TOP_LEVEL_FIELDS = {
    "schema_version",
    "repository",
    "environment",
    "capture_policy",
    "generator_version",
    "dependencies",
    "commands",
    "summary",
    "summary_artifact",
    "redaction_count",
    "result",
}
REPOSITORY_FIELDS = {"commit", "tree_status", "tree_diff_hash", "clean", "evidence_root"}
ENVIRONMENT_FIELDS = {"python", "node", "npm", "platform"}
POLICY_FIELDS = {"timeout_seconds", "timeout_state", "blocker_reason", "blocker_owner"}
COMMAND_FIELDS = {
    "argv",
    "started_at",
    "exit_code",
    "state",
    "duration_ms",
    "redaction_count",
    "failure_code",
    "stdout_artifact",
    "stderr_artifact",
}
ARTIFACT_FIELDS = {"path", "sha256", "size", "media_type"}
DEPENDENCY_FIELDS = {"path", "sha256", "size"}
SUMMARY_FIELDS = {"command_count", "state_counts"}
STATE_COUNT_FIELDS = {"PASS", "FAIL", "BLOCKED"}


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _reject_constant(_: str) -> None:
    raise ValueError("NONFINITE_JSON")


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _strict_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"), parse_constant=_reject_constant, object_pairs_hook=_unique_object)
    if not isinstance(payload, dict):
        raise ValueError("INVALID_MANIFEST_SHAPE")
    return payload


def _has_exact_fields(value: Any, fields: set[str]) -> bool:
    return isinstance(value, dict) and set(value) == fields


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_hash(value: Any, lengths: tuple[int, ...] = (64,)) -> bool:
    return isinstance(value, str) and len(value) in lengths and bool(re.fullmatch(r"[0-9a-f]+", value))


def _artifact_schema_valid(value: Any) -> bool:
    return (
        _has_exact_fields(value, ARTIFACT_FIELDS)
        and isinstance(value["path"], str)
        and bool(value["path"])
        and _is_hash(value["sha256"])
        and _is_nonnegative_int(value["size"])
        and value["media_type"] == "text/plain; charset=utf-8"
    )


def _closed_schema_valid(payload: dict[str, Any]) -> bool:
    if not _has_exact_fields(payload, TOP_LEVEL_FIELDS):
        return False
    if not _has_exact_fields(payload.get("repository"), REPOSITORY_FIELDS):
        return False
    repository = payload["repository"]
    if (
        not _is_hash(repository["commit"], (40, 64))
        or not isinstance(repository["tree_status"], list)
        or not all(isinstance(line, str) for line in repository["tree_status"])
        or not _is_hash(repository["tree_diff_hash"])
        or not isinstance(repository["clean"], bool)
        or not isinstance(repository["evidence_root"], str)
        or not repository["evidence_root"]
    ):
        return False
    if not _has_exact_fields(payload.get("environment"), ENVIRONMENT_FIELDS):
        return False
    environment = payload["environment"]
    if (
        not isinstance(environment["python"], str)
        or not environment["python"]
        or not isinstance(environment["platform"], str)
        or not environment["platform"]
        or any(environment[key] is not None and not isinstance(environment[key], str) for key in ("node", "npm"))
    ):
        return False
    if not _has_exact_fields(payload.get("capture_policy"), POLICY_FIELDS):
        return False
    policy = payload["capture_policy"]
    if (
        not isinstance(policy["timeout_seconds"], (int, float))
        or isinstance(policy["timeout_seconds"], bool)
        or policy["timeout_seconds"] <= 0
        or policy["timeout_state"] not in {"FAIL", "BLOCKED"}
        or any(policy[key] is not None and not isinstance(policy[key], str) for key in ("blocker_reason", "blocker_owner"))
    ):
        return False
    if policy["timeout_state"] == "BLOCKED":
        if not policy["blocker_reason"] or not policy["blocker_owner"]:
            return False
    elif policy["blocker_reason"] is not None or policy["blocker_owner"] is not None:
        return False
    if not _has_exact_fields(payload.get("summary"), SUMMARY_FIELDS):
        return False
    if not _has_exact_fields(payload["summary"].get("state_counts"), STATE_COUNT_FIELDS):
        return False
    if not _is_nonnegative_int(payload["summary"].get("command_count")) or not all(
        _is_nonnegative_int(payload["summary"]["state_counts"][state]) for state in STATE_COUNT_FIELDS
    ):
        return False
    if not _artifact_schema_valid(payload.get("summary_artifact")):
        return False
    commands = payload.get("commands")
    dependencies = payload.get("dependencies")
    if not isinstance(commands, list) or not all(_has_exact_fields(command, COMMAND_FIELDS) for command in commands):
        return False
    if not all(
        isinstance(command["argv"], list)
        and all(isinstance(argument, str) for argument in command["argv"])
        and isinstance(command["started_at"], str)
        and (command["exit_code"] is None or (isinstance(command["exit_code"], int) and not isinstance(command["exit_code"], bool)))
        and command["state"] in {"PASS", "FAIL", "BLOCKED"}
        and _is_nonnegative_int(command["duration_ms"])
        and _is_nonnegative_int(command["redaction_count"])
        and (command["failure_code"] is None or isinstance(command["failure_code"], str))
        for command in commands
    ):
        return False
    if not all(
        _artifact_schema_valid(command.get(field))
        for command in commands
        for field in ("stdout_artifact", "stderr_artifact")
    ):
        return False
    if not isinstance(dependencies, list) or not all(_has_exact_fields(dependency, DEPENDENCY_FIELDS) for dependency in dependencies):
        return False
    if not all(
        isinstance(dependency["path"], str)
        and bool(dependency["path"])
        and _is_hash(dependency["sha256"])
        and _is_nonnegative_int(dependency["size"])
        for dependency in dependencies
    ):
        return False
    return (
        payload.get("schema_version") == SCHEMA_VERSION
        and payload.get("generator_version") == GENERATOR_VERSION
        and payload.get("result") in {"PASS", "FAIL", "BLOCKED"}
        and _is_nonnegative_int(payload.get("redaction_count"))
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True, encoding="utf-8", capture_output=True, check=True).stdout.strip()


def _tree_status(root: Path, excluded_prefix: str | None = None) -> list[str]:
    lines = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    if not excluded_prefix:
        return lines
    normalized = excluded_prefix.rstrip("/") + "/"
    return [
        line
        for line in lines
        if not (line.startswith("?? ") and line[3:].replace("\\", "/").startswith(normalized))
    ]


def _tree_snapshot(root: Path, excluded_prefix: str | None = None) -> tuple[list[str], str]:
    status = _tree_status(root, excluded_prefix)
    excluded = (excluded_prefix or "").rstrip("/")
    raw_untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    ).stdout
    untracked: list[dict[str, Any]] = []
    for raw_name in sorted(item for item in raw_untracked.split(b"\0") if item):
        relative = raw_name.decode("utf-8", errors="surrogateescape").replace("\\", "/")
        if excluded and (relative == excluded or relative.startswith(excluded + "/")):
            continue
        candidate = root / relative
        if candidate.is_symlink():
            target = os.readlink(candidate)
            payload = target.encode("utf-8", errors="surrogateescape")
            kind = "symlink"
        else:
            if not stat.S_ISREG(candidate.lstat().st_mode):
                raise ValueError("UNSUPPORTED_UNTRACKED_FILE")
            payload = candidate.read_bytes()
            kind = "file"
        untracked.append({"path": relative, "kind": kind, "size": len(payload), "sha256": _sha256_bytes(payload)})
    staged = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "--cached", "HEAD"], cwd=root, capture_output=True, check=True
    ).stdout
    unstaged = subprocess.run(
        ["git", "diff", "--binary", "--no-ext-diff", "HEAD"], cwd=root, capture_output=True, check=True
    ).stdout
    fingerprint_payload = {
        "status": status,
        "staged_diff_sha256": _sha256_bytes(staged),
        "unstaged_diff_sha256": _sha256_bytes(unstaged),
        "untracked": untracked,
    }
    return status, _sha256_bytes(_canonical_json(fingerprint_payload).encode("utf-8"))


def _relative_path(root: Path, candidate: Path) -> Path:
    resolved_root = root.resolve()
    resolved_candidate = candidate.resolve(strict=False)
    try:
        relative = resolved_candidate.relative_to(resolved_root)
    except ValueError as exc:
        raise ValueError("PATH_OUTSIDE_ROOT") from exc
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("INVALID_ARTIFACT_PATH")
    return relative


def _redact(text: str, root: Path) -> tuple[str, int]:
    result = text.replace(str(root.resolve()), "<REPO_ROOT>").replace(str(root), "<REPO_ROOT>")
    result, secret_count = SECRET_PATTERN.subn("<REDACTED_SECRET>", result)
    result, windows_path_count = WINDOWS_PATH_PATTERN.subn("<ABSOLUTE_PATH>", result)
    result, posix_path_count = POSIX_PATH_PATTERN.subn("<ABSOLUTE_PATH>", result)
    return result, secret_count + windows_path_count + posix_path_count


def _redact_argument(value: str, root: Path) -> tuple[str, int]:
    if value == sys.executable:
        return "<PYTHON_EXECUTABLE>", 0
    if value.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return "<ABSOLUTE_PATH>", 0
    return _redact(value, root)


def _redact_arguments(values: list[str], root: Path) -> tuple[list[str], int]:
    redacted: list[str] = []
    count = 0
    conceal_next = False
    for value in values:
        if conceal_next:
            redacted.append("<REDACTED_SECRET>")
            count += 1
            conceal_next = False
            continue
        rendered, rendered_count = _redact_argument(value, root)
        redacted.append(rendered)
        count += rendered_count
        option, separator, option_value = value.partition("=")
        if separator and SENSITIVE_OPTION.fullmatch(option):
            redacted[-1] = f"{option}=<REDACTED_SECRET>"
            count += 1
        conceal_next = bool(SENSITIVE_OPTION.fullmatch(value))
    return redacted, count


def _parse_command(value: str) -> list[str]:
    """Parse an explicit command without invoking a shell on either platform."""
    values = shlex.split(value, posix=False)
    return [item[1:-1] if len(item) >= 2 and item[0] == item[-1] and item[0] in {'"', "'"} else item for item in values]


def _write_artifact(root: Path, destination: Path, content: str) -> dict[str, Any]:
    relative = _relative_path(root, destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = content.encode("utf-8")
    destination.write_bytes(payload)
    return {"path": relative.as_posix(), "sha256": _sha256_bytes(payload), "size": len(payload), "media_type": "text/plain; charset=utf-8"}


def _dependencies(root: Path) -> list[dict[str, Any]]:
    dependency_files = [
        root / name
        for name in (
            "pyproject.toml",
            "uv.lock",
            "requirements.txt",
            "agent/requirements.txt",
            "package.json",
            "package-lock.json",
            "frontend/package.json",
            "frontend/package-lock.json",
        )
    ]
    return [
        {"path": file.relative_to(root).as_posix(), "sha256": _sha256_bytes(file.read_bytes()), "size": file.stat().st_size}
        for file in dependency_files
        if file.is_file()
    ]


def _tool_version(tool: str, *args: str) -> str | None:
    executable = shutil.which(tool)
    if executable is None:
        return None
    try:
        completed = subprocess.run([executable, *args], text=True, encoding="utf-8", capture_output=True, check=False, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    value = (completed.stdout or completed.stderr).strip()
    return value if completed.returncode == 0 and value else None


def _environment() -> dict[str, str | None]:
    return {
        "python": sys.version.split()[0],
        "node": _tool_version("node", "--version"),
        "npm": _tool_version("npm", "--version"),
        "platform": platform.platform(),
    }


def _render_summary(
    commit: str,
    tree_status: list[str],
    result: str,
    state_counts: dict[str, int],
    environment: dict[str, str | None],
) -> str:
    return "\n".join(
        (
            "# Agent baseline summary",
            "",
            f"- Commit: `{commit}`",
            f"- Result: `{result}`",
            f"- Working tree: `{'clean' if not tree_status else 'dirty'}`",
            f"- Commands: `{sum(state_counts.values())}` total; `{state_counts['PASS']}` PASS; "
            f"`{state_counts['FAIL']}` FAIL; `{state_counts['BLOCKED']}` BLOCKED",
            f"- Python: `{environment['python']}`",
            f"- Node: `{environment['node'] or 'unavailable'}`",
            f"- npm: `{environment['npm'] or 'unavailable'}`",
            "",
        )
    )


def _command_semantics_valid(command: Any) -> bool:
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
        return False
    state = command.get("state")
    exit_code = command.get("exit_code")
    failure_code = command.get("failure_code")
    if state == "PASS":
        return exit_code == 0 and failure_code is None
    if state == "FAIL":
        return (isinstance(exit_code, int) and exit_code != 0 and failure_code is None) or (
            exit_code is None and failure_code == "TIMEOUT"
        )
    if state == "BLOCKED":
        return exit_code is None and failure_code in {"TIMEOUT", "MISSING_TOOL"}
    return False


def _aggregate_result(states: list[str]) -> str:
    if "FAIL" in states:
        return "FAIL"
    if "BLOCKED" in states:
        return "BLOCKED"
    return "PASS" if states and all(state == "PASS" for state in states) else "FAIL"


def _artifact_matches(root: Path, evidence_directory: Path, artifact: Any) -> bool:
    if not isinstance(artifact, dict) or not isinstance(artifact.get("path"), str):
        return False
    try:
        path = (root / artifact["path"]).resolve()
        path.relative_to(evidence_directory.resolve())
    except (OSError, ValueError):
        return False
    return (
        path.is_file()
        and path.stat().st_size == artifact.get("size")
        and _sha256_bytes(path.read_bytes()) == artifact.get("sha256")
    )


def _existing_capture_matches(
    root: Path,
    output: Path,
    commands: list[str],
    evidence_root: str,
    timeout_seconds: float,
    timeout_state: str,
    blocker_reason: str | None,
    blocker_owner: str | None,
) -> dict[str, Any] | None:
    if not output.is_file():
        return None
    try:
        payload = _strict_json(output)
    except (json.JSONDecodeError, ValueError) as exc:
        raise ValueError("CONFLICTING_CAPTURE") from exc
    if not _closed_schema_valid(payload) or _redact(_canonical_json(payload), root)[0] != _canonical_json(payload):
        raise ValueError("CONFLICTING_CAPTURE")
    expected_argv = [_redact_arguments(_parse_command(command), root)[0] for command in commands]
    repository = payload.get("repository", {})
    status, diff_hash = _tree_snapshot(root, EVIDENCE_ROOT.as_posix())
    expected_environment = _environment()
    if (
        payload.get("schema_version") == SCHEMA_VERSION
        and repository.get("commit") == _run_git(root, "rev-parse", "HEAD")
        and repository.get("tree_status") == status
        and repository.get("tree_diff_hash") == diff_hash
        and repository.get("evidence_root") == evidence_root
        and payload.get("dependencies") == _dependencies(root)
        and payload.get("environment") == expected_environment
        and payload.get("capture_policy")
        == {
            "timeout_seconds": timeout_seconds,
            "timeout_state": timeout_state,
            "blocker_reason": blocker_reason,
            "blocker_owner": blocker_owner,
        }
        and [item.get("argv") for item in payload.get("commands", [])] == expected_argv
    ):
        captured_commands = payload.get("commands", [])
        states = [item.get("state") for item in captured_commands if isinstance(item, dict)]
        expected_result = _aggregate_result(states)
        expected_counts = {state: states.count(state) for state in ("PASS", "FAIL", "BLOCKED")}
        semantic_valid = (
            bool(captured_commands)
            and all(_command_semantics_valid(item) for item in captured_commands)
            and payload.get("result") == expected_result
            and payload.get("summary") == {"command_count": len(captured_commands), "state_counts": expected_counts}
            and repository.get("clean") is (not status)
            and payload.get("redaction_count") == sum(item.get("redaction_count", -1) for item in captured_commands)
            and payload.get("generator_version") == GENERATOR_VERSION
        )
        command_artifacts_valid = all(
            isinstance(item, dict)
            and _artifact_matches(root, output.parent, item.get("stdout_artifact"))
            and _artifact_matches(root, output.parent, item.get("stderr_artifact"))
            for item in payload.get("commands", [])
        )
        environment = payload.get("environment")
        summary_valid = _artifact_matches(root, output.parent, payload.get("summary_artifact"))
        if summary_valid and isinstance(environment, dict):
            summary_path = root / payload["summary_artifact"]["path"]
            summary_valid = summary_path.read_text(encoding="utf-8") == _render_summary(
                repository["commit"], status, expected_result, expected_counts, environment
            )
        else:
            summary_valid = False
        if not semantic_valid or not command_artifacts_valid or not summary_valid:
            raise ValueError("CONFLICTING_CAPTURE")
        return payload
    raise ValueError("CONFLICTING_CAPTURE")


def capture(
    root: Path,
    output: Path,
    commands: list[str],
    timeout_seconds: float = 300.0,
    timeout_state: str = "FAIL",
    blocker_reason: str | None = None,
    blocker_owner: str | None = None,
) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve(strict=False)
    _relative_path(root, output)
    controlled_root = (root / EVIDENCE_ROOT).resolve()
    try:
        controlled_relative = output.relative_to(controlled_root)
    except ValueError as exc:
        raise ValueError("UNCONTROLLED_EVIDENCE_ROOT") from exc
    if len(controlled_relative.parts) < 2:
        raise ValueError("UNCONTROLLED_EVIDENCE_ROOT")
    if not (root / ".git").exists():
        raise ValueError("NOT_A_GIT_REPOSITORY")
    if not commands:
        raise ValueError("EMPTY_COMMAND_SET")
    if timeout_seconds <= 0:
        raise ValueError("INVALID_TIMEOUT")
    if timeout_state not in {"FAIL", "BLOCKED"}:
        raise ValueError("INVALID_TIMEOUT_STATE")
    blocker_reason = blocker_reason.strip() if isinstance(blocker_reason, str) else blocker_reason
    blocker_owner = blocker_owner.strip() if isinstance(blocker_owner, str) else blocker_owner
    if timeout_state == "BLOCKED":
        if not blocker_reason or not blocker_owner:
            raise ValueError("MISSING_BLOCKER_METADATA")
        if _redact(blocker_reason, root)[0] != blocker_reason or _redact(blocker_owner, root)[0] != blocker_owner:
            raise ValueError("SENSITIVE_BLOCKER_METADATA")
    elif blocker_reason is not None or blocker_owner is not None:
        raise ValueError("UNUSED_BLOCKER_METADATA")
    commit = _run_git(root, "rev-parse", "HEAD")
    evidence_root = output.parent.relative_to(root).as_posix()
    existing = _existing_capture_matches(
        root,
        output,
        commands,
        evidence_root,
        timeout_seconds,
        timeout_state,
        blocker_reason,
        blocker_owner,
    )
    if existing is not None:
        return existing
    status, diff_hash = _tree_snapshot(root, EVIDENCE_ROOT.as_posix())
    logs_directory = output.parent / "command_logs"
    command_results: list[dict[str, Any]] = []
    for index, raw_command in enumerate(commands):
        argv = _parse_command(raw_command)
        if not argv:
            raise ValueError("EMPTY_COMMAND")
        started = time.monotonic()
        started_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            completed = subprocess.run(argv, cwd=root, text=True, encoding="utf-8", capture_output=True, check=False, timeout=timeout_seconds)
            exit_code: int | None = completed.returncode
            state = "PASS" if completed.returncode == 0 else "FAIL"
            stdout, stdout_redactions = _redact(completed.stdout, root)
            stderr, stderr_redactions = _redact(completed.stderr, root)
            failure_code: str | None = None
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            state = timeout_state
            raw_stdout = exc.stdout or ""
            raw_stderr = exc.stderr or ""
            stdout, stdout_redactions = _redact(raw_stdout.decode() if isinstance(raw_stdout, bytes) else raw_stdout, root)
            stderr, stderr_redactions = _redact(raw_stderr.decode() if isinstance(raw_stderr, bytes) else raw_stderr, root)
            failure_code = "TIMEOUT"
        except FileNotFoundError:
            exit_code = None
            state = "BLOCKED"
            stdout = ""
            stderr, stderr_redactions = _redact(f"MISSING_TOOL:{argv[0]}\n", root)
            stdout_redactions = 0
            failure_code = "MISSING_TOOL"
        elapsed_ms = round((time.monotonic() - started) * 1000)
        safe_argv, argv_redactions = _redact_arguments(argv, root)
        command_results.append(
            {
                "argv": safe_argv,
                "started_at": started_at,
                "exit_code": exit_code,
                "state": state,
                "duration_ms": elapsed_ms,
                "redaction_count": argv_redactions + stdout_redactions + stderr_redactions,
                "failure_code": failure_code,
                "stdout_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stdout.txt", stdout),
                "stderr_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stderr.txt", stderr),
            }
        )
    dependencies = _dependencies(root)
    result = _aggregate_result([item["state"] for item in command_results])
    state_counts = {state: sum(item["state"] == state for item in command_results) for state in ("PASS", "FAIL", "BLOCKED")}
    environment = _environment()
    summary_content = _render_summary(commit, status, result, state_counts, environment)
    summary_artifact = _write_artifact(root, output.parent / "baseline_summary.md", summary_content)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repository": {
            "commit": commit,
            "tree_status": status,
            "tree_diff_hash": diff_hash,
            "clean": not status,
            "evidence_root": evidence_root,
        },
        "environment": environment,
        "capture_policy": {
            "timeout_seconds": timeout_seconds,
            "timeout_state": timeout_state,
            "blocker_reason": blocker_reason,
            "blocker_owner": blocker_owner,
        },
        "generator_version": GENERATOR_VERSION,
        "dependencies": dependencies,
        "commands": command_results,
        "summary": {"command_count": len(command_results), "state_counts": state_counts},
        "summary_artifact": summary_artifact,
        "redaction_count": sum(item["redaction_count"] for item in command_results),
        "result": result,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text(_canonical_json(payload) + "\n", encoding="utf-8")
    os.replace(temporary, output)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--command", action="append", default=[])
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--timeout-state", choices=("FAIL", "BLOCKED"), default="FAIL")
    parser.add_argument("--blocker-reason")
    parser.add_argument("--blocker-owner")
    args = parser.parse_args()
    try:
        payload = capture(
            args.root,
            args.output,
            args.command,
            args.timeout_seconds,
            args.timeout_state,
            args.blocker_reason,
            args.blocker_owner,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CAPTURE_ERROR:{exc}", file=sys.stderr)
        return 2
    print(_canonical_json({"result": payload["result"], "schema_version": SCHEMA_VERSION}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
