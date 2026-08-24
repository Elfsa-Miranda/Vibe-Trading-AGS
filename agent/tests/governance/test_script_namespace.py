from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def test_governance_helpers_preserve_the_existing_scripts_namespace() -> None:
    """Root helpers must not hide runtime scripts contributed by agent/scripts."""
    assert (REPOSITORY_ROOT / "scripts").is_dir()
    assert (REPOSITORY_ROOT / "agent" / "scripts").is_dir()
    assert not (REPOSITORY_ROOT / "scripts" / "__init__.py").exists()
