"""Capture reproducible, integrity-bound local baseline evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ags.agent-baseline.v1"
GENERATOR_VERSION = "capture_agent_baseline.v1"
EVIDENCE_ROOT = Path("agent/research_evidence/agent_baseline")
SECRET_PATTERN = re.compile(r"(?i)(?:(?:api[_-]?key|token|password|secret)\s*(?:=|:|\s)\s*[^\s,'\"}]+|authorization\s*:\s*(?:bearer\s+)?[^\s,'\"}]+|\"(?:api[_-]?key|token|password|secret)\"\s*:\s*\"[^\"]+\")")
SENSITIVE_OPTION = re.compile(r"(?i)^--?(?:api[_-]?key|token|password|secret|authorization)$")


def _canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _run_git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, text=True, encoding="utf-8", capture_output=True, check=True).stdout.strip()


def _tree_status(root: Path, excluded_prefix: str | None = None) -> list[str]:
    lines = _run_git(root, "status", "--porcelain=v1").splitlines()
    if not excluded_prefix:
        return lines
    normalized = excluded_prefix.rstrip("/") + "/"
    return [line for line in lines if not line[3:].replace("\\", "/").startswith(normalized)]


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
    result, count = SECRET_PATTERN.subn("<REDACTED_SECRET>", result)
    return result, count


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
    dependency_files = [root / name for name in ("pyproject.toml", "package-lock.json", "frontend/package-lock.json")]
    return [
        {"path": file.relative_to(root).as_posix(), "sha256": _sha256_bytes(file.read_bytes()), "size": file.stat().st_size}
        for file in dependency_files
        if file.is_file()
    ]


def _existing_capture_matches(root: Path, output: Path, commands: list[str], evidence_root: str) -> dict[str, Any] | None:
    if not output.is_file():
        return None
    try:
        payload = json.loads(output.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("CONFLICTING_CAPTURE") from exc
    expected_argv = [_redact_arguments(_parse_command(command), root)[0] for command in commands]
    repository = payload.get("repository", {})
    if (
        payload.get("schema_version") == SCHEMA_VERSION
        and repository.get("commit") == _run_git(root, "rev-parse", "HEAD")
        and repository.get("tree_status") == _tree_status(root, evidence_root)
        and repository.get("evidence_root") == evidence_root
        and payload.get("dependencies") == _dependencies(root)
        and [item.get("argv") for item in payload.get("commands", [])] == expected_argv
    ):
        return payload
    raise ValueError("CONFLICTING_CAPTURE")


def capture(root: Path, output: Path, commands: list[str], timeout_seconds: float = 300.0) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve(strict=False)
    _relative_path(root, output)
    controlled_root = (root / EVIDENCE_ROOT).resolve()
    try:
        output.relative_to(controlled_root)
    except ValueError as exc:
        raise ValueError("UNCONTROLLED_EVIDENCE_ROOT") from exc
    if not (root / ".git").exists():
        raise ValueError("NOT_A_GIT_REPOSITORY")
    commit = _run_git(root, "rev-parse", "HEAD")
    evidence_root = output.parent.relative_to(root).as_posix()
    existing = _existing_capture_matches(root, output, commands, evidence_root)
    if existing is not None:
        return existing
    status = _tree_status(root, evidence_root)
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
        except subprocess.TimeoutExpired as exc:
            exit_code = None
            state = "BLOCKED"
            raw_stdout = exc.stdout or ""
            raw_stderr = exc.stderr or ""
            stdout, stdout_redactions = _redact(raw_stdout.decode() if isinstance(raw_stdout, bytes) else raw_stdout, root)
            stderr, stderr_redactions = _redact(raw_stderr.decode() if isinstance(raw_stderr, bytes) else raw_stderr, root)
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
                "stdout_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stdout.txt", stdout),
                "stderr_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stderr.txt", stderr),
            }
        )
    dependencies = _dependencies(root)
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repository": {"commit": commit, "tree_status": status, "clean": not status, "evidence_root": evidence_root},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "generator_version": GENERATOR_VERSION,
        "dependencies": dependencies,
        "commands": command_results,
        "redaction_count": sum(item["redaction_count"] for item in command_results),
        "result": "PASS" if all(item["state"] == "PASS" for item in command_results) else ("BLOCKED" if any(item["state"] == "BLOCKED" for item in command_results) else "FAIL"),
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
    args = parser.parse_args()
    try:
        payload = capture(args.root, args.output, args.command, args.timeout_seconds)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CAPTURE_ERROR:{exc}", file=sys.stderr)
        return 2
    print(_canonical_json({"result": payload["result"], "schema_version": SCHEMA_VERSION}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
