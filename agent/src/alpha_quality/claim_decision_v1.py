"""Scope-bound claim matrix and narrow producer-event-only decision authority."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping, Sequence

from src.research_ledger.events.model import (
    EventDraft,
    EventTransitionError,
    ResearchEventEnvelope,
)
from src.research_ledger.hash_utils import canonical_json_hash


CLAIM_MATRIX_EVENT_TYPE = "ClaimMatrixRecorded"
SELECTION_EVENT_TYPE = "SelectionAssessmentRecorded"
DECISION_EVENT_TYPE = "QualityDecisionV4Recorded"
PRODUCER_SCHEMA_VERSION = "claim_decision_service.v1"
TIER_INVARIANT_MANIFEST_HASH = canonical_json_hash(
    {
        "schema_version": "narrow_decision_tier_invariants.v1",
        "tier_0": [
            "invalid_or_ambiguous_formula",
            "physical_leakage",
            "non_control_duplicate",
            "cost_exceeds_execution_alpha",
        ],
        "tier_1": [
            "missing_pit_or_execution_authority",
            "non_reproducible_or_incomplete_selection",
            "decisive_mechanism_falsified_or_unavailable",
            "negative_portfolio_marginal_value",
        ],
        "tier_2": "terminal_train_valid_only",
        "final_forward_absent": "candidate_zoo_ceiling",
        "soft_scores_cross_tier": False,
    }
)
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "claim_decision_policy.v1",
        "claim_source": "protected_producer_events_only",
        "selection_population": "all_trials_in_research_run",
        "tier_invariant_manifest_hash": TIER_INVARIANT_MANIFEST_HASH,
        "legacy_evidence_ceiling": "research_only",
        "report_input": False,
        "llm_input": False,
    }
)


ClaimAvailability = Literal["available", "blocked", "unavailable", "not_applicable"]
ClaimVerdict = Literal[
    "supported", "rejected", "inconclusive", "blocked", "not_applicable"
]
EvidenceGrade = Literal["descriptive", "exploratory", "decision_grade", "none"]


@dataclass(frozen=True)
class ClaimAssessmentV1:
    claim_type: str
    factor_spec_id: str
    availability: ClaimAvailability
    verdict: ClaimVerdict
    evidence_grade: EvidenceGrade
    scope: str
    estimate: Mapping[str, Any]
    uncertainty: Mapping[str, Any]
    bias_codes: tuple[str, ...]
    selection_codes: tuple[str, ...]
    blocker_codes: tuple[str, ...]
    root_cause_event_hashes: tuple[str, ...]
    promotion_effect: Literal["none", "reject", "cap_research_only"]
    source_event_hashes: tuple[str, ...]
    claim_hash: str

    @classmethod
    def build(
        cls,
        *,
        claim_type: str,
        factor_spec_id: str,
        availability: ClaimAvailability,
        verdict: ClaimVerdict,
        evidence_grade: EvidenceGrade,
        scope: str,
        estimate: Mapping[str, Any] | None = None,
        uncertainty: Mapping[str, Any] | None = None,
        bias_codes: Sequence[str] = (),
        selection_codes: Sequence[str] = (),
        blocker_codes: Sequence[str] = (),
        root_cause_event_hashes: Sequence[str] = (),
        promotion_effect: Literal["none", "reject", "cap_research_only"] = "none",
        source_event_hashes: Sequence[str] = (),
    ) -> "ClaimAssessmentV1":
        canonical: dict[str, Any] = {
            "schema_version": "claim_assessment.v1",
            "claim_type": claim_type,
            "factor_spec_id": factor_spec_id,
            "availability": availability,
            "verdict": verdict,
            "evidence_grade": evidence_grade,
            "scope": scope,
            "estimate": dict(estimate or {}),
            "uncertainty": dict(uncertainty or {}),
            "bias_codes": sorted(set(bias_codes)),
            "selection_codes": sorted(set(selection_codes)),
            "blocker_codes": sorted(set(blocker_codes)),
            "root_cause_event_hashes": sorted(set(root_cause_event_hashes)),
            "promotion_effect": promotion_effect,
            "source_event_hashes": sorted(set(source_event_hashes)),
        }
        if availability == "blocked" and (
            not canonical["blocker_codes"] or not canonical["root_cause_event_hashes"]
        ):
            raise ValueError("blocked claim requires complete root-cause chain")
        if verdict == "supported" and availability != "available":
            raise ValueError("supported claim requires available evidence")
        return cls(
            claim_type=claim_type,
            factor_spec_id=factor_spec_id,
            availability=availability,
            verdict=verdict,
            evidence_grade=evidence_grade,
            scope=scope,
            estimate=canonical["estimate"],
            uncertainty=canonical["uncertainty"],
            bias_codes=tuple(canonical["bias_codes"]),
            selection_codes=tuple(canonical["selection_codes"]),
            blocker_codes=tuple(canonical["blocker_codes"]),
            root_cause_event_hashes=tuple(canonical["root_cause_event_hashes"]),
            promotion_effect=promotion_effect,
            source_event_hashes=tuple(canonical["source_event_hashes"]),
            claim_hash=canonical_json_hash(canonical),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "claim_assessment.v1",
            "claim_type": self.claim_type,
            "factor_spec_id": self.factor_spec_id,
            "availability": self.availability,
            "verdict": self.verdict,
            "evidence_grade": self.evidence_grade,
            "scope": self.scope,
            "estimate": dict(self.estimate),
            "uncertainty": dict(self.uncertainty),
            "bias_codes": list(self.bias_codes),
            "selection_codes": list(self.selection_codes),
            "blocker_codes": list(self.blocker_codes),
            "root_cause_event_hashes": list(self.root_cause_event_hashes),
            "promotion_effect": self.promotion_effect,
            "source_event_hashes": list(self.source_event_hashes),
            "claim_hash": self.claim_hash,
        }


@dataclass(frozen=True)
class SelectionAssessmentV1:
    research_family_id: str
    run_id: str
    trial_count: int
    candidate_count: int
    terminal_counts: Mapping[str, int]
    unpublished_or_open_trial_count: int
    selection_policy_hash: str
    multiplicity_policy_hash: str
    effective_independent_run_groups: int
    final_access_count: int
    confirmatory_grade_eligible: bool
    source_event_hashes: tuple[str, ...]
    assessment_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "selection_assessment.v1",
            "research_family_id": self.research_family_id,
            "run_id": self.run_id,
            "trial_count": self.trial_count,
            "candidate_count": self.candidate_count,
            "terminal_counts": dict(self.terminal_counts),
            "unpublished_or_open_trial_count": self.unpublished_or_open_trial_count,
            "selection_policy_hash": self.selection_policy_hash,
            "multiplicity_policy_hash": self.multiplicity_policy_hash,
            "effective_independent_run_groups": self.effective_independent_run_groups,
            "final_access_count": self.final_access_count,
            "confirmatory_grade_eligible": self.confirmatory_grade_eligible,
            "source_event_hashes": list(self.source_event_hashes),
            "assessment_hash": self.assessment_hash,
        }


@dataclass(frozen=True)
class NarrowQualityDecisionV4:
    factor_spec_id: str
    decision: Literal["reject", "research_only", "candidate_zoo"]
    tier: Literal[0, 1, 2]
    reasons: tuple[str, ...]
    warnings: tuple[str, ...]
    caps: tuple[str, ...]
    limitations: tuple[str, ...]
    claim_matrix_hash: str
    selection_assessment_hash: str
    evidence_event_hashes: tuple[str, ...]
    profile_template_hash: str
    profile_authority_class: str
    profile_maximum_promotion: str
    policy_hash: str
    tier_invariant_manifest_hash: str
    decision_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "narrow_quality_decision.v4",
            "factor_spec_id": self.factor_spec_id,
            "decision": self.decision,
            "tier": self.tier,
            "reasons": list(self.reasons),
            "warnings": list(self.warnings),
            "caps": list(self.caps),
            "limitations": list(self.limitations),
            "claim_matrix_hash": self.claim_matrix_hash,
            "selection_assessment_hash": self.selection_assessment_hash,
            "evidence_event_hashes": list(self.evidence_event_hashes),
            "profile_template_hash": self.profile_template_hash,
            "profile_authority_class": self.profile_authority_class,
            "profile_maximum_promotion": self.profile_maximum_promotion,
            "policy_hash": self.policy_hash,
            "tier_invariant_manifest_hash": self.tier_invariant_manifest_hash,
            "decision_hash": self.decision_hash,
        }


def _claim_from_status(
    *,
    claim_type: str,
    factor_spec_id: str,
    status: str,
    source: ResearchEventEnvelope,
    scope: str,
    supported: frozenset[str],
    rejected: frozenset[str] = frozenset(),
    blocked: frozenset[str] = frozenset(),
    promotion_effect: Literal["none", "reject", "cap_research_only"] = "none",
) -> ClaimAssessmentV1:
    if status in supported:
        availability: ClaimAvailability = "available"
        verdict: ClaimVerdict = "supported"
        blockers: tuple[str, ...] = ()
        roots: tuple[str, ...] = ()
    elif status in rejected:
        availability, verdict = "available", "rejected"
        blockers, roots = (), ()
    elif status == "not_applicable":
        availability, verdict = "not_applicable", "not_applicable"
        blockers, roots = (), ()
    elif status in blocked:
        availability, verdict = "blocked", "blocked"
        blockers = (f"{claim_type.upper()}_UPSTREAM_BLOCKED",)
        roots = (source.event_hash,)
    else:
        availability, verdict = "unavailable", "inconclusive"
        blockers, roots = (), ()
    return ClaimAssessmentV1.build(
        claim_type=claim_type,
        factor_spec_id=factor_spec_id,
        availability=availability,
        verdict=verdict,
        evidence_grade="decision_grade" if availability == "available" else "none",
        scope=scope,
        blocker_codes=blockers,
        root_cause_event_hashes=roots,
        promotion_effect=promotion_effect if verdict in {"rejected", "blocked"} else "none",
        source_event_hashes=(source.event_hash,),
    )


def build_claim_matrix(
    *,
    factor_spec_id: str,
    observed: ResearchEventEnvelope,
    pit: ResearchEventEnvelope,
    execution: ResearchEventEnvelope | None,
    secondary: ResearchEventEnvelope,
    selection: SelectionAssessmentV1,
) -> tuple[tuple[ClaimAssessmentV1, ...], str]:
    """Build orthogonal claims without allowing one adverse claim to rewrite another."""
    claims: list[ClaimAssessmentV1] = []
    observed_available = observed.payload["availability"] in {"available", "partial"}
    claims.append(
        ClaimAssessmentV1.build(
            claim_type="observed_panel_predictive_association",
            factor_spec_id=factor_spec_id,
            availability="available" if observed_available else "unavailable",
            verdict="supported" if observed_available else "inconclusive",
            evidence_grade="descriptive" if observed_available else "none",
            scope=str(observed.payload["claim_scope"]),
            estimate=dict(observed.payload.get("split_metrics", {})),
            bias_codes=tuple(observed.payload.get("warnings", ())),
            source_event_hashes=(observed.event_hash,),
        )
    )
    legacy_pit_authority = pit.payload.get("pit_authority", {})
    legacy_decision_grade = bool(
        isinstance(legacy_pit_authority, Mapping)
        and legacy_pit_authority.get("decision_grade") is True
    )
    pit_available = (
        pit.payload["availability"] == "available"
        and (
            pit.payload.get("evidence_grade") == "confirmatory"
            or legacy_decision_grade
        )
        and not pit.payload.get("caps")
    )
    pit_blockers = tuple(pit.payload.get("caps", ())) or ("PIT_AUTHORITY_UNAVAILABLE",)
    for claim_type in (
        "pit_historical_universe_generalization",
        "pit_scoped_predictive_signal",
    ):
        claims.append(
            ClaimAssessmentV1.build(
                claim_type=claim_type,
                factor_spec_id=factor_spec_id,
                availability="available" if pit_available else "blocked",
                verdict="supported" if pit_available else "blocked",
                evidence_grade=(
                    "decision_grade"
                    if pit_available and selection.confirmatory_grade_eligible
                    else "exploratory" if pit_available else "none"
                ),
                scope=str(pit.payload["claim_scope"]),
                estimate=dict(pit.payload.get("split_metrics", {})),
                bias_codes=tuple(pit.payload.get("warnings", ())),
                selection_codes=(
                    ()
                    if selection.confirmatory_grade_eligible
                    else ("SELECTIVE_NONPUBLICATION_OR_OPEN_TRIAL",)
                ),
                blocker_codes=() if pit_available else pit_blockers,
                root_cause_event_hashes=() if pit_available else (pit.event_hash,),
                promotion_effect="none" if pit_available else "cap_research_only",
                source_event_hashes=(pit.event_hash,),
            )
        )
    if execution is None:
        execution_claim = ClaimAssessmentV1.build(
            claim_type="ashare_implementability",
            factor_spec_id=factor_spec_id,
            availability="blocked",
            verdict="blocked",
            evidence_grade="none",
            scope="A-share train/valid execution",
            blocker_codes=("EXECUTION_EVIDENCE_UNAVAILABLE",),
            root_cause_event_hashes=(observed.event_hash,),
            promotion_effect="cap_research_only",
            source_event_hashes=(observed.event_hash,),
        )
    else:
        implementability = str(execution.payload["implementability_claim"])
        execution_claim = _claim_from_status(
            claim_type="ashare_implementability",
            factor_spec_id=factor_spec_id,
            status=implementability,
            source=execution,
            scope="A-share train/valid producer execution",
            supported=frozenset({"supported"}),
            blocked=frozenset({"unavailable", "inconclusive"}),
            promotion_effect="cap_research_only",
        )
    claims.append(execution_claim)
    duplicate = bool(secondary.payload["duplicate_detected"])
    claims.extend(
        [
            ClaimAssessmentV1.build(
                claim_type="duplicate_identity",
                factor_spec_id=factor_spec_id,
                availability="available",
                verdict="rejected" if duplicate else "supported",
                evidence_grade="decision_grade",
                scope="frozen comparison pool exact/sign identity",
                promotion_effect="reject" if duplicate else "none",
                source_event_hashes=(secondary.event_hash,),
            ),
            ClaimAssessmentV1.build(
                claim_type="novelty",
                factor_spec_id=factor_spec_id,
                availability="available",
                verdict="rejected" if duplicate else "supported",
                evidence_grade="decision_grade",
                scope="frozen comparison pool novelty",
                promotion_effect="reject" if duplicate else "none",
                source_event_hashes=(secondary.event_hash,),
            ),
            ClaimAssessmentV1.build(
                claim_type="replication",
                factor_spec_id=factor_spec_id,
                availability="available" if duplicate else "not_applicable",
                verdict="supported" if duplicate else "not_applicable",
                evidence_grade="decision_grade" if duplicate else "none",
                scope="identity replication only; not novelty",
                source_event_hashes=(secondary.event_hash,),
            ),
        ]
    )
    claims.append(
        _claim_from_status(
            claim_type="residual_prediction",
            factor_spec_id=factor_spec_id,
            status=str(secondary.payload["residual_status"]),
            source=secondary,
            scope="frozen-pool train/valid ridge residual",
            supported=frozenset({"available"}),
        )
    )
    claims.append(
        _claim_from_status(
            claim_type="portfolio_marginal_value",
            factor_spec_id=factor_spec_id,
            status=str(secondary.payload["portfolio_status"]),
            source=secondary,
            scope="frozen allocation/cost/capacity/exposure policy",
            supported=frozenset({"complementary"}),
            rejected=frozenset({"nonpositive"}),
            blocked=frozenset({"unavailable"}),
            promotion_effect="cap_research_only",
        )
    )
    claims.append(
        _claim_from_status(
            claim_type="mechanism",
            factor_spec_id=factor_spec_id,
            status=str(secondary.payload["mechanism_status"]),
            source=secondary,
            scope="registered applicability and producer mechanism evidence",
            supported=frozenset({"supported", "partial_support"}),
            rejected=frozenset({"falsified"}),
            blocked=frozenset({"inconclusive"}),
            promotion_effect="cap_research_only",
        )
    )
    prefinal_supported = pit_available and execution_claim.verdict == "supported" and not duplicate
    claims.append(
        ClaimAssessmentV1.build(
            claim_type="prefinal_eligibility",
            factor_spec_id=factor_spec_id,
            availability="available" if prefinal_supported else "blocked",
            verdict="supported" if prefinal_supported else "blocked",
            evidence_grade="decision_grade" if prefinal_supported else "none",
            scope="terminal train/valid evidence only",
            blocker_codes=() if prefinal_supported else ("PREFINAL_PREREQUISITES_UNMET",),
            root_cause_event_hashes=(
                () if prefinal_supported else tuple(sorted({pit.event_hash, secondary.event_hash}))
            ),
            promotion_effect="none" if prefinal_supported else "cap_research_only",
            source_event_hashes=tuple(
                sorted(
                    {
                        pit.event_hash,
                        secondary.event_hash,
                        *(() if execution is None else (execution.event_hash,)),
                    }
                )
            ),
        )
    )
    for claim_type, blocker in (
        ("final_test", "FINAL_TEST_NOT_OPENED"),
        ("forward_monitoring", "FORWARD_PLAN_NOT_FROZEN"),
    ):
        claims.append(
            ClaimAssessmentV1.build(
                claim_type=claim_type,
                factor_spec_id=factor_spec_id,
                availability="blocked",
                verdict="blocked",
                evidence_grade="none",
                scope="not available in discovery evaluation",
                blocker_codes=(blocker,),
                root_cause_event_hashes=(secondary.event_hash,),
                promotion_effect="none",
                source_event_hashes=(secondary.event_hash,),
            )
        )
    ordered = tuple(sorted(claims, key=lambda item: item.claim_type))
    if len({item.claim_type for item in ordered}) != len(ordered):
        raise ValueError("claim matrix contains duplicate claim types")
    content = {
        "schema_version": "claim_matrix.v1",
        "factor_spec_id": factor_spec_id,
        "claims": [item.to_dict() for item in ordered],
        "selection_assessment_hash": selection.assessment_hash,
    }
    return ordered, canonical_json_hash(content)


def decide_narrow(
    *,
    factor_spec_id: str,
    claims: Sequence[ClaimAssessmentV1],
    claim_matrix_hash: str,
    selection: SelectionAssessmentV1,
    claim_event_hash: str,
    selection_event_hash: str,
    contract_payload: Mapping[str, Any],
    effective_maximum_promotion: str | None = None,
) -> NarrowQualityDecisionV4:
    by_type = {claim.claim_type: claim for claim in claims}
    reasons: set[str] = set()
    caps: set[str] = set()
    warnings: set[str] = set()
    if by_type["duplicate_identity"].verdict == "rejected":
        decision, tier = "reject", 0
        reasons.add("NON_CONTROL_DUPLICATE_IDENTITY")
    elif by_type["ashare_implementability"].verdict == "rejected":
        decision, tier = "reject", 0
        reasons.add("EXECUTION_ECONOMICS_HARD_FAILURE")
    else:
        cap_claims = [
            by_type[name]
            for name in (
                "pit_historical_universe_generalization",
                "pit_scoped_predictive_signal",
                "ashare_implementability",
                "prefinal_eligibility",
            )
            if by_type[name].verdict != "supported"
        ]
        mechanism = by_type["mechanism"]
        portfolio = by_type["portfolio_marginal_value"]
        custom_profile = contract_payload["profile_authority_class"] != "build_time_allowlisted"
        mechanism_caps = mechanism.verdict in {"rejected", "blocked", "inconclusive"}
        if cap_claims or mechanism_caps or portfolio.verdict == "rejected" or custom_profile or not selection.confirmatory_grade_eligible:
            decision, tier = "research_only", 1
            if cap_claims:
                caps.add("TRAIN_VALID_AUTHORITY_INCOMPLETE")
            if mechanism.verdict == "rejected":
                caps.add("MECHANISM_FALSIFIED")
            elif mechanism_caps:
                caps.add("MECHANISM_DECISIVE_EVIDENCE_UNAVAILABLE")
            if portfolio.verdict == "rejected":
                caps.add("NEGATIVE_PORTFOLIO_MARGINAL_VALUE")
            if custom_profile:
                caps.add("CUSTOM_PROFILE_RESEARCH_ONLY")
            if not selection.confirmatory_grade_eligible:
                caps.add("SELECTION_ASSESSMENT_INCOMPLETE")
            reasons.add("RESEARCH_ONLY_NONCOMPENSATORY_CAP")
        else:
            decision, tier = "candidate_zoo", 2
            reasons.add("TERMINAL_TRAIN_VALID_EVIDENCE_QUALIFIED")
    warnings.update(
        code
        for claim in claims
        for code in (*claim.bias_codes, *claim.selection_codes)
    )
    limitations = {
        "TRAIN_VALID_ONLY",
        "FINAL_TEST_NOT_OPENED",
        "FORWARD_SUCCESS_NOT_ESTABLISHED",
        "NO_LIVE_TRADING_MEANING",
    }
    profile_maximum = str(contract_payload["maximum_promotion"])
    effective_maximum = (
        profile_maximum
        if effective_maximum_promotion is None
        else effective_maximum_promotion
    )
    if effective_maximum not in {
        "reject", "research_only", "candidate_zoo", "paper_candidate", "forward_track"
    }:
        raise ValueError("effective promotion ceiling is invalid")
    if effective_maximum == "research_only" and decision == "candidate_zoo":
        decision, tier = "research_only", 1
        if profile_maximum == "research_only":
            caps.add("PROFILE_PROMOTION_CEILING")
        else:
            caps.add("RESEARCH_ONLY_ACTIVATION_INPUT_CEILING")
            limitations.add("RESEARCH_ONLY_ACTIVATION_INPUT")
        reasons = {"RESEARCH_ONLY_NONCOMPENSATORY_CAP"}
    content = {
        "schema_version": "narrow_quality_decision.v4",
        "factor_spec_id": factor_spec_id,
        "decision": decision,
        "tier": tier,
        "reasons": sorted(reasons),
        "warnings": sorted(warnings),
        "caps": sorted(caps),
        "limitations": sorted(limitations),
        "claim_matrix_hash": claim_matrix_hash,
        "selection_assessment_hash": selection.assessment_hash,
        "evidence_event_hashes": sorted({claim_event_hash, selection_event_hash}),
        "profile_template_hash": contract_payload["profile_template_hash"],
        "profile_authority_class": contract_payload["profile_authority_class"],
        "profile_maximum_promotion": profile_maximum,
        "policy_hash": PRODUCER_POLICY_HASH,
        "tier_invariant_manifest_hash": TIER_INVARIANT_MANIFEST_HASH,
    }
    return NarrowQualityDecisionV4(
        factor_spec_id=factor_spec_id,
        decision=decision,  # type: ignore[arg-type]
        tier=tier,  # type: ignore[arg-type]
        reasons=tuple(content["reasons"]),
        warnings=tuple(content["warnings"]),
        caps=tuple(content["caps"]),
        limitations=tuple(content["limitations"]),
        claim_matrix_hash=claim_matrix_hash,
        selection_assessment_hash=selection.assessment_hash,
        evidence_event_hashes=tuple(content["evidence_event_hashes"]),
        profile_template_hash=str(content["profile_template_hash"]),
        profile_authority_class=str(content["profile_authority_class"]),
        profile_maximum_promotion=profile_maximum,
        policy_hash=PRODUCER_POLICY_HASH,
        tier_invariant_manifest_hash=TIER_INVARIANT_MANIFEST_HASH,
        decision_hash=canonical_json_hash(content),
    )


class ClaimDecisionServiceV1:
    def __init__(self, store: Any) -> None:
        from src.research_ledger.events.store import ResearchEventStore

        if not isinstance(store, ResearchEventStore):
            raise TypeError("claim decision requires ResearchEventStore")
        if not store.flags.enabled("VIBE_TRADING_DECISION_V2"):
            raise RuntimeError("narrow Decision capability is disabled")
        self.store = store

    def _selection(
        self,
        *,
        run_id: str,
        contract: ResearchEventEnvelope,
        current_trial_id: str,
    ) -> SelectionAssessmentV1:
        events = self.store.query_events()
        starts = [event for event in events if event.event_type == "TrialStarted" and event.run_id == run_id]
        terminals = [event for event in events if event.event_type == "TrialTerminated" and event.run_id == run_id]
        terminal_by_trial = {str(event.payload["trial_id"]): event for event in terminals}
        statuses = (
            "success", "reject", "skip", "invalid", "duplicate", "timeout",
            "error", "infrastructure_failure",
        )
        counts = {
            status: sum(event.payload["status"] == status for event in terminals)
            for status in statuses
        }
        open_trials = {
            str(event.payload["trial_id"])
            for event in starts
            if str(event.payload["trial_id"]) not in terminal_by_trial
            and str(event.payload["trial_id"]) != current_trial_id
        }
        final_access = [
            event for event in events
            if event.event_type in {"FinalTestAccessRecorded", "FinalTestArtifactRecorded"}
            and event.run_id == run_id
        ]
        source_hashes = tuple(
            sorted(
                {
                    contract.event_hash,
                    *(event.event_hash for event in starts),
                    *(event.event_hash for event in terminals),
                    *(event.event_hash for event in final_access),
                }
            )
        )
        content = {
            "schema_version": "selection_assessment.v1",
            "research_family_id": contract.payload["research_family_id"],
            "run_id": run_id,
            "trial_count": len(starts),
            "candidate_count": len(
                {
                    str(event.payload["candidate_id"])
                    for event in starts
                }
            ),
            "terminal_counts": counts,
            "unpublished_or_open_trial_count": len(open_trials),
            "selection_policy_hash": canonical_json_hash(
                {"policy": "all_attempts_in_run.v1", "run_id": run_id}
            ),
            "multiplicity_policy_hash": canonical_json_hash(
                {"policy": "contract_family_frozen.v1", "contract_hash": contract.payload["contract_hash"]}
            ),
            "effective_independent_run_groups": 1,
            "final_access_count": len(final_access),
            "confirmatory_grade_eligible": not open_trials and not final_access,
            "source_event_hashes": list(source_hashes),
        }
        return SelectionAssessmentV1(
            research_family_id=str(content["research_family_id"]),
            run_id=run_id,
            trial_count=int(content["trial_count"]),
            candidate_count=int(content["candidate_count"]),
            terminal_counts=counts,
            unpublished_or_open_trial_count=len(open_trials),
            selection_policy_hash=str(content["selection_policy_hash"]),
            multiplicity_policy_hash=str(content["multiplicity_policy_hash"]),
            effective_independent_run_groups=1,
            final_access_count=len(final_access),
            confirmatory_grade_eligible=not open_trials and not final_access,
            source_event_hashes=source_hashes,
            assessment_hash=canonical_json_hash(content),
        )

    def record(
        self,
        *,
        run_id: str,
        trial_id: str,
        factor_spec_id: str,
        contract_event_hash: str,
        observed_event_hash: str,
        pit_event_hash: str,
        execution_event_hash: str | None,
        secondary_event_hash: str,
    ) -> tuple[ResearchEventEnvelope, ResearchEventEnvelope, ResearchEventEnvelope, NarrowQualityDecisionV4]:
        by_hash = {event.event_hash: event for event in self.store.query_events()}
        contract = by_hash.get(contract_event_hash)
        observed = by_hash.get(observed_event_hash)
        pit = by_hash.get(pit_event_hash)
        execution = None if execution_event_hash is None else by_hash.get(execution_event_hash)
        secondary = by_hash.get(secondary_event_hash)
        if (
            contract is None or contract.event_type != "ResolvedEvaluationContractRegistered"
            or observed is None or observed.event_type != "ObservedPanelPredictiveEvidenceRecorded"
            or pit is None or pit.event_type != "PITPredictiveEvidenceRecorded"
            or secondary is None or secondary.event_type != "SecondaryEvidenceRecorded"
            or (execution_event_hash is not None and (execution is None or execution.event_type != "ExecutionEvidenceRecorded"))
        ):
            raise EventTransitionError("claim decision protected sources are incomplete")
        sources = [observed, pit, secondary, *(() if execution is None else (execution,))]
        if any(event.run_id != run_id or event.payload["factor_spec_id"] != factor_spec_id for event in sources):
            raise EventTransitionError("claim decision cannot mix run or factor sources")
        selection = self._selection(
            run_id=run_id, contract=contract, current_trial_id=trial_id
        )
        selection_id = "selection-" + selection.assessment_hash.removeprefix("sha256:")[:24]
        selection_event = self.store._append_producer_event(
            EventDraft(
                event_type=SELECTION_EVENT_TYPE,
                entity_id=selection_id,
                run_id=run_id,
                payload_schema_version="selection_assessment_recorded.v1",
                idempotency_key="selection-assessment:" + selection.assessment_hash,
                payload={
                    "assessment_id": selection_id,
                    **selection.to_dict(),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                },
            )
        )
        claims, matrix_hash = build_claim_matrix(
            factor_spec_id=factor_spec_id,
            observed=observed,
            pit=pit,
            execution=execution,
            secondary=secondary,
            selection=selection,
        )
        matrix_content = {
            "schema_version": "claim_matrix.v1",
            "factor_spec_id": factor_spec_id,
            "claims": [item.to_dict() for item in claims],
            "selection_assessment_hash": selection.assessment_hash,
        }
        matrix_id = "claim-matrix-" + matrix_hash.removeprefix("sha256:")[:24]
        claim_sources = tuple(
            sorted(
                {
                    selection_event.event_hash,
                    *(event.event_hash for event in sources),
                }
            )
        )
        claim_event = self.store._append_producer_event(
            EventDraft(
                event_type=CLAIM_MATRIX_EVENT_TYPE,
                entity_id=matrix_id,
                run_id=run_id,
                payload_schema_version="claim_matrix_recorded.v1",
                idempotency_key="claim-matrix:" + matrix_hash,
                payload={
                    "matrix_id": matrix_id,
                    "factor_spec_id": factor_spec_id,
                    "claim_matrix_hash": matrix_hash,
                    "selection_event_hash": selection_event.event_hash,
                    "selection_assessment_hash": selection.assessment_hash,
                    "claims": matrix_content["claims"],
                    "claim_hashes": sorted(item.claim_hash for item in claims),
                    "source_event_hashes": list(claim_sources),
                    "promotion_effect": "none",
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )
        decision = decide_narrow(
            factor_spec_id=factor_spec_id,
            claims=claims,
            claim_matrix_hash=matrix_hash,
            selection=selection,
            claim_event_hash=claim_event.event_hash,
            selection_event_hash=selection_event.event_hash,
            contract_payload=contract.payload,
            effective_maximum_promotion=(
                "research_only"
                if self.store._shared_activation_sources_in_events(
                    list(by_hash.values()),
                    run_id=run_id,
                    contract_event_hash=contract.event_hash,
                    snapshot_event_hash=None,
                    before_event_hash=secondary.event_hash,
                )
                else None
            ),
        )
        decision_id = "quality-decision-v4-" + decision.decision_hash.removeprefix("sha256:")[:24]
        decision_event = self.store._append_producer_event(
            EventDraft(
                event_type=DECISION_EVENT_TYPE,
                entity_id=decision_id,
                run_id=run_id,
                payload_schema_version="quality_decision_recorded.v4",
                idempotency_key="quality-decision-v4:" + decision.decision_hash,
                payload={
                    "decision_id": decision_id,
                    **decision.to_dict(),
                    "claim_matrix_event_hash": claim_event.event_hash,
                    "selection_event_hash": selection_event.event_hash,
                    "promotion_effect": "authoritative_train_valid_tier",
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )
        return selection_event, claim_event, decision_event, decision


__all__ = [
    "CLAIM_MATRIX_EVENT_TYPE",
    "DECISION_EVENT_TYPE",
    "SELECTION_EVENT_TYPE",
    "ClaimAssessmentV1",
    "ClaimDecisionServiceV1",
    "NarrowQualityDecisionV4",
    "SelectionAssessmentV1",
    "build_claim_matrix",
    "decide_narrow",
]
