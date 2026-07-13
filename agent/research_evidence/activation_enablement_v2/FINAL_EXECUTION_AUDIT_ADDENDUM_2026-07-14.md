# AGS v3.2 Activation Enablement V2 - Final Audit Addendum

## Accepted local lineage

- Accepted branch: `codex/ags-v32-main`.
- Audit-start accepted commit: `c336ea3e`.
- This addendum does not replace or rewrite any historical readiness, Phase 11,
  pilot, confirmatory, or outcome artifact.
- The user-owned worktree state in
  `agent/research_evidence/production_evaluation_v32/final_execution_audit.md`
  was not staged or overwritten.
- No fetch, pull, push, remote query, PR operation, reset, clean, or checkout
  over user work occurred.

## Reuse and no-parallel-implementation result

The required 16-capability PRE-FLIGHT inventory remains recorded in
`PREFLIGHT_REUSE_MATRIX.md`. The implemented production path continues to use:

```text
existing Flat pre-arm schedule or Retriever v7 policy
-> existing AlphaFoundrySearch generator and executable grammar
-> existing FactorIdentityService / FactorDefinitionRecorded producer
-> existing ProductionCandidateDAGEvaluatorV1
-> existing ProductionCandidateEvaluatorV1 and evidence DAG
-> existing production QualityDecision/dossier authority
-> existing QualityDecisionV3Service compatibility/replay call
-> existing TrialTerminated / EvaluationRecorded / dossier events
-> existing typed ResearchEventStore and content-addressed writer
```

No second ledger, event store, artifact repository, candidate evaluator,
scorecard, execution engine, Claim Matrix, QualityDecision runner, parser, or
factor identity service exists in the Activation implementation.

## Additional accepted authority corrections

### Exact arm resource source binding

- Feature commit: `f2a49684`.
- Local merge commit: `5e1ae9b8`.
- The candidate factory no longer accepts a pre-arm caller resource artifact.
- Factory output exposes only retrieval, terminal, evaluation, production
  decision, dossier, and completion refs; it exposes no metric or verdict.
- The existing scheduled runner completes its measurement after the executor
  returns. The Pair Coordinator then joins the protected same-store resource
  event hash to the refs-only factory result.
- Run Source v3 requires exactly one bound resource event per arm and rejects
  missing, wrong-family, wrong plan/pair/run-group/arm, source-incomplete, or
  outcome-preceding resource evidence.

### Exact safety/resource/failure noninferiority reconstruction

- Feature commit: `bb28e66b`.
- Local merge commit: `c336ea3e`.
- Statistical Analyzer v2 no longer trusts pair projection NI booleans.
- It resolves exact `TrialTerminated` and `ActivationResourceMeasuredV2` refs
  from the analyzer's typed event store, then applies the registered margins.
- New protocol registrations freeze safety endpoints
  `duplicate_rate/failure_rate` and resource endpoints
  `cpu_seconds/peak_rss_mb/wall_seconds`.
- Previously valid v2 `peak_rss_mb/wall_seconds` protocols remain replayable;
  historical protocols are not silently rewritten.
- Missing, non-finite, negative, wrong-family, or source-incomplete resource
  evidence is unresolved, never a pass. A false NI gate remains a
  non-compensatory governance rejection.

## Readiness re-audit and mandatory stop

`ready_for_pilot_outcome_access` remains false.

The local environment and repository were inspected without opening outcome
data:

- `TUSHARE_TOKEN`: absent (presence only checked; no value read or printed).
- `TUSHARE_API_TOKEN`: absent (presence only checked; no value read or printed).
- strict provider field-level PIT authority: unavailable;
- authority-qualified golden production slice: unavailable;
- formal production train/valid input bundle: unavailable;
- source-complete whole-arm production resource event: unavailable;
- appendable formal Activation research-event database: not present in the
  workspace. Creating a temporary database would not be formal authority and
  was not done.

The tracked `activation_readiness_v4.json` remains an immutable historical
false artifact and was not overwritten. In a new formal research cycle, code
availability alone cannot substitute for recorded protocol, provider, golden
slice, run-input, factory binding, applicability, schedule, source-replay, and
resource-isolation events.

The existing `ActivationResourceMeasuredV2` contract is deliberately
`source_complete=false`, has no isolated process-tree RSS, and does not enforce
the whole-arm timeout. The isolated-worker v3 code is explicitly restricted to
infrastructure probes and cannot be treated as production-arm effect or NI
evidence. Adding an Activation-specific execution engine to bypass this gap is
forbidden.

Therefore no outcome access was legal:

- pilot planned/started/complete pairs: `0 / 0 / 0`;
- pilot dispersion, resource, order-effect, and treatment-fidelity outcomes:
  not accessed;
- confirmatory plan/execution/analysis: not minted/not run;
- primary effect, CI, and randomization p-value: unavailable;
- safety/resource/failure/diversity outcome gates: not evaluated;
- final/test/forward data: not accessed;
- final verdict: `inconclusive` (pre-outcome readiness blocked);
- official policy: `flat_with_topology_shadow`;
- active research-only Topology influence: `false`;
- live-trading meaning: none.

## Final validation evidence

- Resource-source Candidate Factory / Run Source / cycle focus: `63 passed`.
- Resource-source Activation selection: `141 passed, 5835 deselected`, five
  existing FastAPI/Starlette deprecation warnings.
- Exact-NI statistical/governance focus: `41 passed`.
- Exact-NI Activation selection: `146 passed, 5835 deselected`, five existing
  FastAPI/Starlette deprecation warnings.
- Research-event schema/capability/concurrency: `137 passed`.
- Alpha Foundry full after final exact-NI change: `369 passed, 1 failed`. The
  sole failure is the accepted-baseline release-manifest mismatch caused by
  the user-owned `D:\Vibe-Trading\problem.md`; neither that file nor
  `agent/research_evidence/release_manifest.json` was modified.
- Final changed-source Ruff: passed.
- Final changed-source mypy with `--follow-imports=skip`: passed.
- Final changed-source compileall: passed.
- `pip check`: `No broken requirements found.`
- `git diff --check`: passed with expected LF/CRLF worktree notices only.

## Exact next evidence required

1. A strict, row-level, field-by-field PIT provider audit that passes the
   registered closed catalog without fallback inference.
2. A provider-bound golden production slice and formal train/valid input bundle
   recorded in the same authoritative event store.
3. A reviewed existing production runner capability that executes the closed
   production arm in a fresh spawn process, enforces parent timeout, measures
   process-tree CPU/wall/RSS, and emits a source-complete protected resource
   event. This must extend the existing runner, not create an Activation-specific
   execution engine.
4. A durable formal Activation event DB/artifact root in which the new research
   cycle can register protocol, applicability, schedules, bindings, readiness,
   and later exact pair evidence.
5. Only after a new recorded Readiness V4 has no blockers may pilot outcome
   access begin. Confirmatory planning remains downstream of source-complete
   pilot evidence.
