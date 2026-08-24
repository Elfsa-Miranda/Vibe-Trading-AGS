"""Verify that captured baseline evidence still binds to this checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "ags.agent-baseline.v1"
EVIDENCE_ROOT = Path("agent/research_evidence/agent_baseline")


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


def _tree_status(root: Path, excluded_prefix: str | None) -> list[str]:
    lines = _git(root, "status", "--porcelain=v1").splitlines()
    if not excluded_prefix:
        return lines
    prefix = excluded_prefix.rstrip("/") + "/"
    return [line for line in lines if not line[3:].replace("\\", "/").startswith(prefix)]


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
    repository = payload.get("repository")
    if not isinstance(repository, dict) or not isinstance(payload.get("commands"), list) or not isinstance(payload.get("dependencies"), list):
        return ("INVALID_MANIFEST_SHAPE",)
    evidence_root = repository.get("evidence_root")
    expected_evidence_root = manifest_path.parent.relative_to(root).as_posix()
    if evidence_root != expected_evidence_root or not expected_evidence_root.startswith(EVIDENCE_ROOT.as_posix() + "/"):
        errors.append("UNCONTROLLED_EVIDENCE_ROOT")
    try:
        if _git(root, "rev-parse", "HEAD") != payload.get("repository", {}).get("commit"):
            errors.append("BASE_SHA_MISMATCH")
        if _tree_status(root, evidence_root) != payload.get("repository", {}).get("tree_status"):
            errors.append("TREE_STATE_MISMATCH")
    except subprocess.SubprocessError:
        errors.append("GIT_STATE_UNAVAILABLE")
    for command in payload.get("commands", []):
        if not isinstance(command, dict) or command.get("state") not in {"PASS", "FAIL", "BLOCKED"}:
            errors.append("INVALID_MANIFEST_SHAPE")
            continue
        for field in ("stdout_artifact", "stderr_artifact"):
            artifact = command.get(field, {})
            try:
                path = _resolve_artifact(root, artifact["path"])
                if not path.is_file() or path.stat().st_size != artifact.get("size") or _sha256(path) != artifact.get("sha256"):
                    errors.append("EVIDENCE_HASH_MISMATCH")
            except (KeyError, OSError, ValueError):
                errors.append("EVIDENCE_HASH_MISMATCH")
    for dependency in payload.get("dependencies", []):
        try:
            path = _resolve_artifact(root, dependency["path"])
            if not path.is_file() or path.stat().st_size != dependency.get("size") or _sha256(path) != dependency.get("sha256"):
                errors.append("DEPENDENCY_HASH_MISMATCH")
        except (KeyError, OSError, ValueError):
            errors.append("DEPENDENCY_HASH_MISMATCH")
    return tuple(dict.fromkeys(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        errors = verify(args.root, args.manifest)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors = ("MANIFEST_UNREADABLE",)
        print(f"verification detail: {exc}", file=sys.stderr)
    for error in errors:
        print(error)
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
