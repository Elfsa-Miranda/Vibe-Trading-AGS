from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.alpha_foundry.activation.protocol_v2 import (
    ActivationApplicabilityMatrixV1,
    ActivationProtocolRegistryV2,
    CanonicalHashSpecV1,
    PreregisteredActivationStatisticalProtocolV2,
)
from src.alpha_quality.flags import AGS_FLAG_DEFAULTS, ResolvedAGSFlags
from src.research_ledger.events import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventStore,
)
from src.research_ledger.hash_utils import canonical_json_hash


ZERO_HASH = "sha256:" + "0" * 64


def _hash(name: str) -> str:
    return canonical_json_hash({"fixture": name})


def _flags() -> ResolvedAGSFlags:
    return ResolvedAGSFlags(
        {
            name: True if name != "VIBE_TRADING_ALPHA_REPORT_API" else False
            for name in AGS_FLAG_DEFAULTS
        }
    )


def _store(tmp_path: Path) -> ResearchEventStore:
    return ResearchEventStore(
        tmp_path / "events.sqlite",
        artifact_root=tmp_path / "artifacts",
        flags=_flags(),
        code_version="activation-protocol-v2-test",
    )


def _protocol(
    *,
    cycle: str = "activation-cycle-v2-fixture",
    registration_event_hash: str = ZERO_HASH,
) -> PreregisteredActivationStatisticalProtocolV2:
    return PreregisteredActivationStatisticalProtocolV2.create(
        research_cycle_id=cycle,
        normalization_policy_hash=_hash("normalization"),
        registration_event_hash=registration_event_hash,
        code_manifest_hash=_hash("code-manifest"),
        sesoi=0.10,
    )


def test_hash_spec_uses_domain_separation() -> None:
    spec = CanonicalHashSpecV1.create()
    payload = {"same": "payload"}

    assert spec.hash_payload("activation-protocol.v1", payload) != spec.hash_payload(
        "activation-analysis.v1", payload
    )
    assert spec.hash_payload("activation-protocol.v1", payload).startswith("sha256:")


def test_legacy_sha256_artifacts_replay_unchanged() -> None:
    payload = {"schema_version": "legacy_fixture.v1", "value": 7}
    before = canonical_json_hash(payload)

    CanonicalHashSpecV1.create().hash_payload("activation-new-object.v1", payload)

    assert canonical_json_hash(payload) == before
    assert before == "sha256:d562cfc6d21b46fabdb2f2d985b92179fc48c2a56912f5b56a88ae6ec73ea023"


def test_hash_is_not_used_as_authentication_claim() -> None:
    assert CanonicalHashSpecV1.create().authentication_claim is False


def test_primary_test_does_not_switch_after_normality_test() -> None:
    protocol = _protocol()

    assert protocol.primary_test == "paired_randomization_sign_flip.v1"
    assert "normal" not in protocol.primary_test
    assert not hasattr(protocol, "normality_test")


def test_pilot_observed_uplift_cannot_change_sesoi() -> None:
    protocol = _protocol()

    with pytest.raises(ValueError, match="protocol hash mismatch"):
        replace(protocol, sesoi=0.01)
    assert "observed uplift forbidden" in protocol.pilot_use_policy


def test_protocol_and_matrix_are_protected_and_frozen_before_candidate_execution(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    registry = ActivationProtocolRegistryV2(store)
    protocol = registry.register_protocol(run_id="cycle-run", protocol=_protocol())
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id=protocol.protocol.research_cycle_id,
        profile="candidate_zoo",
        source_hash=protocol.event.event_hash,
        frozen_before_event_hash=protocol.event.event_hash,
        code_manifest_hash=_hash("matrix-code"),
    )
    recorded = registry.register_applicability_matrix(run_id="cycle-run", matrix=matrix)

    assert protocol.event.event_type == "ActivationStatisticalProtocolV2Registered"
    assert recorded.event.event_type == "ActivationApplicabilityMatrixV1Registered"
    assert store.verify_chain()
    with pytest.raises(EventValidationError, match="deterministic producer"):
        store.append_event(
            EventDraft(
                event_type="ActivationStatisticalProtocolV2Registered",
                entity_id="caller-protocol",
                run_id="cycle-run",
                payload_schema_version="activation_statistical_protocol_registered.v2",
                payload=dict(protocol.event.payload),
            )
        )


def test_applicability_matrix_is_frozen_before_candidate_execution(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    registry = ActivationProtocolRegistryV2(store)
    recorded = registry.register_protocol(run_id="cycle-run", protocol=_protocol())
    store.append_event(
        EventDraft(
            event_type="TrialStarted",
            entity_id="trial-001",
            run_id="cycle-run",
            payload_schema_version="trial_started.v1",
            payload={
                "trial_id": "trial-001",
                "candidate_id": "candidate-001",
                "data_scope": "train_valid",
                "objective": "activation fixture",
                "started_at": "2026-07-13T00:00:00Z",
            },
        )
    )
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id=recorded.protocol.research_cycle_id,
        profile="candidate_zoo",
        source_hash=recorded.event.event_hash,
        frozen_before_event_hash=recorded.event.event_hash,
        code_manifest_hash=_hash("matrix-code"),
    )

    with pytest.raises(EventTransitionError, match="before outcome access"):
        registry.register_applicability_matrix(run_id="cycle-run", matrix=matrix)


def test_not_applicable_requires_producer_assessment() -> None:
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id="cycle",
        profile="candidate_zoo",
        source_hash=_hash("source"),
        frozen_before_event_hash=_hash("freeze"),
        code_manifest_hash=_hash("code"),
    )
    by_claim = {rule.claim_type: rule for rule in matrix.rules}

    assert by_claim["pit_snapshot_authority"].allowed_na_rule == "never"
    assert by_claim["duplicate_identity"].allowed_na_rule == "never"
    assert by_claim["mechanism"].allowed_na_rule == "producer_not_applicable_only"
    assert by_claim["complement"].allowed_na_rule == "producer_not_applicable_only"


def test_final_and_forward_are_not_applicable_to_discovery_activation_cycle() -> None:
    matrix = ActivationApplicabilityMatrixV1.create_default(
        research_cycle_id="cycle",
        profile="candidate_zoo",
        source_hash=_hash("source"),
        frozen_before_event_hash=_hash("freeze"),
        code_manifest_hash=_hash("code"),
    )
    rule = next(rule for rule in matrix.rules if rule.claim_type == "final_forward")

    assert rule.activation_effect == "not_applicable_no_feedback"
    assert rule.execution_stage == "forbidden"
