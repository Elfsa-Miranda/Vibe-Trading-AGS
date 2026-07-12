"""Producer-bound identity, complement, and mechanism evidence.

This module deliberately keeps secondary evidence orthogonal to promotion.  It
accepts immutable event references, rebuilds every assessment from protected
artifacts, and records one non-promoting producer event.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from src.alpha_foundry.dsl.identity import (
    build_expression_identity,
    build_sign_normalized_identity,
)
from src.alpha_quality.evaluation_contract.applicability import (
    ApplicabilityAssessmentV1,
)
from src.alpha_quality.execution_evidence_v1 import (
    EXECUTION_ARTIFACT_MEDIA_TYPE,
    ExecutionEvidenceArtifactStoreV1,
)
from src.alpha_quality.predictive_evidence_v4 import (
    FACTOR_OUTPUT_MEDIA_TYPE,
    FactorOutputArtifactStoreV3,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    EventValidationError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json_hash


COMPARISON_POOL_EVENT_TYPE = "ComparisonPoolFrozen"
SECONDARY_EVIDENCE_EVENT_TYPE = "SecondaryEvidenceRecorded"
PRODUCER_SCHEMA_VERSION = "secondary_evidence_service.v1"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "secondary_evidence_producer_policy.v1",
        "inputs": "protected_event_references_only",
        "identity": "canonical_and_sign_normalized_before_predictive",
        "portfolio_uncertainty": "seeded_circular_block_bootstrap.v1",
        "mechanism": "applicability_only_until_falsification_v2",
        "promotion_effect": "none",
    }
)


@dataclass(frozen=True)
class SecondaryEvidencePolicyV1:
    schema_version: Literal["secondary_evidence_policy.v1"] = (
        "secondary_evidence_policy.v1"
    )
    candidate_allocation: float = 0.10
    minimum_effective_dates: int = 12
    minimum_coverage: float = 0.80
    marginal_value_sesoi: float = 0.0
    baseline_denominator_floor: float = 1e-8
    annualization_factor: int = 252
    bootstrap_samples: int = 400
    bootstrap_block_length: int = 5
    bootstrap_seed: int = 45432
    ridge_alpha: float = 1.0
    allocation_policy_hash: str = canonical_json_hash(
        {"policy": "fixed_candidate_sleeve.v1", "allocation": 0.10}
    )
    cost_policy_hash: str = canonical_json_hash({"policy": "producer_execution_cost.v1"})
    capacity_policy_hash: str = canonical_json_hash(
        {"policy": "producer_execution_capacity.v1"}
    )
    exposure_policy_hash: str = canonical_json_hash(
        {"policy": "producer_execution_exposure.v1"}
    )

    def __post_init__(self) -> None:
        if self.schema_version != "secondary_evidence_policy.v1":
            raise ValueError("unsupported secondary evidence policy")
        finite = (
            self.candidate_allocation,
            self.minimum_coverage,
            self.marginal_value_sesoi,
            self.baseline_denominator_floor,
            self.ridge_alpha,
        )
        if any(not math.isfinite(value) for value in finite):
            raise ValueError("secondary policy contains non-finite values")
        if not 0.0 < self.candidate_allocation <= 1.0:
            raise ValueError("candidate allocation must be in (0, 1]")
        if not 0.0 < self.minimum_coverage <= 1.0:
            raise ValueError("minimum coverage must be in (0, 1]")
        if self.baseline_denominator_floor <= 0.0 or self.ridge_alpha <= 0.0:
            raise ValueError("secondary denominator and ridge parameters must be positive")
        for name in (
            "minimum_effective_dates",
            "annualization_factor",
            "bootstrap_samples",
            "bootstrap_block_length",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.bootstrap_samples > 10_000:
            raise ValueError("secondary bootstrap resource limit exceeded")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "candidate_allocation": self.candidate_allocation,
            "minimum_effective_dates": self.minimum_effective_dates,
            "minimum_coverage": self.minimum_coverage,
            "marginal_value_sesoi": self.marginal_value_sesoi,
            "baseline_denominator_floor": self.baseline_denominator_floor,
            "annualization_factor": self.annualization_factor,
            "bootstrap_samples": self.bootstrap_samples,
            "bootstrap_block_length": self.bootstrap_block_length,
            "bootstrap_seed": self.bootstrap_seed,
            "ridge_alpha": self.ridge_alpha,
            "allocation_policy_hash": self.allocation_policy_hash,
            "cost_policy_hash": self.cost_policy_hash,
            "capacity_policy_hash": self.capacity_policy_hash,
            "exposure_policy_hash": self.exposure_policy_hash,
        }

    @property
    def policy_hash(self) -> str:
        return canonical_json_hash(self.to_dict())


@dataclass(frozen=True)
class ComparisonPoolMemberV1:
    factor_spec_id: str
    factor_definition_event_hash: str
    factor_output_event_hash: str | None = None
    execution_event_hash: str | None = None
    decision_event_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "factor_spec_id": self.factor_spec_id,
            "factor_definition_event_hash": self.factor_definition_event_hash,
            "factor_output_event_hash": self.factor_output_event_hash,
            "execution_event_hash": self.execution_event_hash,
            "decision_event_hash": self.decision_event_hash,
        }


@dataclass(frozen=True)
class FrozenComparisonPoolV1:
    source_watermark_event_hash: str
    members: tuple[ComparisonPoolMemberV1, ...]
    policy: SecondaryEvidencePolicyV1
    evidence_watermark: int
    comparison_pool_hash: str

    @classmethod
    def build(
        cls,
        *,
        source_watermark_event_hash: str,
        members: Sequence[ComparisonPoolMemberV1],
        policy: SecondaryEvidencePolicyV1,
        evidence_watermark: int,
    ) -> "FrozenComparisonPoolV1":
        ordered = tuple(sorted(members, key=lambda item: item.factor_spec_id))
        if len({item.factor_spec_id for item in ordered}) != len(ordered):
            raise ValueError("comparison pool factor identities must be unique")
        content = {
            "schema_version": "frozen_comparison_pool.v1",
            "source_watermark_event_hash": source_watermark_event_hash,
            "evidence_watermark": evidence_watermark,
            "members": [item.to_dict() for item in ordered],
            "policy": policy.to_dict(),
            "policy_hash": policy.policy_hash,
        }
        return cls(
            source_watermark_event_hash=source_watermark_event_hash,
            members=ordered,
            policy=policy,
            evidence_watermark=evidence_watermark,
            comparison_pool_hash=canonical_json_hash(content),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "frozen_comparison_pool.v1",
            "source_watermark_event_hash": self.source_watermark_event_hash,
            "evidence_watermark": self.evidence_watermark,
            "members": [item.to_dict() for item in self.members],
            "policy": self.policy.to_dict(),
            "policy_hash": self.policy.policy_hash,
            "comparison_pool_hash": self.comparison_pool_hash,
        }


def _information_ratio(series: pd.Series, annualization_factor: int) -> float | None:
    values = series.to_numpy(dtype=float)
    if len(values) < 2:
        return None
    standard_deviation = float(np.std(values, ddof=1))
    if not math.isfinite(standard_deviation) or standard_deviation <= 0.0:
        return None
    return float(np.mean(values) / standard_deviation * math.sqrt(annualization_factor))


def assess_portfolio_marginal_value(
    *,
    pool_net_returns: pd.Series | None,
    candidate_net_returns: pd.Series | None,
    policy: SecondaryEvidencePolicyV1,
) -> dict[str, Any]:
    """Compute a preregistered uncertainty-aware marginal-value assessment."""
    base: dict[str, Any] = {
        "schema_version": "portfolio_marginal_value_assessment.v1",
        "policy_hash": policy.policy_hash,
        "sesoi": policy.marginal_value_sesoi,
        "baseline_denominator_floor": policy.baseline_denominator_floor,
    }
    if pool_net_returns is None or candidate_net_returns is None:
        content = {
            **base,
            "status": "unavailable",
            "effective_dates": 0,
            "coverage": 0.0,
            "delta_information_ratio": None,
            "confidence_interval": None,
            "reason_code": "PRODUCER_EXECUTION_EVIDENCE_MISSING",
        }
        return {**content, "assessment_hash": canonical_json_hash(content)}
    union = pool_net_returns.index.union(candidate_net_returns.index)
    aligned = pd.concat(
        [pool_net_returns.rename("pool"), candidate_net_returns.rename("candidate")],
        axis=1,
        join="outer",
    ).dropna()
    coverage = 0.0 if len(union) == 0 else len(aligned) / len(union)
    if len(aligned) < policy.minimum_effective_dates or coverage < policy.minimum_coverage:
        content = {
            **base,
            "status": "inconclusive",
            "effective_dates": len(aligned),
            "coverage": float(coverage),
            "delta_information_ratio": None,
            "confidence_interval": None,
            "reason_code": "MARGINAL_VALUE_SAMPLE_OR_COVERAGE_INSUFFICIENT",
        }
        return {**content, "assessment_hash": canonical_json_hash(content)}
    if not np.isfinite(aligned.to_numpy(dtype=float)).all():
        raise ValueError("portfolio marginal evidence contains non-finite values")
    pool = aligned["pool"].astype(float)
    candidate = aligned["candidate"].astype(float)
    if float(pool.std(ddof=1)) < policy.baseline_denominator_floor:
        content = {
            **base,
            "status": "inconclusive",
            "effective_dates": len(aligned),
            "coverage": float(coverage),
            "delta_information_ratio": None,
            "confidence_interval": None,
            "reason_code": "BASELINE_DENOMINATOR_BELOW_FLOOR",
        }
        return {**content, "assessment_hash": canonical_json_hash(content)}
    after = (1.0 - policy.candidate_allocation) * pool + policy.candidate_allocation * candidate
    before_ir = _information_ratio(pool, policy.annualization_factor)
    after_ir = _information_ratio(after, policy.annualization_factor)
    if before_ir is None or after_ir is None:
        raise ValueError("portfolio information ratio is undefined")
    delta = after_ir - before_ir
    rng = np.random.default_rng(policy.bootstrap_seed)
    n = len(aligned)
    block = min(policy.bootstrap_block_length, n)
    blocks_needed = math.ceil(n / block)
    deltas: list[float] = []
    for _ in range(policy.bootstrap_samples):
        indices: list[int] = []
        for _ in range(blocks_needed):
            start = int(rng.integers(0, n))
            indices.extend((start + offset) % n for offset in range(block))
        selected = np.asarray(indices[:n], dtype=int)
        sample_pool = pd.Series(pool.to_numpy()[selected])
        sample_after = pd.Series(after.to_numpy()[selected])
        sample_before_ir = _information_ratio(sample_pool, policy.annualization_factor)
        sample_after_ir = _information_ratio(sample_after, policy.annualization_factor)
        if sample_before_ir is not None and sample_after_ir is not None:
            deltas.append(sample_after_ir - sample_before_ir)
    if len(deltas) < max(20, policy.bootstrap_samples // 2):
        status = "inconclusive"
        interval = None
        reason = "BOOTSTRAP_EFFECTIVE_SAMPLES_INSUFFICIENT"
    else:
        quantiles = np.asarray(
            np.quantile(np.asarray(deltas), [0.025, 0.975]), dtype=float
        )
        lower = float(quantiles[0])
        upper = float(quantiles[1])
        interval = [lower, upper]
        if lower > policy.marginal_value_sesoi:
            status, reason = "complementary", None
        elif upper < -policy.marginal_value_sesoi:
            status, reason = "nonpositive", "MARGINAL_VALUE_BELOW_NEGATIVE_SESOI"
        else:
            status, reason = "inconclusive", "MARGINAL_VALUE_INTERVAL_NOT_DECISIVE"
    content = {
        **base,
        "status": status,
        "effective_dates": len(aligned),
        "coverage": float(coverage),
        "delta_information_ratio": float(delta),
        "confidence_interval": interval,
        "reason_code": reason,
    }
    return {**content, "assessment_hash": canonical_json_hash(content)}


def build_mechanism_assessment(
    applicability: ApplicabilityAssessmentV1,
    *,
    formal_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Keep applicability and mechanism outcome separate from predictive truth."""
    if applicability.result == "not_applicable":
        status, reason = "not_applicable", applicability.reason_code
        result_hash = None
    elif formal_result is None:
        status, reason = "inconclusive", "FORMAL_FALSIFICATION_RESULT_UNAVAILABLE"
        result_hash = None
    else:
        allowed = {"falsified", "inconclusive", "partial_support", "supported"}
        status = str(formal_result.get("status"))
        if status not in allowed:
            raise ValueError("formal mechanism result status is not closed")
        result_hash = str(formal_result.get("result_hash"))
        reason = None
    content = {
        "schema_version": "mechanism_assessment.v1",
        "claim_type": applicability.claim_type,
        "applicability_assessment_hash": applicability.assessment_hash,
        "applicability": applicability.result,
        "status": status,
        "reason_code": reason,
        "formal_result_hash": result_hash,
        "predictive_claim_effect": "none",
    }
    return {**content, "assessment_hash": canonical_json_hash(content)}


def _identity_from_definition(event: ResearchEventEnvelope) -> dict[str, str]:
    formula = str(event.payload["metadata"]["canonical_formula"])
    expression = build_expression_identity(formula)
    sign = build_sign_normalized_identity(expression)
    if expression.expression_id != event.payload["expression_id"]:
        raise EventValidationError("factor definition identity does not replay")
    return {
        "factor_spec_id": str(event.payload["factor_spec_id"]),
        "expression_id": expression.expression_id,
        "formula_hash": canonical_json_hash({"canonical_formula": expression.canonical_formula}),
        "sign_normalized_id": sign.sign_normalized_id,
    }


def assess_duplicate_identity(
    candidate_definition: ResearchEventEnvelope,
    pool_definitions: Sequence[ResearchEventEnvelope],
) -> dict[str, Any]:
    candidate = _identity_from_definition(candidate_definition)
    members = [_identity_from_definition(event) for event in pool_definitions]
    comparisons = {
        "exact_expression_matches": sorted(
            item["factor_spec_id"]
            for item in members
            if item["expression_id"] == candidate["expression_id"]
        ),
        "exact_factor_spec_matches": sorted(
            item["factor_spec_id"]
            for item in members
            if item["factor_spec_id"] == candidate["factor_spec_id"]
        ),
        "exact_formula_matches": sorted(
            item["factor_spec_id"]
            for item in members
            if item["formula_hash"] == candidate["formula_hash"]
        ),
        "sign_normalized_matches": sorted(
            item["factor_spec_id"]
            for item in members
            if item["sign_normalized_id"] == candidate["sign_normalized_id"]
        ),
    }
    reasons = sorted(
        code
        for key, code in (
            ("exact_expression_matches", "EXACT_EXPRESSION_DUPLICATE"),
            ("exact_factor_spec_matches", "EXACT_FACTOR_SPEC_DUPLICATE"),
            ("exact_formula_matches", "EXACT_FORMULA_DUPLICATE"),
            ("sign_normalized_matches", "SIGN_NORMALIZED_DUPLICATE"),
        )
        if comparisons[key]
    )
    content = {
        "schema_version": "duplicate_identity_assessment.v1",
        **candidate,
        **comparisons,
        "duplicate_detected": bool(reasons),
        "duplicate_reasons": reasons,
        "novelty_claim": "rejected" if reasons else "not_rejected",
        "replication_claim": "preserved",
        "panel_correlation": {"status": "separate_not_computed"},
        "ic_correlation": {"status": "separate_not_computed"},
        "return_correlation": {"status": "separate_not_computed"},
    }
    return {**content, "assessment_hash": canonical_json_hash(content)}


def _event_ref(event: ResearchEventEnvelope, media_type: str) -> Mapping[str, Any]:
    refs = [item for item in event.payload["artifact_refs"] if item["media_type"] == media_type]
    if len(refs) != 1:
        raise EventValidationError("producer event lacks one exact artifact reference")
    return refs[0]


def _factor_panel(store: Any, event: ResearchEventEnvelope) -> pd.DataFrame:
    ref = _event_ref(event, FACTOR_OUTPUT_MEDIA_TYPE)
    artifacts = FactorOutputArtifactStoreV3(store.artifact_root)
    manifest = artifacts.read_manifest(
        str(ref["relative_path"]),
        expected_hash=str(event.payload["factor_output_hash"]),
        expected_blob_hash=str(ref["artifact_hash"]),
    )
    return artifacts.read_factor_frame(manifest)


def _net_returns(store: Any, event: ResearchEventEnvelope) -> pd.Series:
    ref = _event_ref(event, EXECUTION_ARTIFACT_MEDIA_TYPE)
    artifacts = ExecutionEvidenceArtifactStoreV1(store.artifact_root)
    artifact = artifacts.read_artifact(
        str(ref["relative_path"]),
        expected_hash=str(event.payload["execution_artifact_hash"]),
        expected_blob_hash=str(ref["artifact_hash"]),
    )
    _, aggregate = artifacts.read_tables(artifact)
    return aggregate["net_return"].astype(float)


def _residual_assessment(
    candidate: pd.DataFrame | None,
    references: Mapping[str, pd.DataFrame],
    policy: SecondaryEvidencePolicyV1,
) -> dict[str, Any]:
    base: dict[str, Any] = {
        "schema_version": "residual_prediction_assessment.v1",
        "policy_hash": policy.policy_hash,
    }
    if candidate is None or not references:
        content = {
            **base,
            "status": "unavailable",
            "effective_cells": 0,
            "residual_variance_ratio": None,
            "reason_code": "FACTOR_OUTPUT_POOL_INCOMPLETE",
        }
        return {**content, "assessment_hash": canonical_json_hash(content)}
    def stack(frame: pd.DataFrame, name: str) -> pd.Series:
        index = pd.MultiIndex.from_product([frame.index, frame.columns])
        return pd.Series(
            frame.to_numpy(dtype=float).reshape(-1), index=index, name=name
        ).dropna()

    stacked = stack(candidate, "candidate")
    columns = [stacked]
    for factor_id, panel in sorted(references.items()):
        columns.append(stack(panel, factor_id))
    aligned = pd.concat(columns, axis=1, join="inner").dropna()
    if len(aligned) < policy.minimum_effective_dates:
        content = {
            **base,
            "status": "inconclusive",
            "effective_cells": len(aligned),
            "residual_variance_ratio": None,
            "reason_code": "RESIDUAL_SAMPLE_INSUFFICIENT",
        }
        return {**content, "assessment_hash": canonical_json_hash(content)}
    y = aligned.pop("candidate").to_numpy(dtype=float)
    x = aligned.to_numpy(dtype=float)
    y = y - y.mean()
    x = x - x.mean(axis=0)
    coefficients = np.linalg.solve(
        x.T @ x + policy.ridge_alpha * np.eye(x.shape[1]), x.T @ y
    )
    residual = y - x @ coefficients
    denominator = float(np.dot(y, y))
    ratio = None if denominator <= 0.0 else float(np.dot(residual, residual) / denominator)
    content = {
        **base,
        "status": "available" if ratio is not None else "inconclusive",
        "effective_cells": len(aligned),
        "residual_variance_ratio": ratio,
        "reason_code": None if ratio is not None else "CANDIDATE_VARIANCE_ZERO",
    }
    return {**content, "assessment_hash": canonical_json_hash(content)}


class ComparisonPoolServiceV1:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("comparison pool requires ResearchEventStore")
        self.store = store

    def freeze(
        self,
        *,
        run_id: str,
        source_watermark_event_hash: str,
        members: Sequence[ComparisonPoolMemberV1],
        policy: SecondaryEvidencePolicyV1 | None = None,
    ) -> tuple[FrozenComparisonPoolV1, ResearchEventEnvelope]:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        positions = {event.event_hash: index for index, event in enumerate(events)}
        watermark = by_hash.get(source_watermark_event_hash)
        if watermark is None:
            raise EventTransitionError("comparison pool watermark is missing")
        for member in members:
            definition = by_hash.get(member.factor_definition_event_hash)
            if (
                definition is None
                or definition.event_type != "FactorDefinitionRecorded"
                or definition.payload["factor_spec_id"] != member.factor_spec_id
            ):
                raise EventTransitionError("comparison pool definition binding differs")
            expected = (
                (member.factor_output_event_hash, "FactorOutputRecordedV3"),
                (member.execution_event_hash, "ExecutionEvidenceRecorded"),
                (member.decision_event_hash, "QualityDecisionV4Recorded"),
            )
            for event_hash, event_type in expected:
                if event_hash is None:
                    continue
                source = by_hash.get(event_hash)
                if (
                    source is None
                    or source.event_type != event_type
                    or source.payload.get("factor_spec_id") != member.factor_spec_id
                ):
                    raise EventTransitionError("comparison pool member evidence differs")
            cited = [value for value in member.to_dict().values() if isinstance(value, str) and value.startswith("sha256:")]
            if any(positions[value] > positions[source_watermark_event_hash] for value in cited):
                raise EventTransitionError("comparison pool source lies after watermark")
        frozen = FrozenComparisonPoolV1.build(
            source_watermark_event_hash=source_watermark_event_hash,
            members=members,
            policy=policy or SecondaryEvidencePolicyV1(),
            evidence_watermark=positions[source_watermark_event_hash],
        )
        pool_id = "comparison-pool-" + frozen.comparison_pool_hash.removeprefix("sha256:")[:24]
        event = self.store._append_producer_event(
            EventDraft(
                event_type=COMPARISON_POOL_EVENT_TYPE,
                entity_id=pool_id,
                run_id=run_id,
                payload_schema_version="comparison_pool_frozen.v1",
                idempotency_key="comparison-pool:" + frozen.comparison_pool_hash,
                payload={
                    "pool_id": pool_id,
                    **frozen.to_dict(),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                },
            )
        )
        return frozen, event


class SecondaryEvidenceServiceV1:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("secondary evidence requires ResearchEventStore")
        self.store = store

    def record(
        self,
        *,
        run_id: str,
        factor_definition_event_hash: str,
        comparison_pool_hash: str,
        factor_output_event_hash: str | None,
        execution_event_hash: str | None,
        applicability_event_hash: str,
    ) -> ResearchEventEnvelope:
        events = self.store.query_events()
        by_hash = {event.event_hash: event for event in events}
        factor = by_hash.get(factor_definition_event_hash)
        pools = [
            event
            for event in events
            if event.event_type == COMPARISON_POOL_EVENT_TYPE
            and event.payload["comparison_pool_hash"] == comparison_pool_hash
        ]
        applicability_event = by_hash.get(applicability_event_hash)
        if factor is None or factor.event_type != "FactorDefinitionRecorded" or len(pools) != 1:
            raise EventTransitionError("secondary evidence sources are incomplete")
        if (
            applicability_event is None
            or applicability_event.event_type != "ApplicabilityAssessmentRecorded"
            or applicability_event.payload["factor_spec_id"] != factor.payload["factor_spec_id"]
        ):
            raise EventTransitionError("secondary applicability binding differs")
        pool_event = pools[0]
        policy = SecondaryEvidencePolicyV1(**dict(pool_event.payload["policy"]))
        members = tuple(
            ComparisonPoolMemberV1(**dict(item)) for item in pool_event.payload["members"]
        )
        definitions = [by_hash[item.factor_definition_event_hash] for item in members]
        identity = assess_duplicate_identity(factor, definitions)
        candidate_panel = None
        output_event = None if factor_output_event_hash is None else by_hash.get(factor_output_event_hash)
        if output_event is not None:
            if output_event.event_type != "FactorOutputRecordedV3" or output_event.payload["factor_spec_id"] != factor.payload["factor_spec_id"]:
                raise EventTransitionError("secondary factor output binding differs")
            candidate_panel = _factor_panel(self.store, output_event)
        reference_panels = {
            member.factor_spec_id: _factor_panel(self.store, by_hash[member.factor_output_event_hash])
            for member in members
            if member.factor_output_event_hash is not None
        }
        residual = _residual_assessment(candidate_panel, reference_panels, policy)
        execution_event = None if execution_event_hash is None else by_hash.get(execution_event_hash)
        candidate_returns = None
        if execution_event is not None:
            if execution_event.event_type != "ExecutionEvidenceRecorded" or execution_event.payload["factor_spec_id"] != factor.payload["factor_spec_id"]:
                raise EventTransitionError("secondary execution binding differs")
            candidate_returns = _net_returns(self.store, execution_event)
        member_returns = [
            _net_returns(self.store, by_hash[item.execution_event_hash])
            for item in members
            if item.execution_event_hash is not None
        ]
        pool_returns = None
        if member_returns:
            pool_returns = pd.concat(member_returns, axis=1, join="inner").mean(axis=1)
        portfolio = assess_portfolio_marginal_value(
            pool_net_returns=pool_returns,
            candidate_net_returns=candidate_returns,
            policy=policy,
        )
        applicability = ApplicabilityAssessmentV1(
            **{
                key: tuple(value) if key in {"evaluated_input_hashes", "source_event_hashes"} else value
                for key, value in applicability_event.payload.items()
                if key != "assessment_id"
            }
        )
        mechanism = build_mechanism_assessment(applicability)
        bundle = {
            "schema_version": "secondary_evidence_bundle.v1",
            "identity": identity,
            "residual_prediction": residual,
            "portfolio_marginal_value": portfolio,
            "mechanism": mechanism,
        }
        bundle_hash = canonical_json_hash(bundle)
        source_hashes = tuple(
            sorted(
                {
                    factor.event_hash,
                    pool_event.event_hash,
                    applicability_event.event_hash,
                    *(() if output_event is None else (output_event.event_hash,)),
                    *(() if execution_event is None else (execution_event.event_hash,)),
                    *(item.factor_definition_event_hash for item in members),
                    *(item.factor_output_event_hash for item in members if item.factor_output_event_hash),
                    *(item.execution_event_hash for item in members if item.execution_event_hash),
                }
            )
        )
        evidence_id = "secondary-evidence-" + bundle_hash.removeprefix("sha256:")[:24]
        return self.store._append_producer_event(
            EventDraft(
                event_type=SECONDARY_EVIDENCE_EVENT_TYPE,
                entity_id=evidence_id,
                run_id=run_id,
                payload_schema_version="secondary_evidence_recorded.v1",
                idempotency_key="secondary-evidence:" + bundle_hash,
                payload={
                    "evidence_id": evidence_id,
                    "factor_spec_id": str(factor.payload["factor_spec_id"]),
                    "comparison_pool_hash": comparison_pool_hash,
                    "identity_assessment_hash": identity["assessment_hash"],
                    "residual_assessment_hash": residual["assessment_hash"],
                    "portfolio_assessment_hash": portfolio["assessment_hash"],
                    "mechanism_assessment_hash": mechanism["assessment_hash"],
                    "duplicate_detected": identity["duplicate_detected"],
                    "novelty_claim": identity["novelty_claim"],
                    "replication_claim": identity["replication_claim"],
                    "residual_status": residual["status"],
                    "portfolio_status": portfolio["status"],
                    "mechanism_status": mechanism["status"],
                    "applicability_event_hash": applicability_event.event_hash,
                    "factor_output_event_hash": None if output_event is None else output_event.event_hash,
                    "execution_event_hash": None if execution_event is None else execution_event.event_hash,
                    "secondary_evidence_bundle": bundle,
                    "secondary_evidence_bundle_hash": bundle_hash,
                    "promotion_effect": "none",
                    "source_event_hashes": list(source_hashes),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )


__all__ = [
    "COMPARISON_POOL_EVENT_TYPE",
    "SECONDARY_EVIDENCE_EVENT_TYPE",
    "ComparisonPoolMemberV1",
    "ComparisonPoolServiceV1",
    "FrozenComparisonPoolV1",
    "SecondaryEvidencePolicyV1",
    "SecondaryEvidenceServiceV1",
    "assess_duplicate_identity",
    "assess_portfolio_marginal_value",
    "build_mechanism_assessment",
]
