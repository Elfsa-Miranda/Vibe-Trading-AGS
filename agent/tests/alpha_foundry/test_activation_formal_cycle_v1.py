from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

import pytest

from src.alpha_foundry.activation.formal_cycle_v1 import (
    FormalActivationCycleBootstrapV1,
    FormalActivationCycleConfigV1,
)
from src.alpha_quality.flags import AGS_FLAG_DEFAULTS, ResolvedAGSFlags
from src.research_ledger.events import EventDraft
from src.research_ledger.hash_utils import canonical_json_hash


ACCEPTED_COMMIT = "508d482efbaa45276d004d15d3d387af7e26fe43"


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _flags(*, enabled: bool = True) -> ResolvedAGSFlags:
    return ResolvedAGSFlags(
        {name: enabled if name != "VIBE_TRADING_ALPHA_REPORT_API" else False for name in AGS_FLAG_DEFAULTS}
    )


def _config(cycle: str = "formal-cycle-readiness-20260714") -> FormalActivationCycleConfigV1:
    return FormalActivationCycleConfigV1(
        research_cycle_id=cycle,
        accepted_code_commit=ACCEPTED_COMMIT,
        normalization_policy_hash=_hash("normalization"),
        flat_policy_hash=_hash("flat"),
        topology_policy_hash=_hash("topology"),
        resource_policy_hash=_hash("resource"),
    )


def _bootstrap(repository_root: Path, *, enabled: bool = True) -> FormalActivationCycleBootstrapV1:
    return FormalActivationCycleBootstrapV1(
        repository_root,
        flags=_flags(enabled=enabled),
        code_version="formal-cycle-v1-test",
    )


def _fixture_repository(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[3]
    root = tmp_path / "repository"
    for relative in (
        Path("agent/research_evidence/activation"),
        Path("agent/research_evidence/release_manifest.json"),
        Path("docs/alpha-genesis-final-acceptance.md"),
        Path("docs/alpha-genesis-known-limitations.md"),
    ):
        origin = source / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)
    return root


def _legacy_hashes(repository_root: Path) -> dict[str, str]:
    root = repository_root / "agent/research_evidence/activation"
    return {
        path.relative_to(repository_root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_formal_cycle_store_survives_restart(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    cycle_id = "formal-cycle-restart-fixture"
    first = _bootstrap(root).bootstrap(_config(cycle_id))
    first_hashes = [event.event_hash for event in first.store.query_events()]

    reopened = _bootstrap(root).bootstrap(_config(cycle_id))
    assert [event.event_hash for event in reopened.store.query_events()] == first_hashes
    appended = reopened.store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="restart-trial",
            run_id=cycle_id,
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "restart-trial",
                "candidate_id": "restart-candidate",
                "data_scope": "train_valid",
                "objective": "restart chain validation",
                "started_at": "2026-07-14T00:00:00Z",
            },
        )
    )
    assert appended.previous_event_hash == first_hashes[-1]
    assert reopened.store.verify_chain()


def test_formal_cycle_bootstrap_is_idempotent(tmp_path: Path) -> None:
    repository = _fixture_repository(tmp_path)
    cycle_id = "formal-cycle-idempotent-fixture"
    first = _bootstrap(repository).bootstrap(_config(cycle_id))
    second = _bootstrap(repository).bootstrap(_config(cycle_id))

    assert first.bootstrap_event.event_hash == second.bootstrap_event.event_hash
    assert first.manifest_artifact.semantic_hash == second.manifest_artifact.semantic_hash
    assert len(second.store.query_events()) == 3


def test_formal_cycle_capability_off_writes_nothing(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="capability is disabled"):
        _bootstrap(tmp_path, enabled=False).bootstrap(_config("formal-cycle-off"))
    assert list(tmp_path.iterdir()) == []


def test_old_inconclusive_cycle_is_unchanged(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    before = _legacy_hashes(root)
    cycle_id = "formal-cycle-old-unchanged-fixture"
    _bootstrap(root).bootstrap(_config(cycle_id))
    assert _legacy_hashes(root) == before


def test_formal_cycle_manifest_has_no_private_path_or_outcome(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    cycle = _bootstrap(root).bootstrap(_config("formal-cycle-safe-manifest"))
    payload = (cycle.store.artifact_root / cycle.manifest_artifact.relative_path).read_text(encoding="utf-8")

    assert str(root) not in payload
    assert "problem.md" not in payload
    assert '"outcome_accessed":false' in payload
    assert cycle.store.durability_diagnostics()["synchronous"] == "FULL"
