from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

import pandas as pd

from src.alpha_quality.model import FEATURE_FLAGS
from src.factors import bench_runner


class _IdentityRegistry:
    def list(self, zoo: str | None = None) -> list[str]:  # noqa: ARG002
        return ["identity_signal"]

    def get(self, alpha_id: str) -> Any:  # noqa: ARG002
        class _Alpha:
            meta = {"theme": ["identity"], "formula_latex": "identity"}

        return _Alpha()

    def compute(
        self, alpha_id: str, panel: dict[str, pd.DataFrame]  # noqa: ARG002
    ) -> pd.DataFrame:
        return panel["factor"]


def _panel() -> dict[str, pd.DataFrame]:
    dates = pd.date_range("2024-01-01", periods=8, freq="D")
    symbols = [f"S{i}" for i in range(6)]
    factor = pd.DataFrame(
        [[float(j) for j in range(len(symbols))] for _ in range(len(dates))],
        index=dates,
        columns=symbols,
    )
    return {"factor": factor, "close": factor + 100.0}


def _stable(result: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in result.items() if k != "wall_seconds"}


_PRE_V32_BENCH_GOLDEN = json.loads(
    """{
      "alive": 0,
      "by_theme": {"identity": {"alive": 0, "count": 1, "dead": 1, "reversed": 0}},
      "dead": 1,
      "dead_examples": [{"category": "dead", "formula_latex": "identity", "ic_mean": 1.0, "id": "identity_signal", "ir": 0.0, "theme": ["identity"]}],
      "meta": {},
      "n_alphas_tested": 1,
      "n_skipped": 0,
      "period": "2024-2024",
      "reversed": 0,
      "rows": [{"_category": "dead", "formula_latex": "identity", "ic_count": 8, "ic_mean": 1.0, "ic_positive_ratio": 1.0, "ic_std": 0.0, "id": "identity_signal", "ir": 0.0, "theme": ["identity"]}],
      "skipped": [],
      "status": "ok",
      "top5_by_ir": [{"category": "dead", "formula_latex": "identity", "ic_mean": 1.0, "id": "identity_signal", "ir": 0.0, "theme": ["identity"]}],
      "universe": "fixture",
      "zoo": "fixture"
    }"""
)


def test_ags_feature_flags_default_false() -> None:
    assert FEATURE_FLAGS == {
        "VIBE_TRADING_AGS_ENABLED": False,
        "VIBE_TRADING_ALPHA_SCORECARD": False,
        "VIBE_TRADING_TRIAL_LEDGER": False,
        "VIBE_TRADING_ALPHA_FOUNDRY": False,
        "VIBE_TRADING_ADMISSION_GATE": False,
        "VIBE_TRADING_FORWARD_TRACKING": False,
        "VIBE_TRADING_ALPHA_REPORT_API": False,
        "VIBE_TRADING_RESEARCH_EVENTS": False,
        "VIBE_TRADING_FACTOR_DAG": False,
        "VIBE_TRADING_PROCESS_MEMORY": False,
        "VIBE_TRADING_TOPOLOGY_RETRIEVER": False,
        "VIBE_TRADING_TOPOLOGY_RETRIEVER_ACTIVE": False,
        "VIBE_TRADING_FALSIFICATION_CONTRACT": False,
        "VIBE_TRADING_COMPLEMENT_V2": False,
        "VIBE_TRADING_DECISION_V2": False,
    }


def test_feature_flags_off_keep_existing_run_bench_identity(monkeypatch) -> None:
    panel = _panel()
    monkeypatch.setattr(
        bench_runner,
        "_load_universe_panel",
        lambda universe, period: panel,  # noqa: ARG005
    )
    monkeypatch.setattr(
        bench_runner,
        "_compute_forward_returns",
        lambda loaded_panel: loaded_panel["factor"] * 0.001,
    )
    for name in FEATURE_FLAGS:
        monkeypatch.delenv(name, raising=False)

    result = bench_runner.run_bench(
        "fixture",
        "fixture",
        "2024-2024",
        registry=_IdentityRegistry(),
    )

    assert _stable(result) == _PRE_V32_BENCH_GOLDEN


def test_feature_off_fresh_process_creates_no_ags_state_or_worker(tmp_path: Path) -> None:
    script = """
import json
from pathlib import Path
import sys
import threading

before_files = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*'))
before_threads = sorted(thread.name for thread in threading.enumerate())
import api_server
paths = sorted(api_server.app.openapi()['paths'])
after_files = sorted(str(path.relative_to(Path.cwd())) for path in Path.cwd().rglob('*'))
after_threads = sorted(thread.name for thread in threading.enumerate())
print(json.dumps({
    'files_equal': before_files == after_files,
    'threads_equal': before_threads == after_threads,
    'ags_routes': [path for path in paths if 'alpha-genesis' in path],
    'ags_route_module_loaded': 'src.api.alpha_genesis_routes' in sys.modules,
}, sort_keys=True))
"""
    env = dict(os.environ)
    for name in FEATURE_FLAGS:
        env.pop(name, None)
    agent_root = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(agent_root)

    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "ags_route_module_loaded": False,
        "ags_routes": [],
        "files_equal": True,
        "threads_equal": True,
    }
