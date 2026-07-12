from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.alpha_quality.baseline_v1 import (
    BASELINE_ADAPTER_ID,
    BundledHistoricalFixturePITAdapterV1,
    FirstRealBaselineResultV1,
    FirstRealBaselineRunnerV1,
    _dates,
)
from src.alpha_quality.pit_adapter_v1 import AsharePITSnapshotRequestV1
from src.research_ledger.hash_utils import canonical_json_hash


COMMITTED_MANIFEST = (
    Path(__file__).parents[2]
    / "research_evidence"
    / "baseline_v1"
    / "baseline_manifest.json"
)


@pytest.fixture(scope="module")
def baseline(tmp_path_factory: pytest.TempPathFactory) -> FirstRealBaselineResultV1:
    return FirstRealBaselineRunnerV1().run(tmp_path_factory.mktemp("baseline"))


def test_first_real_baseline_is_non_empty_replayable_and_fail_closed(
    baseline: FirstRealBaselineResultV1,
) -> None:
    manifest = baseline.manifest

    assert manifest["baseline_status"] == "COMPLETED_RESEARCH_ONLY"
    assert manifest["authority_grade"] == (
        "external_unverified_bundled_historical_fixture"
    )
    assert manifest["effective_sample"] >= 1
    assert manifest["event_count"] >= 1
    assert manifest["chain_verified"] is True
    assert manifest["serial_retry_equal"] is True
    assert manifest["decision"] == "research_only"
    assert manifest["test_access_count"] == 0
    assert manifest["forward_observation_count"] == 0
    assert manifest["candidate_dossier_hash"].startswith("sha256:")
    assert manifest["run_report_hash"].startswith("sha256:")
    assert manifest["release_manifest_hash"].startswith("sha256:")


def test_independent_replay_preserves_scientific_scope_and_outcome(
    baseline: FirstRealBaselineResultV1,
    tmp_path: Path,
) -> None:
    replay = FirstRealBaselineRunnerV1().run(tmp_path / "independent-replay")

    for key in (
        "authority_grade",
        "completion_status",
        "decision",
        "effective_sample",
        "factor_spec_id",
        "formula",
        "limitations",
    ):
        assert replay.manifest[key] == baseline.manifest[key]

    for key in (
        "capacity",
        "cost",
        "inference",
        "missing_outcome",
        "multiplicity",
        "portfolio",
        "resource_budget",
        "stopping_rule",
        "universe",
        "weighting",
    ):
        assert (
            replay.manifest["preregistered_policy"][key]
            == baseline.manifest["preregistered_policy"][key]
        )

    # Separate append-only chains have distinct authority/event references.  The
    # in-chain serial retry above is the hash-equality authority.
    assert replay.manifest["chain_verified"] is True
    assert replay.manifest["serial_retry_equal"] is True


def test_fixture_uses_dated_membership_and_discloses_fixture_authority() -> None:
    dates = _dates()
    request = AsharePITSnapshotRequestV1(
        adapter_id=BASELINE_ADAPTER_ID,
        calendar_dates=dates,
        required_fields=("amount", "close", "high", "low", "open", "volume"),
        valid_cutoff=dates[-1],
        evaluation_policy_event_hash="sha256:" + "0" * 64,
    )
    adapter = BundledHistoricalFixturePITAdapterV1()
    bundle = adapter.load(request)

    assert adapter.descriptor().provider == "bundled-historical-fixture"
    assert bundle.daily_membership.nunique(axis=0).max() == 2
    assert bundle.source_manifest.dataset_vintage == "bundled-fixture-20241210"


def test_baseline_manifest_is_strict_json_without_private_output_path(
    baseline: FirstRealBaselineResultV1,
) -> None:
    raw = baseline.manifest_path.read_text(encoding="utf-8")
    decoded = json.loads(raw, parse_constant=lambda value: pytest.fail(value))

    assert decoded == baseline.manifest
    assert str(baseline.manifest_path.parent) not in raw
    assert "Administrator" not in raw
    assert "FINAL_TEST_NOT_OPENED" in decoded["limitations"]
    assert decoded["preregistered_policy"]["resource_budget"] == {
        "calendar_dates": 72,
        "candidate_count": 1,
        "symbols": 8,
        "worker_count": 1,
    }


def test_committed_baseline_manifest_is_self_verifying_and_sanitized() -> None:
    raw = COMMITTED_MANIFEST.read_text(encoding="utf-8")
    manifest = json.loads(raw, parse_constant=lambda value: pytest.fail(value))
    claimed_hash = manifest.pop("baseline_manifest_hash")

    assert canonical_json_hash(manifest) == claimed_hash
    assert "Administrator" not in raw
    assert "D:\\" not in raw
