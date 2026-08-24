from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
CAPTURE = REPOSITORY_ROOT / "scripts" / "capture_agent_baseline.py"
VERIFY = REPOSITORY_ROOT / "scripts" / "verify_agent_baseline.py"


def _run(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, cwd=cwd, text=True, capture_output=True, check=True)


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _run("git", "init", "-q", cwd=root)
    _run("git", "config", "user.email", "governance@example.test", cwd=root)
    _run("git", "config", "user.name", "Governance Test", cwd=root)
    (root / "README.md").write_text("baseline\n", encoding="utf-8")
    _run("git", "add", "--", "README.md", cwd=root)
    _run("git", "commit", "-qm", "baseline", cwd=root)
    return root


def _capture(root: Path) -> Path:
    output = root / "evidence" / "baseline_manifest.json"
    command = f'{sys.executable} -c "print(\'baseline command\')"'
    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return output


def _verify(root: Path, manifest: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(VERIFY), "--root", str(root), str(manifest)],
        text=True,
        capture_output=True,
        check=False,
    )


def test_capture_and_verify_bind_exact_commit_and_artifacts(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)

    result = _verify(root, manifest)
    payload = json.loads(manifest.read_text(encoding="utf-8"))

    assert result.returncode == 0, result.stderr
    assert payload["schema_version"] == "ags.agent-baseline.v1"
    assert payload["repository"]["commit"] == _run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    assert payload["commands"][0]["stdout_artifact"]["sha256"]


def test_verifier_rejects_tampered_command_artifact(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    artifact = root / payload["commands"][0]["stdout_artifact"]["path"]
    artifact.write_text("tampered\n", encoding="utf-8")

    result = _verify(root, manifest)

    assert result.returncode == 1
    assert "EVIDENCE_HASH_MISMATCH" in result.stdout


def test_verifier_rejects_wrong_commit_and_dirty_tree(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    (root / "README.md").write_text("changed\n", encoding="utf-8")

    dirty_result = _verify(root, manifest)
    assert dirty_result.returncode == 1
    assert "TREE_STATE_MISMATCH" in dirty_result.stdout

    _run("git", "add", "--", "README.md", cwd=root)
    _run("git", "commit", "-qm", "changed", cwd=root)
    commit_result = _verify(root, manifest)
    assert commit_result.returncode == 1
    assert "BASE_SHA_MISMATCH" in commit_result.stdout


def test_capture_preserves_a_failing_command_as_a_failure(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "evidence" / "failed_manifest.json"
    command = f'{sys.executable} -c "import sys; sys.exit(7)"'

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["result"] == "FAIL"


def test_capture_rejects_an_output_path_outside_the_repository(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    outside = tmp_path / "outside.json"

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(outside)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "PATH_OUTSIDE_ROOT" in result.stderr
