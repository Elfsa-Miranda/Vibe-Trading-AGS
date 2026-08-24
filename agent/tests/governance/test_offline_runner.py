from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
RUNNER = REPOSITORY_ROOT / "scripts" / "run_governance_tests.py"


def test_offline_runner_propagates_test_failure(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_sample.py").write_text(
        "def test_pass():\n    assert True\n\ndef test_fail():\n    assert False\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "1 passed, 1 failed" in result.stdout


def test_offline_runner_rejects_unsupported_fixture_signature(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_sample.py").write_text("def test_bad(monkeypatch):\n    assert True\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "UNSUPPORTED_TEST_SIGNATURE" in result.stderr


def test_offline_runner_cannot_be_bypassed_by_system_exit_zero(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_sample.py").write_text("def test_exit():\n    raise SystemExit(0)\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "0 passed, 1 failed" in result.stdout
    assert "SystemExit: 0" in result.stderr


def test_offline_runner_cannot_be_bypassed_by_import_time_system_exit_zero(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_a_pass.py").write_text("def test_pass():\n    assert True\n", encoding="utf-8")
    (tests_root / "test_b_exit.py").write_text("raise SystemExit(0)\n", encoding="utf-8")

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "1 passed, 1 failed" in result.stdout
    assert "test_b_exit.py::<module import>" in result.stdout
    assert "SystemExit: 0" in result.stderr


def test_offline_runner_rejects_async_tests_without_marking_them_passed(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_async.py").write_text(
        "async def test_never_fake_pass():\n    raise AssertionError('must execute or reject')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "0 passed, 1 failed" in result.stdout
    assert "UNSUPPORTED_ASYNC_TEST:test_never_fake_pass" in result.stderr


def test_offline_runner_rejects_generator_tests_without_marking_them_passed(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    tests_root.mkdir()
    (tests_root / "test_generator.py").write_text(
        "def test_never_fake_pass():\n    yield None\n    raise AssertionError('must execute or reject')\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(RUNNER), "--root", str(REPOSITORY_ROOT), "--tests-root", str(tests_root)],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 1
    assert "0 passed, 1 failed" in result.stdout
    assert "UNSUPPORTED_GENERATOR_TEST:test_never_fake_pass" in result.stderr
