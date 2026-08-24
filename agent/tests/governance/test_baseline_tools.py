from __future__ import annotations

import hashlib
import json
import shutil
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
    assert payload["repository"]["tree_diff_hash"]
    assert payload["commands"][0]["stdout_artifact"]["sha256"]
    assert payload["summary"]["command_count"] == 1
    assert (manifest.parent / "baseline_summary.md").is_file()
    assert set(payload["environment"]) == {
        "python",
        "python_implementation",
        "python_build",
        "python_cache_tag",
        "python_executable_sha256",
        "python_packages_sha256",
        "python_package_count",
        "node",
        "npm",
        "platform",
    }
    assert len(payload["environment"]["python_executable_sha256"]) == 64
    assert len(payload["environment"]["python_packages_sha256"]) == 64
    assert payload["environment"]["python_package_count"] >= 0


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


def test_dirty_tree_fingerprint_binds_tracked_and_untracked_bytes(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    (root / "README.md").write_text("dirty one\n", encoding="utf-8")
    scratch = root / "scratch.txt"
    scratch.write_text("untracked one\n", encoding="utf-8")
    manifest = _capture(root)

    (root / "README.md").write_text("dirty two\n", encoding="utf-8")
    assert "TREE_STATE_MISMATCH" in _verify(root, manifest).stdout
    (root / "README.md").write_text("dirty one\n", encoding="utf-8")
    scratch.write_text("untracked two\n", encoding="utf-8")
    assert "TREE_STATE_MISMATCH" in _verify(root, manifest).stdout


def test_sibling_evidence_capture_does_not_invalidate_existing_manifest(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    first = _capture(root)
    second = root / "agent" / "research_evidence" / "agent_baseline" / "second" / "baseline_manifest.json"
    command = f'{sys.executable} -c "print(\'second capture\')"'

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(second), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert _verify(root, first).returncode == 0
    assert _verify(root, second).returncode == 0


def test_tracked_source_inside_evidence_root_remains_tree_bound(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    tracked = root / "agent" / "research_evidence" / "agent_baseline" / ".gitkeep"
    tracked.write_text("changed tracked source\n", encoding="utf-8")

    result = _verify(root, manifest)

    assert result.returncode == 1
    assert "TREE_STATE_MISMATCH" in result.stdout


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


def test_capture_rejects_an_empty_command_set(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "agent" / "research_evidence" / "agent_baseline" / "empty" / "baseline_manifest.json"

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "EMPTY_COMMAND_SET" in result.stderr
    assert not output.exists()


def test_capture_rejects_a_different_python_environment(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    copied_root = tmp_path / "other-environment"
    copied_root.mkdir()
    names = (
        ("python.exe", "pythonw.exe", "py.exe")
        if sys.platform == "win32"
        else ("python", "pythonw", "py")
    )
    for index, name in enumerate(names):
        output = (
            root
            / "agent"
            / "research_evidence"
            / "agent_baseline"
            / f"wrong-python-{index}"
            / "baseline_manifest.json"
        )
        copied_python = copied_root / name
        shutil.copy2(sys.executable, copied_python)
        command = f'{copied_python} -c "print(\'wrong interpreter\')"'

        result = subprocess.run(
            [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output), "--command", command],
            text=True,
            capture_output=True,
            check=False,
        )

        assert result.returncode == 2
        assert "PYTHON_ENVIRONMENT_MISMATCH" in result.stderr
        assert not output.exists()


def test_missing_tool_and_timeout_are_typed_blocked_records(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    missing = root / "agent" / "research_evidence" / "agent_baseline" / "missing" / "baseline_manifest.json"
    missing_result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(missing), "--command", "definitely-missing-governance-tool"],
        text=True,
        capture_output=True,
        check=False,
    )
    missing_payload = json.loads(missing.read_text(encoding="utf-8"))

    assert missing_result.returncode == 0
    assert missing_payload["result"] == "BLOCKED"
    assert missing_payload["commands"][0]["failure_code"] == "MISSING_TOOL"
    assert missing_payload["commands"][0]["exit_code"] is None

    timeout = root / "agent" / "research_evidence" / "agent_baseline" / "timeout" / "baseline_manifest.json"
    timeout_command = f'{sys.executable} -c "import time; time.sleep(2)"'
    timeout_result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE),
            "--root",
            str(root),
            "--output",
            str(timeout),
            "--timeout-seconds",
            "0.05",
            "--timeout-state",
            "BLOCKED",
            "--blocker-reason",
            "CI runner unavailable",
            "--blocker-owner",
            "platform-team",
            "--command",
            timeout_command,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    timeout_payload = json.loads(timeout.read_text(encoding="utf-8"))

    assert timeout_result.returncode == 0
    assert timeout_payload["result"] == "BLOCKED"
    assert timeout_payload["commands"][0]["failure_code"] == "TIMEOUT"
    assert timeout_payload["capture_policy"]["blocker_owner"] == "platform-team"

    timeout_fail = root / "agent" / "research_evidence" / "agent_baseline" / "timeout-fail" / "baseline_manifest.json"
    default_result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE),
            "--root",
            str(root),
            "--output",
            str(timeout_fail),
            "--timeout-seconds",
            "0.05",
            "--command",
            timeout_command,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    default_payload = json.loads(timeout_fail.read_text(encoding="utf-8"))

    assert default_result.returncode == 0
    assert default_payload["result"] == "FAIL"
    assert default_payload["commands"][0]["state"] == "FAIL"
    assert default_payload["capture_policy"]["timeout_state"] == "FAIL"


def test_blocked_timeout_requires_named_external_condition_and_owner(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "agent" / "research_evidence" / "agent_baseline" / "unjustified-block" / "baseline_manifest.json"
    command = f'{sys.executable} -c "print(\'quick\')"'

    result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE),
            "--root",
            str(root),
            "--output",
            str(output),
            "--timeout-state",
            "BLOCKED",
            "--command",
            command,
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "MISSING_BLOCKER_METADATA" in result.stderr
    assert not output.exists()


def test_failure_takes_precedence_over_blocked_in_aggregate_result(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "agent" / "research_evidence" / "agent_baseline" / "mixed" / "baseline_manifest.json"
    failing = f'{sys.executable} -c "import sys; sys.exit(9)"'

    result = subprocess.run(
        [
            sys.executable,
            str(CAPTURE),
            "--root",
            str(root),
            "--output",
            str(output),
            "--command",
            "definitely-missing-governance-tool",
            "--command",
            failing,
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))

    assert result.returncode == 0
    assert payload["result"] == "FAIL"
    assert payload["summary"]["state_counts"] == {"PASS": 0, "FAIL": 1, "BLOCKED": 1}


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


def test_idempotent_capture_reopens_existing_artifacts(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    artifact = root / payload["commands"][0]["stdout_artifact"]["path"]
    artifact.write_text("tampered but same capture inputs\n", encoding="utf-8")

    command = f'{sys.executable} -c "print(\'baseline command\')"'
    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(manifest), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 2
    assert "CONFLICTING_CAPTURE" in result.stderr


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


def test_capture_redacts_quoted_secrets_and_unrelated_absolute_paths(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    output = root / "agent" / "research_evidence" / "agent_baseline" / "redacted-paths" / "baseline_manifest.json"
    emitter = root / "emit_sensitive.py"
    emitter.write_text(
        'print(\'password: "two word secret"\')\n'
        'print(r"D:\\Private Folder\\client export.csv")\n'
        'print("/home/reviewer/My Documents/client.csv")\n'
        'print("https://docs.pytest.org/en/stable/how-to/capture-warnings.html")\n',
        encoding="utf-8",
    )
    command = f'{sys.executable} emit_sensitive.py'

    result = subprocess.run(
        [sys.executable, str(CAPTURE), "--root", str(root), "--output", str(output), "--command", command],
        text=True,
        capture_output=True,
        check=False,
    )
    payload = json.loads(output.read_text(encoding="utf-8"))
    stdout = (root / payload["commands"][0]["stdout_artifact"]["path"]).read_text(encoding="utf-8")

    assert result.returncode == 0, result.stderr
    assert "two word secret" not in stdout
    assert "Private Folder" not in stdout
    assert "My Documents" not in stdout
    assert "<REDACTED_SECRET>" in stdout
    assert stdout.count("<ABSOLUTE_PATH>") == 2
    assert "https://docs.pytest.org/en/stable/how-to/capture-warnings.html" in stdout


def test_verifier_rejects_forged_command_and_aggregate_states(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["commands"][0]["state"] = "FAIL"
    payload["commands"][0]["exit_code"] = 0
    payload["result"] = "PASS"
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    result = _verify(root, manifest)

    assert result.returncode == 1
    assert "COMMAND_STATE_MISMATCH" in result.stdout


def test_verifier_rejects_forged_environment_and_unknown_sensitive_fields(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["environment"]["python_packages_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    environment_result = _verify(root, manifest)

    assert environment_result.returncode == 1
    assert "ENVIRONMENT_MISMATCH" in environment_result.stdout

    payload["password"] = "synthetic exposed value"
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    sensitive_result = _verify(root, manifest)

    assert sensitive_result.returncode == 1
    assert "SENSITIVE_MANIFEST" in sensitive_result.stdout
    assert "INVALID_MANIFEST_SHAPE" in sensitive_result.stdout


def test_verifier_rejects_invalid_nested_field_types_deterministically(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    payload["commands"][0]["stdout_artifact"]["path"] = None
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    result = _verify(root, manifest)

    assert result.returncode == 1
    assert result.stdout.strip() == "INVALID_MANIFEST_SHAPE"
    assert result.stderr == ""


def test_verifier_rejects_rehashed_sensitive_evidence(tmp_path: Path) -> None:
    root = _repository(tmp_path)
    manifest = _capture(root)
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    artifact_record = payload["commands"][0]["stdout_artifact"]
    artifact = root / artifact_record["path"]
    sensitive = b"authorization: Bearer exposed-value\nD:\\private\\export.csv\n"
    artifact.write_bytes(sensitive)
    artifact_record["size"] = len(sensitive)
    artifact_record["sha256"] = hashlib.sha256(sensitive).hexdigest()
    manifest.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")

    result = _verify(root, manifest)

    assert result.returncode == 1
    assert "SENSITIVE_EVIDENCE" in result.stdout


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
