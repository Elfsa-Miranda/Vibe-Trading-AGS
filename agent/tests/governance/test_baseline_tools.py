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
    (root / "agent").mkdir()
    (root / "agent" / ".gitkeep").write_text("\n", encoding="utf-8")
    (root / "agent" / "research_evidence" / "agent_baseline").mkdir(parents=True)
    (root / "agent" / "research_evidence" / "agent_baseline" / ".gitkeep").write_text("\n", encoding="utf-8")
    _run("git", "add", "--", "README.md", "agent/.gitkeep", "agent/research_evidence/agent_baseline/.gitkeep", cwd=root)
    _run("git", "commit", "-qm", "baseline", cwd=root)
    return root


def _capture(root: Path) -> Path:
    output = root / "agent" / "research_evidence" / "agent_baseline" / "attempt" / "baseline_manifest.json"
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
    output = root / "agent" / "research_evidence" / "agent_baseline" / "failed" / "baseline_manifest.json"
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


def test_capture_rejects_an_uncontrolled_evidence_directory(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "evidence" / "baseline_manifest.json"

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "UNCONTROLLED_EVIDENCE_ROOT" in result.stderr


def test_same_capture_is_idempotent_and_conflicting_capture_is_rejected(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    original = manifest.read_bytes()

    assert _capture(root).read_bytes() == original
    (root / "README.md").write_text("conflicting baseline\n", encoding="utf-8")
    command = f'{sys.executable} -c "print(\'baseline command\')"'
    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(manifest), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "CONFLICTING_CAPTURE" in result.stderr
    assert manifest.read_bytes() == original


def test_capture_redacts_secret_arguments_and_output(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "agent" / "research_evidence" / "agent_baseline" / "redacted" / "baseline_manifest.json"
    command = f'{sys.executable} -c "print(\'token=visible-secret\')" --token visible-argument --authorization=Bearer-secret'

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    stdout = (root / payload["commands"][0]["stdout_artifact"]["path"]).read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "visible-secret" not in output.read_text(encoding="utf-8")
    assert "visible-secret" not in stdout
    assert "Bearer-secret" not in output.read_text(encoding="utf-8")
    assert payload["redaction_count"] >= 3


def test_verifier_reopens_dependency_hashes_and_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "pyproject.toml").write_text("[project]\nname = 'fixture'\n", encoding="utf-8")
    _run("git", "add", "--", "pyproject.toml", cwd=root)
    _run("git", "commit", "-qm", "add dependency manifest", cwd=root)
    manifest = _capture(root)
    (root / "pyproject.toml").write_text("[project]\nname = 'tampered'\n", encoding="utf-8")

    dependency_result = _verify(root, manifest)
    assert dependency_result.returncode == 1
    assert "DEPENDENCY_HASH_MISMATCH" in dependency_result.stdout

    manifest.write_text('{"schema_version":"ags.agent-baseline.v1","schema_version":"duplicate"}', encoding="utf-8")
    malformed_result = _verify(root, manifest)
    assert malformed_result.returncode == 1
    assert "MANIFEST_UNREADABLE" in malformed_result.stdout
