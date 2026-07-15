# ACTIVATION ENABLEMENT V2 — POST-HARDENING OUTCOME

## Accepted local lineage

- Accepted branch at final-audit start: `codex/ags-v32-main`.
- Accepted commit at final-audit start:
  `7a0f5ba0f6ccead4c908b2ea22853efa9439cc93`.
- Historical Phase 11 dossier, readiness artifact, events, and original
  `FINAL_EXECUTION_AUDIT.md` were preserved and were not rewritten as approved.
- The user-owned line-ending-only state in
  `agent/research_evidence/production_evaluation_v32/final_execution_audit.md`
  was preserved.
- No fetch, pull, push, remote query, PR, reset, clean, or checkout over user
  work was performed.

## Milestone branches and local commits

- `codex/ags-v32-activation-protocol-hardening`: `56022c70`; merged by
  `ba5b2877`.
- `codex/ags-v32-activation-factory-hardening`: `0ae8dc5b`; merged by
  `ce5c8e8c`.
- `codex/ags-v32-activation-projector-hardening`: `d0d5abd9`; merged by
  `8289935f`.
- `codex/ags-v32-activation-governance-hardening`: `0f3f6884`; merged by
  `7a0f5ba0`.
- `codex/ags-v32-activation-final-audit`: documentation-only final evidence
  milestone; its commit is recorded in local Git history.

All branches were created fresh from the then-current accepted
`codex/ags-v32-main` and locally merged only after their focused gates passed.

## Reuse and no-parallel-implementation result

The PRE-FLIGHT matrix records all 16 required capabilities with actual modules,
classes/services/factories, event/schema versions, call paths, adapters, and
tests. Runtime changes remain within the allowed narrow Activation factory,
pair projector/run-source binding, statistical protocol/analyzer, governance,
and provider PIT audit boundaries.

No Activation-specific scorecard, execution engine, Claim Matrix,
QualityDecision runner, factor parser, identity service, second candidate
evaluator, second ledger, second event store, or second artifact repository was
created. Flat and Topology use the same generator/evaluator factories, contract
snapshot, grammar, budgets, and identity service; only the frozen retriever
policy differs.

## Production boundary corrections

1. The candidate factory's real path is:
   existing retrieval authority → existing generator →
   `FactorIdentityService` → `ProductionCandidateDAGEvaluatorV1` →
   `ProductionCandidateEvaluatorV1` → exact evaluator events → existing
   `QualityDecisionV3Service` → existing terminal/dossier events.
2. A real compatibility bug was fixed: the DAG result property is
   `authoritative_result`, not the nonexistent `authoritative`.
3. The factory no longer accepts caller Decision evidence refs. It derives its
   compatibility records from exact production evaluator event refs and returns
   refs only.
4. Run Source v3 now obtains factor identity from the referenced
   `EvaluationRecorded` event because production `TrialTerminated.v1` has no
   `factor_spec_id`. It also uses Decision v3's string `decision` rather than
   comparing integer `tier` with decision names.
5. Terminal dossier trial, terminal, evaluation, and factor bindings are
   verified before a candidate can enter the projected endpoint.
6. Statistical analysis requires a registry-minted protocol present in the
   exact same `ResearchEventStore`. Pilot summary and confirmatory-plan minting
   use the same check.
7. Confirmatory power planning uses the frozen seed/count and conservative
   variance in a simulation engine; observed pilot uplift cannot change SESOI.
8. Readiness no longer equates `verify_chain()` with source completeness or
   hard-codes governance role separation.

## Strict PIT and production-input result

`ready_for_pilot_outcome_access = false`.

The only repository production adapter is the registered Tushare CSI300 path.
It cannot support `verified_strict` Activation authority because the current
interface does not provide authoritative row-level availability timestamps,
revision-complete statement vintages, independently checked cross-interface
consistency, or a complete rate-limit/retry batch manifest. No provider token
was present or read. No real golden slice or producer-bound train/valid input
bundle exists.

Acceptable future source alternatives, each requiring a new registered adapter
and the same field-level audit, are:

- a vendor/exchange bitemporal feed with effective time, provider-availability
  time, retrieval vintage, immutable raw partitions, and revision identifiers;
- a receipt-logging gateway around the provider that content-addresses every
  raw response and proves retry/rate-limit batch completeness;
- an independently controlled historical-as-of archive with signed/anchored
  heads, complete restatement chains, and cross-interface reconciliation.

Adapter reputation, configuration booleans, end dates, local file mtimes, and
plain hashes are not substitutes for those facts.

## Exact remaining typed blockers

- `PROVIDER_FIELD_AUDITS_SUFFICIENT`: strict provider authority unavailable.
- `REAL_PRODUCTION_GOLDEN_SLICE_UNAVAILABLE`: no authority-qualified slice was
  opened.
- `PRODUCTION_TRAIN_VALID_INPUT_BOUND`: no formal run-input bundle can be
  registered before the first two blockers close.
- `IDENTITY_TERMINAL_DOSSIER_PRODUCER_UNAVAILABLE`: invalid/duplicate attempts
  have no real factor ID but the existing dossier schema requires one.
- `QUALITY_DECISION_V3_PRODUCER_AUTHORITY_INCOMPLETE`: the existing v3 service
  intentionally caps generic evidence at `research_only`; the Activation
  endpoint counts `candidate_zoo+` only.
- `PRODUCTION_ARM_RESOURCE_EVIDENCE_UNAVAILABLE`: v2 resource evidence is
  explicitly incomplete and isolated-worker v3 is probe-only.
- `PAIR_EVIDENCE_SOURCE_INCOMPLETE`: exact source-complete pairs therefore
  cannot yet be minted for outcome analysis.

These are non-compensatory blockers. No metric, narrative, report, worker
summary, or caller boolean can override them.

## Pilot and confirmatory execution

- Pilot planned/started/complete pairs: `0 / 0 / 0`.
- Pilot variance, overdispersion, zero-yield, completion, CPU/wall/RSS,
  treatment-fidelity, and order-effect outcomes: not accessed.
- Confirmatory plan: not minted, because there is no source-complete pilot.
- Confirmatory complete/incomplete/invalid pairs: `0 / 0 / 0`.
- Primary effect, CI, randomization p-value, safety/resource/failure NI,
  diversity, order sensitivity, and power/completeness outcome: not estimated.
- Final/test/forward data: not accessed and not fed back.

This is the required stop condition: readiness is false, so opening pilot or
confirmatory outcomes would itself invalidate the experiment.

## Validation evidence

- Focused protocol/analyzer: `38 passed`.
- Protocol milestone all Activation tests at that point: `99 passed,
  249 deselected`.
- Research event capability/regression: `131 passed`.
- Candidate factory focused: `18 passed`; affected evaluator/DAG/Decision:
  `65 passed`.
- Run-source milestone Activation: `102 passed, 249 deselected`.
- Governance milestone focused: `50 passed`; Activation full:
  `104 passed, 249 deselected`.
- Final Alpha Foundry full: `352 passed, 1 failed`. The sole failure is the
  accepted-main baseline release-manifest mismatch caused by the user-owned root
  `problem.md`; neither input was modified here.
- Final Alpha Quality + Research Ledger full: `620 passed, 4 failed, 1 warning`.
  All four failures are the same date-sensitive forward fixture: the fixture
  claims `available_at=2026-07-14T00:00:00Z` while the governed current date is
  2026-07-13, so the production availability contract correctly rejects future
  rows. These tests and forward code are outside Activation and were not changed.
- Contracts + security + acceptance + performance + feature-off + replay +
  concurrency, executed from the required repository root: `105 passed,
  21 warnings`.
- An earlier invocation of that same group from `agent/` produced four relative
  path `FileNotFoundError`s; the correctly rooted rerun above is authoritative.
- Changed-file Ruff across the post-cycle Python diff: passed.
- Affected-source mypy with `--follow-imports=skip`: `Success: no issues found
  in 6 source files`.
- `python -m compileall -q agent/src agent/tests`: passed.
- `python -m pip check`: `No broken requirements found`.
- `git diff --check`: passed; Git emitted only the expected local LF/CRLF
  notice for the edited Markdown file.

## Final verdict and policy

- Verdict: `inconclusive` — pre-outcome readiness blocked, not an analyzed null
  result and not an invalidated opened experiment.
- Official research policy: `flat_with_topology_shadow`.
- Active research-only Topology influence: `false`.
- Live/broker/order authority: none.
- Historical results: preserved and not reinterpreted.
