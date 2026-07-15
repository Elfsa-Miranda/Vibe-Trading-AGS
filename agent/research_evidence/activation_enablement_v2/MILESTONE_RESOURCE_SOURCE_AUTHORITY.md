# Activation Enablement V2 - Resource Source Authority Milestone

## Local lineage and scope

- Base: `codex/ags-v32-main` at
  `84d0890699f0b94c3e981b866794bf9a1f2165b5`.
- Feature branch: `codex/ags-v32-activation-resource-source-authority`.
- Scope is limited to the permitted Production Activation Candidate Factory
  adapter, Pair Coordinator, Pair Evidence Projector/Run Source v3 binding, and
  Activation readiness derivation.
- No remote operation occurred and no historical Phase 11 or Activation
  outcome artifact was modified.

## Authority correction

The prior request/result boundary allowed a caller to place a structurally
valid `resource_artifact_ref` in the pre-arm factory request. That reference was
not proven to belong to the same event store, plan, pair, run group, arm, or
event-chain prefix. It also could not represent a measurement of an arm that
had not started yet.

The corrected lifecycle follows the existing runner's real ordering:

```text
candidate factory starts and evaluates the arm inside the executor boundary
-> candidate factory returns evaluator/decision/dossier event refs only
-> existing scheduled runner returns and finishes whole-arm measurement
-> existing Activation service records the protected resource event/artifact
-> Pair Coordinator joins the resource event hash with factory refs
-> Activation Run Source v3 validates the exact refs and bindings
-> Pair Evidence Projector
```

Specific invariants:

- `ProductionActivationFactoryArmRequestV1` no longer accepts a caller resource
  artifact or any precomputed resource outcome.
- The factory result contains retrieval, terminal, evaluation, production
  QualityDecision v4, dossier, and arm-completion refs only. It contains no
  yield, score, metrics, decision, success count, or caller resource artifact.
- `ActivationPairCoordinatorV2.projector_refs` is a narrow post-executor refs
  join. It requires the resource hash to resolve to the existing protected
  `ActivationResourceMeasuredV2` event in the factory's event store.
- Run Source v3 requires exactly one resource event per arm and verifies the
  plan, pair, run group, arm, event family, source-complete state, and that the
  resource event follows every referenced arm outcome event.
- `ActivationArmEventRefsV2` requires the resource event ref, so the projector
  cannot silently omit resource evidence.
- Pair source completeness includes resource failures. Existing resource v2
  remains explicitly incomplete and therefore cannot support a pilot outcome.
- Readiness remains false for resource isolation because the existing v3 worker
  proves infrastructure probes only, while the existing scheduled-runner v2
  resource event is source-incomplete. It does not depend on post-outcome pair
  measurements, which would create a pre-pilot circular gate.

No Activation-specific execution engine or resource algorithm was created.
The existing scheduled runner/resource event family remains the only resource
producer. No second ledger, event store, artifact repository, evaluator,
scorecard, Claim Matrix, QualityDecision runner, parser, or identity service was
introduced.

## Validation

- Candidate Factory / Run Source / cycle focused: `63 passed`.
- All Activation-selected tests: `141 passed, 5835 deselected`, with five
  existing FastAPI/Starlette deprecation warnings.
- Research-event schemas, capability closure, and concurrency: `137 passed`.
- The final rerun of feature-off identity, DAG replay, OpenAPI snapshot, and
  selected security boundaries: `24 passed, 21 warnings`. An earlier broader
  unchanged-boundary run on this branch passed `84` tests with the same 21
  warnings.
- Changed-source Ruff: passed.
- Affected-source mypy with `--follow-imports=skip`: passed, five files.
- Activation compileall: passed.
- `git diff --check`: passed with only expected LF/CRLF worktree notices.
- Alpha Foundry full: `364 passed, 1 failed`. The sole failure is the unchanged
  accepted-baseline release-manifest mismatch caused by the user-owned root
  `problem.md`; neither that file nor `release_manifest.json` was modified.

## Remaining non-compensatory blockers

- No strict provider field-level PIT authority is available.
- No authority-qualified golden production slice or formal train/valid bundle
  can be opened.
- Existing `ActivationResourceMeasuredV2` is intentionally
  `source_complete=false`, has no isolated peak RSS, and does not enforce the
  whole-arm timeout. It therefore cannot make readiness true.
- No pilot or confirmatory outcome was opened. Official policy remains
  `flat_with_topology_shadow`; active research-only influence remains false;
  live/broker/order meaning remains none.
