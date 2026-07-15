# AGS v3.2 Production Evaluation — Final Local Execution Audit

## Audit identity

- Scope: integrated Phase 1 through Phase 11 execution requested from the two local production-evaluation documents.
- Accepted integration branch: `codex/ags-v32-main`.
- Integrated head before this audit commit: `2d8ddb569095528906acb777391c5482d71607ae`.
- Local-only rule: satisfied. No fetch, pull, push, PR, network data scrape, broker write, or live-trading action occurred.
- Preserved user input: repository-root `problem.md` was not edited.
- Feature-off compatibility and no-live boundaries remained mandatory throughout.

## Phase ledger

| Phase | Accepted merge | Outcome | Primary evidence |
|---|---:|---|---|
| 1 — evaluation profile / resolved contract / research family | `eda9a862` | accepted and merged | profile registry, resolved evaluation contract, applicability and research-family tests |
| 2 — PIT factor output / dual predictive evidence | `c07c5d25` | accepted and merged | PIT output, observed/PIT claim separation, predictive evidence v4 |
| 3 — serial production evaluator | `4558aa4d` | accepted and merged | `ProductionCandidateEvaluatorV1`, terminal lifecycle and production factory tests |
| 4 — stateful A-share execution | `9f1dd527` | accepted and merged | execution timing, holdings/trades, masks, cost and missing-return tests |
| 5 — producer-bound secondary evidence | `d2966329` | accepted and merged | identity, complement, mechanism, applicability and source-authority tests |
| 6 — claim matrix / narrow Decision | `621763c9` | accepted and merged | claim matrix, source-bound Decision v3 and non-compensatory tier tests |
| 7 — canonical dossiers / audience views | `6c8ecba4` | accepted and merged | terminal dossier, run report, release manifest and deterministic view tests |
| 8 — replayable baseline | `d325a65a` | accepted research-only baseline | `baseline_v1/baseline_manifest.json`; effective sample 21, 34 events, replay hash, zero final/forward access |
| 9 — deterministic evaluator DAG | `c87b042c` | accepted and merged | `evaluator_dag_v1/phase9_execution_record.json` |
| 10A — final-test authority v2 | `b2a5c846` | accepted and merged | `final_authority_v2/phase10a_execution_record.json` |
| 10B — falsification authority v2 | `98e7d34c` | accepted and merged | `falsification_authority_v2/phase10b_execution_record.json` |
| 10C — forward authority v3 | `26ddcfd7` | accepted and merged | `forward_authority_v3/phase10c_execution_record.json` |
| 11 — formal Retriever Activation | `2d8ddb56` | accepted as inconclusive, shadow, externally blocked | `activation_phase11/phase11_execution_record.json` and blocked dossier |

Phase 0 was an already accepted prerequisite before the requested Phase 1 start; this audit does not re-label it as work performed in this run.

## Phase 11 formal result

The formal experiment was not approved and was not silently abandoned.

- Two infrastructure-only dry-run groups executed as four independent spawn-process arms.
- The parent process enforced timeout termination; timeout was not merely observed after completion.
- Per-arm process-tree peak RSS was measured by the parent.
- Workers accepted a closed probe enum only and did not receive a ledger handle, caller callable, import path, formula, or arbitrary code.
- Twelve exploratory pilot pairs were frozen before outcomes with 6/6 counterbalanced first-arm order, identical budgets, and independent per-arm RNG/cache namespaces.
- Dry-run evidence is explicitly excluded from effect analysis.
- Pilot complete pairs: 0. Confirmatory complete pairs: 0. No pair outcomes were fabricated.
- Confirmatory endpoint, threshold, non-inferiority margins and the pilot-to-confirmatory power formula are frozen in the template; no confirmatory plan is minted before a source-complete pilot result.
- Formal run-source v3 accepts the current Flat pre-arm schedule / Retriever v7 and QualityDecision v3 sources only, derives attempts and yield from exact event hashes, and rejects legacy Retriever decisions.
- Verdict: `inconclusive`.
- Official search: `flat_with_topology_shadow`.
- Active research-only influence: false.

Exact external unblock conditions:

1. Bind a production Activation candidate factory to the frozen generator, `ProductionCandidateEvaluatorV1`, deterministic DAG scheduler and formal isolated-worker boundary.
2. Provide frozen producer-bound train/valid input artifacts with production authority. The Phase 8 bundled historical fixture remains explicitly external-unverified and research-only.

The local goal authorized execution budget, so lack of user permission is not recorded as a blocker. The two conditions above are missing authority/input capabilities, not missing Python packages.

## Acceptance matrix audit

| # | Requirement | Status | Verification basis |
|---:|---|---|---|
| 1 | all flags default false and freeze at app construction | pass | flag and feature-off tests |
| 2 | feature-off bench/CLI/API/OpenAPI/import/write identity | pass | feature-off golden and contract suite |
| 3 | child capability cannot bypass AGS parent | pass | flag capability closure tests |
| 4 | deterministic payload hash; position-sensitive event hash | pass | ledger hash fixtures/property tests |
| 5 | concurrent appends preserve one chain | pass | research-event concurrency tests |
| 6 | every outcome terminates | pass | evaluator/lifecycle/DAG tests |
| 7 | mutation, delete and unknown event types rejected | pass | append-only/event validation tests |
| 8 | replay reproduces projection hashes | pass | projection and authority replay tests |
| 9 | secrets, private paths and non-finite JSON safe | pass | security/redaction/artifact tests and Phase 11 scan |
| 10 | safe canonical equivalents share identity | pass | canonical AST tests |
| 11 | grammar/transform semantics change identity correctly | pass | identity/version tests |
| 12 | invalid formula creates no DAG node | pass | DSL/DAG adversarial tests |
| 13 | duplicate reuses node and records observation | pass | identity/DAG duplicate tests |
| 14 | missing parent, self-edge, cycle, forged depth rejected | pass | Factor DAG invariants |
| 15 | valid rejected child stays auditable but is not early-eligible | pass | lineage/discovery separation tests |
| 16 | similarity edge cannot become lineage | pass | DAG edge-kind tests |
| 17 | non-terminal outcomes cannot update discovery/process projections | pass | projection eligibility tests |
| 18 | placeholder AST diff/motif rejected | pass | process-memory source tests |
| 19 | base expectation frozen before generation | pass | action/process-memory ordering tests |
| 20 | low-confidence memory collapses to base policy | pass | retriever policy tests |
| 21 | positive memory cannot hard accept; negative veto is scoped | pass | memory/retriever decision tests |
| 22 | shadow mode cannot change official output | pass | official search control/shadow tests |
| 23 | RetrieverDecision records propensity, seed, policy, watermark | pass | Retriever v7 rebuild tests |
| 24 | final/forward events excluded from discovery views | pass | isolation/import-boundary tests |
| 25 | falsification contract precedes outcome access | pass | contract v2 authority ordering tests |
| 26 | unavailable decisive test is inconclusive/capped | pass | falsification and Decision tests |
| 27 | disappearance uses equivalence, not non-significance | pass | TOST/equivalence fixtures |
| 28 | multiplicity method/family frozen before results | pass | catalog/contract authority tests |
| 29 | Holm/BH/BY match reference fixtures | pass | multiplicity suite |
| 30 | sequential e-process preserves declared null behavior | pass | sequential simulations and service tests |
| 31 | regime definition train-only and frozen | pass | regime-freeze tests |
| 32 | overlapping/time-dependent inference is dependence-aware | pass | HAC/block fixtures |
| 33 | sign-flipped duplicate detected | pass | identity/complement tests |
| 34 | redundant high-IC, negative-marginal factor rejected/capped | pass | complement/decision adversarial cases |
| 35 | weaker orthogonal positive-net factor may reach candidate_zoo | pass | positive orthogonal fixture |
| 36 | no metric compensates for hard fail/cap | pass | non-compensatory Decision tests |
| 37 | caller score/decision/warning truth rejected | pass | Decision authority and override tests |
| 38 | paper_candidate requires one frozen final evaluation | pass | final authority v2 and Decision tests |
| 39 | forward_track requires frozen plan and implies no success | pass | forward authority v3 tests |
| 40 | regime dependence is advisory only | pass | Decision advisory tests |
| 41 | API remains GET-only and flag-off routes absent | pass | OpenAPI/API method security tests |
| 42 | no forbidden broker/live/runtime imports | pass | architecture/import-boundary tests |
| 43 | path, report, API and secret adversarial cases safe | pass | security suite |
| 44 | deterministic resource/performance budgets pass | pass | acceptance/performance suite and Phase 11 resource probes |
| 45 | rollback preserves v3.1 behavior and live/order safety | pass | feature-off identity and local-only architecture tests |

## Production-execplan cross-phase audit

| Area | Status | Notes |
|---|---|---|
| Profile / contract / N/A | pass | closed profile registry; exact hashes; applicability proofs; no tier-invariant escape |
| PIT / data / split | pass with scoped baseline limitation | producer-bound snapshots and split isolation pass; Phase 8 fixture remains external-unverified |
| Evaluator / lifecycle | pass | one production factory, refs-only evaluator, exact terminal and dossier semantics |
| Predictive / claim | pass | observed-panel and PIT claims remain distinct; blocked claims retain root causes |
| Execution | pass | stateful A-share holdings/trades/cost/missing-return semantics covered |
| Identity / complement / mechanism | pass | producer-bound evidence; duplicate and complement are non-compensatory |
| Decision | pass | narrow protected sources, legacy cap, deterministic replay, final/forward gating |
| Artifact / event / replay / concurrency | pass | content/path/blob hashes, protected events, exact-prefix replay and concurrency tests |
| Dossier / audience views | pass | canonical fact layer; views do not recalculate verdicts; reports never decide |
| Final / forward / Activation | pass, Activation remains shadow | Phase 10 authorities closed; Phase 11 timeout/RSS/protocol complete but statistical activation is externally blocked |

## Validation record

- Phase 11 protocol/red-team: 8 passed after final regeneration.
- Focused current and legacy Activation authority regression: 60 passed.
- Alpha Foundry excluding the known user-input manifest case: 273 passed.
- Release-manifest file: 7 passed; one known baseline failure reproduced because the test hashes user-owned repository-root `problem.md`.
- Alpha Quality + Research Ledger: 613 passed, one existing deprecation warning, 1430.67 seconds.
- Contracts/security/acceptance/performance/feature-off from repository root: 95 passed, existing deprecation warnings only.
- The same root-relative suite invoked from `agent/` produced 91 passes and four expected `FileNotFound` failures; rerunning from its required repository-root working directory passed all 95.
- Phase 11 source: Ruff clean; strict mypy clean.
- `python -m pip check`: no broken requirements.
- Integrated-head smoke: Phase 11 formal protocol plus Phase 10C forward authority exited successfully.

## Dependency record

Installed from official PyPI during the integrated execution where required:

- `hypothesis==6.156.6`
- `pandas-stubs==3.0.3.260530`
- `types-psutil==7.2.2.20260518`
- `scipy-stubs==1.16.3.3`

The numerical environment was restored to compatible `numpy==2.3.5`, `scipy==1.16.3`, and `numba==0.62.1`; `pip check` passed.

## Final disposition

Phase 1 through Phase 11 implementation work is locally integrated and audited. Phase 11 is intentionally not an effectiveness approval: it is a complete fail-closed experimental package with real infrastructure evidence, frozen pilot scheduling, typed blockers and exact unblock conditions. No final-test, forward-success, production-readiness or live-trading claim is inferred from this disposition.
