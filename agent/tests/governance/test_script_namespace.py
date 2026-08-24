from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_governance_helpers_preserve_the_existing_scripts_namespace() -> None:
    """Root helpers must not hide runtime scripts contributed by agent/scripts."""
    assert (REPOSITORY_ROOT / "scripts").is_dir()
    agent_root = REPOSITORY_ROOT / "agent"
    target = agent_root / "scripts" / "run_phase11_baostock_research_only_v1.py"
    assert target.is_file()
    assert not (REPOSITORY_ROOT / "scripts" / "__init__.py").exists()

    sys.path.insert(0, str(agent_root))
    try:
        importlib.invalidate_caches()
        spec = importlib.util.find_spec("scripts.run_phase11_baostock_research_only_v1")
    finally:
        sys.path.remove(str(agent_root))

    assert spec is not None
    assert spec.origin is not None
    assert Path(spec.origin).resolve() == target.resolve()
