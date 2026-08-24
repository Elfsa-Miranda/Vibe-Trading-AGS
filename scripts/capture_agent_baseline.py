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
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ags.agent-baseline.v1"
SECRET_PATTERN = re.compile(r"(?i)(?:api[_-]?key|token|password|secret)\s*(?:=|:|\s)\s*[^\s]+")


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


def _redact(text: str, root: Path) -> str:
    result = text.replace(str(root.resolve()), "<REPO_ROOT>").replace(str(root), "<REPO_ROOT>")
    return SECRET_PATTERN.sub("<REDACTED_SECRET>", result)


def _redact_argument(value: str, root: Path) -> str:
    if value == sys.executable:
        return "<PYTHON_EXECUTABLE>"
    if value.startswith(("/", "\\\\")) or re.match(r"^[A-Za-z]:[\\/]", value):
        return "<ABSOLUTE_PATH>"
    return _redact(value, root)


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


def capture(root: Path, output: Path, commands: list[str]) -> dict[str, Any]:
    root = root.resolve()
    output = output.resolve(strict=False)
    _relative_path(root, output)
    if not (root / ".git").exists():
        raise ValueError("NOT_A_GIT_REPOSITORY")
    commit = _run_git(root, "rev-parse", "HEAD")
    evidence_root = output.parent.relative_to(root).as_posix()
    status = _tree_status(root, evidence_root)
    logs_directory = output.parent / "command_logs"
    command_results: list[dict[str, Any]] = []
    for index, raw_command in enumerate(commands):
        argv = _parse_command(raw_command)
        if not argv:
            raise ValueError("EMPTY_COMMAND")
        started = time.monotonic()
        completed = subprocess.run(argv, cwd=root, text=True, encoding="utf-8", capture_output=True, check=False)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        command_results.append(
            {
                "argv": [_redact_argument(argument, root) for argument in argv],
                "exit_code": completed.returncode,
                "duration_ms": elapsed_ms,
                "stdout_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stdout.txt", _redact(completed.stdout, root)),
                "stderr_artifact": _write_artifact(root, logs_directory / f"{index:02d}.stderr.txt", _redact(completed.stderr, root)),
            }
        )
    dependency_files = [root / name for name in ("pyproject.toml", "package-lock.json", "frontend/package-lock.json")]
    dependencies = [
        {"path": file.relative_to(root).as_posix(), "sha256": _sha256_bytes(file.read_bytes()), "size": file.stat().st_size}
        for file in dependency_files
        if file.is_file()
    ]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "repository": {"commit": commit, "tree_status": status, "clean": not status, "evidence_root": evidence_root},
        "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
        "dependencies": dependencies,
        "commands": command_results,
        "result": "PASS" if all(item["exit_code"] == 0 for item in command_results) else "FAIL",
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
    args = parser.parse_args()
    try:
        payload = capture(args.root, args.output, args.command)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"CAPTURE_ERROR:{exc}", file=sys.stderr)
        return 2
    print(_canonical_json({"result": payload["result"], "schema_version": SCHEMA_VERSION}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
