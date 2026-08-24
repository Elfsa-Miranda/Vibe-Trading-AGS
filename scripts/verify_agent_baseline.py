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
    payload: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("SCHEMA_VERSION_MISMATCH")
        return tuple(errors)
    try:
        if _git(root, "rev-parse", "HEAD") != payload.get("repository", {}).get("commit"):
            errors.append("BASE_SHA_MISMATCH")
        if _tree_status(root, payload.get("repository", {}).get("evidence_root")) != payload.get("repository", {}).get("tree_status"):
            errors.append("TREE_STATE_MISMATCH")
    except subprocess.SubprocessError:
        errors.append("GIT_STATE_UNAVAILABLE")
    for command in payload.get("commands", []):
        for field in ("stdout_artifact", "stderr_artifact"):
            artifact = command.get(field, {})
            try:
                path = _resolve_artifact(root, artifact["path"])
                if not path.is_file() or path.stat().st_size != artifact.get("size") or _sha256(path) != artifact.get("sha256"):
                    errors.append("EVIDENCE_HASH_MISMATCH")
            except (KeyError, OSError, ValueError):
                errors.append("EVIDENCE_HASH_MISMATCH")
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
