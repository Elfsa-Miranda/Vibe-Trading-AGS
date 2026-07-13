# AGS v3.2 Activation Enablement V2 — PRE-FLIGHT Reuse Matrix

## Local baseline audit

- Accepted integration worktree: `D:/Vibe-Trading-v32-main`
- Accepted integration branch/commit: `codex/ags-v32-main` / `177bd64d6b9879606b3711223d48a255b1165f4b`
- Implementation worktree/branch: `D:/Vibe-Trading/.worktrees/activation-contract-v2` / `codex/ags-v32-activation-contract-v2`
- Historical Phase 11 result remains `inconclusive`, `flat_with_topology_shadow`, and `active_research_only=false`.
- The accepted worktree reported `agent/research_evidence/production_evaluation_v32/final_execution_audit.md` modified because of a local LF/CRLF worktree conversion; no textual diff was present. It is user state and is not touched here.
- Startup worktree user files (`.pytest-tmp/`, `.tmp/`, `.worktrees/`, `AGS_v32_PRODUCTION_EVALUATION_EXECPLAN.md`, and `problem.md`) are unrelated and preserved.
- No fetch, pull, push, remote query, PR operation, reset, clean, or checkout-over-user-work was performed.

## Reuse Matrix

| Required capability | Actual repository module | Actual class/service/factory | Existing schema/event version | Activation call boundary | Adapter required | Tests proving production boundary |
|---|---|---|---|---|---|---|
| EvaluationProfile / ResolvedEvaluationContract | `agent/src/alpha_quality/evaluation_contract/model.py`; `registry.py`; `contract.py` | `EvaluationProfileTemplateV1`; `ResolvedEvaluationContractV1`; `EvaluationProfileRegistryV1`; `ResolvedEvaluationContractServiceV1` | `evaluation_profile_template.v1`; `resolved_evaluation_contract.v1`; `ResolvedEvaluationContractRegistered` / `resolved_evaluation_contract_registered.v1` | Input bundle resolves the exact registered contract event and immutable contract hash; no profile defaults are accepted at arm runtime. | No algorithm adapter; refs-only bundle binding only. | `agent/tests/alpha_quality/test_evaluation_contract_v1.py`; `test_evaluation_policy_registry_v1.py` |
| Registered PIT adapter / snapshot producer / protected snapshot event | `agent/src/alpha_quality/adapters/pit_adapter_v1.py`; `pit_service_v2.py`; `pit_artifact_v2.py`; `tushare_csi300_pit_v1.py` | `AsharePITAdapterRegistryV1`; `AsharePITAdapterRegistrationServiceV1`; `AsharePITSnapshotServiceV2`; `TushareCSI300PITAdapterV1` | `AsharePITAdapterRegistered` / `ashare_pit_adapter_registered.v1`; `AsharePITSnapshotRecorded` / `ashare_pit_snapshot_recorded.v2`; `frozen_ashare_pit_snapshot.v2` | Provider audit gates authority; input bundle then carries only the protected snapshot event hash. | Field-level PIT audit is required; snapshot producer is reused unchanged. | `agent/tests/alpha_quality/test_pit_adapter_artifact_v2.py`; `test_pit_service_v2.py` |
| Flat pre-arm schedule | `agent/src/alpha_foundry/flat_schedule_v1.py` | `PreArmFlatScheduleV1`; `PreArmFlatScheduleServiceV1`; `PreArmFlatScheduleArtifactStoreV1` | `PreArmFlatScheduleFrozen` / `prearm_flat_schedule_frozen.v1` | Flat arm passes the exact schedule event ref as retrieval authority. | Narrow arm-policy selector only. | `agent/tests/alpha_foundry/test_prearm_flat_schedule_v1.py` |
| Retriever v7 | `agent/src/alpha_foundry/retrieval/service_v7.py` | `RetrieverDecisionV7Service`; `RecordedRetrieverDecisionV7` | `RetrieverDecisionV7Recorded` / `retriever_decision_recorded.v7` | Topology arm passes the exact v7 decision refs; plan/schedule own seeds and budget. | Narrow arm-policy selector only. | `agent/tests/alpha_foundry/test_retriever_decision_v7.py` |
| Frozen generator / executable grammar | `agent/src/alpha_foundry/search.py`; `mutators.py`; `dsl/executable.py`; `dsl/grammar.py` | `AlphaFoundrySearch`; `SeedMutator`; `ExecutableGrammarSnapshotV1`; `DEFAULT_EXECUTABLE_GRAMMAR`; `DEFAULT_GRAMMAR` | `executable_grammar_snapshot.v1`; grammar version/hash in factor identity | Both arms use one Activation candidate-factory adapter that binds the same `AlphaFoundrySearch`/`SeedMutator` factory and executable grammar manifest. | Yes: construction-only adapter; generation algorithms are not copied. | `agent/tests/alpha_foundry/test_mutators.py`; `dsl/test_executable_grammar.py`; `test_prearm_flat_schedule_v1.py` |
| Canonical factor identity / FactorDefinitionRecorded producer | `agent/src/alpha_foundry/dsl/identity.py` | `FactorIdentityService`; `FactorSpecSemantics`; `build_expression_identity`; `build_factor_spec_identity` | `FactorDefinitionRecorded` / `factor_definition_recorded.v1` | Every generated candidate is submitted to `FactorIdentityService.record_attempt`; invalid/duplicate terminals remain owned by that service. | No identity adapter beyond argument translation. | `agent/tests/alpha_foundry/dsl/`; identity/lifecycle cases in `agent/tests/research_ledger/test_research_events.py` |
| ProductionCandidateEvaluatorV1 | `agent/src/alpha_quality/production_evaluator_v1.py` | `ProductionCandidateEvaluatorV1`; `ProductionCandidateEvaluatorFactoryV1`; `ProductionEvaluationRequestV1`; `ProductionEvaluationResultV1` | `production_evaluation_request.v1`; protected evaluator node/terminal dossier events | Activation factory builds only closed request refs and obtains the evaluator through `ProductionCandidateEvaluatorFactoryV1`. | Yes: refs-only candidate-factory orchestration. No second evaluator. | `agent/tests/alpha_quality/test_production_evaluator_v1.py` |
| Deterministic evaluator DAG | `agent/src/alpha_quality/evaluator_dag_v1.py` | `DeterministicEvaluatorDAGSchedulerV1`; `ProductionCandidateDAGEvaluatorV1`; `ProductionCandidateDAGEvaluatorFactoryV1` | `evaluator_dag_policy.v1`; result semantics equal the serial evaluator | The same DAG factory is used for both arms after factor identity is recorded. | Narrow adapter selects existing DAG factory; no DAG logic is copied. | `agent/tests/alpha_quality/test_evaluator_dag_v1.py` |
| Predictive evidence producer | `agent/src/alpha_quality/predictive_evidence_v4.py` | `PITPredictiveEvidenceServiceV4`; `FactorOutputArtifactV3`; `PredictiveEvidenceV1`; `ScorecardDecisionEvidenceV4` | `FactorOutputRecordedV3`; `ObservedPanelPredictiveEvidenceRecorded`; `PITPredictiveEvidenceRecorded`; `ScorecardDecisionEvidenceV4Recorded` | Reached only through the existing production evaluator/DAG. | None. | `agent/tests/alpha_quality/test_predictive_evidence_v4.py` |
| Stateful A-share execution producer | `agent/src/alpha_quality/execution_evidence_v1.py` | `ExecutionEvidenceServiceV1`; `simulate_ashare_execution_v1`; `StatefulExecutionResultV1` | `ExecutionEvidenceRecorded` / `execution_evidence_recorded.v1`; `execution_artifact.v1` | Reached only through the existing production evaluator; Activation never recalculates execution returns. | None. | `agent/tests/alpha_quality/test_execution_evidence_v1.py` |
| Identity / complement / mechanism / applicability producers | `agent/src/alpha_quality/secondary_evidence_v1.py`; `evaluation_contract/applicability.py` | `SecondaryEvidenceServiceV1`; `ComparisonPoolServiceV1`; `ApplicabilityAssessmentServiceV1` | `ComparisonPoolFrozen` / `comparison_pool_frozen.v1`; `SecondaryEvidenceRecorded` / `secondary_evidence_recorded.v1`; `ApplicabilityAssessmentRecorded` / `applicability_assessment_recorded.v1` | Existing evaluator calls these producers; Activation matrix declares when their evidence is required and never accepts caller-authored N/A. | Matrix registration only; no evidence algorithm adapter. | `agent/tests/alpha_quality/test_secondary_evidence_v1.py`; `test_evaluation_contract_v1.py` |
| Claim Matrix producer | `agent/src/alpha_quality/claim_decision_v1.py` | `ClaimDecisionServiceV1`; `build_claim_matrix`; `ClaimAssessmentV1` | `ClaimMatrixRecorded` / `claim_matrix_recorded.v1`; `SelectionAssessmentRecorded` / `selection_assessment_recorded.v1` | Reached through the existing production evaluator when enabled; Activation consumes refs only. | None. | `agent/tests/alpha_quality/test_claim_decision_v1.py` |
| QualityDecision v3 | `agent/src/alpha_quality/decision_v2/source_v3.py` | `QualityDecisionV3Service`; `QualityDecisionInputBundleV3`; `FrozenDecisionEvidenceRepository` | `QualityDecisionV3Recorded` / `quality_decision_recorded.v3` | A narrow compatibility step invokes the existing service from immutable evidence refs because Run Source v3 accepts only v3 authority. It does not recompute evidence producers. | Yes: authority-version bridge only. Current evaluator also emits narrow Decision v4, which is not accepted by Run Source v3. | `agent/tests/alpha_quality/test_quality_decision_v3.py` |
| TrialTerminal / EvaluationRecorded / dossier builders | `agent/src/alpha_quality/production_evaluator_v1.py`; `research_dossier_v1.py`; lifecycle producer in `agent/src/research_ledger/events/store.py` | `TrialTerminalDossierV1`; `TrialTerminalDossierArtifactStoreV1`; `ResearchDossierServiceV1`; `CanonicalDossierResolverV1` | `EvaluationRecorded` / `evaluation_recorded.v1`; `TrialTerminated` / `trial_terminated.v1`; `TrialTerminalDossierRecorded` / `trial_terminal_dossier_recorded.v1`; research dossier v1 events | Activation factory returns exact terminal/evaluation/dossier refs produced by these existing boundaries. | Refs collector only. | `agent/tests/alpha_quality/test_production_evaluator_v1.py`; `test_research_dossier_v1.py` |
| Activation Run Source v3 | `agent/src/alpha_foundry/activation/run_source_v3.py` | `FormalActivationRunSourceV3`; `FormalActivationRunSourceAuditorV3` | `formal_activation_run_source.v3` read-only audit projection | `ActivationEvidenceProjector` delegates each arm to `FormalActivationRunSourceAuditorV3.audit` and computes pair evidence only from those exact source objects. | Yes: pair-level projector around the existing arm auditor. | `agent/tests/alpha_foundry/test_activation_formal_protocol_v3.py` (v3 authority rejection); new delegation/rebuild tests planned |
| Typed ResearchEventStore and content-addressed artifact writer | `agent/src/research_ledger/events/store.py`; `events/artifacts.py`; `events/model.py`; `events/payloads.py` | `ResearchEventStore`; `AtomicContentAddressedArtifactWriter`; `ResearchEventEnvelope`; `EventDraft` | `research_event.v1`; existing `sha256:` canonical artifacts | All new registrations append to this store; any new JSON artifacts use the existing writer. No alternate database/repository is permitted. | Additive protected event payload registrations only; legacy hashing remains replay-compatible. | `agent/tests/research_ledger/test_research_events.py`; `test_research_event_concurrency.py`; `test_atomic_artifact_writer.py`; `test_event_capability_closure.py` |

## First milestone touched files and planned gates

Planned touched files are limited to:

- `agent/src/alpha_foundry/activation/protocol_v2.py`
- `agent/src/research_ledger/events/payloads.py` (two additive closed payload schemas)
- `agent/src/research_ledger/events/store.py` (capability/protected-producer/identity registration only)
- `agent/tests/alpha_foundry/test_activation_protocol_v2.py`
- `agent/tests/research_ledger/test_event_capability_closure.py`
- `agent/tests/research_ledger/test_research_events.py`
- this PRE-FLIGHT record

Planned focused gates:

- domain separation and legacy SHA-256 replay identity;
- no authentication claim from plain hashes;
- fixed primary method and SESOI before outcomes;
- protocol/matrix protected producer boundaries and pre-outcome freeze;
- producer-owned N/A and final/forward non-feedback rules;
- direct caller mint rejection;
- event-chain replay and feature-off import/write behavior;
- Ruff, mypy/compileall where configured, `git diff --check`, and affected Alpha Foundry / Research Ledger regressions.

Later milestone file lists will be recorded before each implementation begins. No Activation-specific scorecard, execution engine, Claim Matrix, decision runner, factor parser, identity service, ledger, event store, or artifact repository is planned.

## Post-hardening production-boundary revalidation (2026-07-13)

The 16-row matrix above remains the reuse inventory. The following corrections
record facts discovered by executing the real boundaries; they supersede only
the affected call-boundary descriptions, not the historical pre-flight audit.

- `ProductionActivationCandidateFactoryV1` now calls the real
  `ProductionCandidateDAGEvaluatorV1`, obtains its
  `authoritative_result`, derives Decision evidence from exact evaluator event
  refs, invokes the existing `QualityDecisionV3Service`, and returns only
  terminal/evaluation/decision/dossier refs. The end-to-end boundary test is
  `test_factory_real_dag_evaluator_decision_and_dossier_chain`.
- The QualityDecision v3 bridge is not promotion-authoritative. Its input still
  uses caller-constructable `DecisionEvidenceRecord.v2`; the existing
  `QualityDecisionAuthorityGateV1` therefore caps every non-reject result at
  `research_only`. The factory binding exposes
  `QUALITY_DECISION_V3_PRODUCER_AUTHORITY_INCOMPLETE` rather than treating the
  bridge as candidate-zoo authority.
- Invalid/duplicate identity attempts still cannot use the existing
  `TrialTerminalDossierV1`, because that schema requires a real non-null
  `factor_spec_id`. The factory binding therefore also exposes
  `IDENTITY_TERMINAL_DOSSIER_PRODUCER_UNAVAILABLE`; no placeholder identity or
  Activation-specific dossier was created.
- `FormalActivationRunSourceAuditorV3` now binds a terminal to
  `EvaluationRecorded.factor_spec_id`, uses the Decision v3 `decision` field
  (not integer `tier`) for candidate-zoo eligibility, and verifies the terminal
  dossier's trial/terminal/evaluation/factor bindings. The production terminal
  payload does not contain `factor_spec_id`; the old assumption would have made
  all real effective candidates ineligible.
- Existing `ActivationResourceEvidenceV2` explicitly has
  `source_complete=false`, parent timeout unenforced, and RSS unavailable.
  Existing `IsolatedWorkerResourceV3` is a closed infrastructure probe only.
  Neither is reinterpreted as source-complete production-arm resource evidence.
- Readiness v4 now verifies the exact same-cycle protocol, applicability,
  provider authority, golden-slice readiness, run-input bundle, and factory
  reference chain. A merely valid/empty event chain is not source replay, and
  governance role separation is structurally checked rather than hard-coded.

No second ledger, event store, artifact repository, evaluator, scorecard,
execution engine, Claim Matrix, QualityDecision runner, parser, or identity
service was introduced during this revalidation.

## Recorded-authority correction (2026-07-14)

The accepted implementation was re-audited through the real event and artifact
boundaries after the post-hardening record above. These findings supersede its
remaining compatibility-blocker statements:

- `ProductionActivationCandidateFactoryV1` uses the production evaluator DAG's
  `authoritative_result`. For evaluated candidates, endpoint eligibility is
  bound to the existing production evaluator's dossier-cited
  `QualityDecisionV4Recorded`; the existing `QualityDecisionV3Service` is still
  invoked as the requested compatibility/replay path, but it is not allowed to
  override the production decision.
- Canonical invalid and duplicate attempts keep their real
  `TrialTerminated` event and intentionally have no fabricated factor identity
  or dossier. `FormalActivationRunSourceAuditorV3` requires dossiers exactly
  for evaluated terminals, not for identity-only terminals.
- `ActivationEvidenceProjector` delegates both arm audits to Activation Run
  Source v3, then records one `ActivationPairEvidenceV2Recorded` event and one
  canonical artifact with the existing `ResearchEventStore` and
  `AtomicContentAddressedArtifactWriter`. It cannot return an authoritative
  caller-constructed pair object.
- Pilot dispersion, confirmatory plan, and confirmatory analysis are now
  separately protected and replayable as
  `ActivationPilotDispersionV2Recorded`,
  `ActivationConfirmatoryPlanV2Registered`, and
  `ActivationStatisticalAnalysisV2Recorded`. Governance accepts only these
  same-store recorded artifacts.
- `ActivationGovernanceService` records
  `ActivationGovernanceDecisionV2Recorded` plus its canonical artifact. It
  consumes only the registered protocol, recorded statistical analysis, and a
  readiness event frozen before the complete transitive pilot/confirmatory
  source ancestry. Report JSON, worker summaries, raw statistics, and
  caller-authored decisions remain invalid inputs.

The resource finding is unchanged: evaluator-DAG child-node isolation and the
closed infrastructure probe do not prove whole-arm resource non-inferiority.
No Activation-specific execution engine was added to manufacture that proof.
