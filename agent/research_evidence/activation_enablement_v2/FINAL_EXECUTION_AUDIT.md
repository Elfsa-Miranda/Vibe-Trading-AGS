# ACTIVATION ENABLEMENT V2 OUTCOME

## Accepted integration base

- Initial accepted implementation baseline: `177bd64d6b9879606b3711223d48a255b1165f4b`.
- Activation Enablement V2 work was staged from the accepted local
  `codex/ags-v32-main` branch only.
- No fetch, pull, push, remote PR, remote status query, or remote dependency was
  used.
- The historical Phase 11 inconclusive dossier and execution record remain
  unchanged.

## Milestone branches and commits

- `codex/ags-v32-activation-contract-v2`: `ae870269`.
- `codex/ags-v32-provider-pit-audit-v1`: `d63d781f`.
- `codex/ags-v32-activation-inputs-v1`: `9068884d`.
- `codex/ags-v32-activation-factory-v1`: `f67043f0`.
- `codex/ags-v32-activation-cycle-v2`: recorded by the local Git history that
  contains this audit.

## Hash standard

New Activation V2 objects use domain-separated canonical SHA-256 through
`CanonicalHashSpecV1`. Existing `sha256:` identities replay unchanged. A plain
hash explicitly makes no authentication claim.

## Provider field PIT audit

The registered Tushare adapter remains `best_effort/blocked` for formal
Activation. The provider interface does not expose sufficient authoritative
row availability/vintage timestamps, complete revision history, or verified
rate-limit completeness to support strict PIT claims. Unknown or partial fields
fail closed.

## Golden production slice and full train/valid authority

No real provider-backed golden slice was opened. Consequently no formal
`ProductionActivationRunInputBundleV1Registered` event was minted. Test/final
and forward data were not accessed.

## Production candidate factory

`ProductionActivationCandidateFactoryV1` is implemented as a narrow adapter
over the existing generator, canonical identity service, production serial/DAG
evaluator, QualityDecision v3 service, typed event store, and terminal/dossier
boundary. Both arms share the same factory; the request exposes no independent
contract, snapshot, grammar, budget, generator, evaluator, or decision override.
Its public results contain refs only.

Two production compatibility facts remain fail-closed:

- invalid/duplicate identity terminals do not currently have an existing
  production dossier producer;
- the evaluator's current producer-bound narrow decision is v4, while Formal
  Activation Run Source v3 requires separately producer-bound Decision v3
  evidence refs.

The implementation binding therefore cannot be promoted into a formal cycle
binding event.

## Readiness result

`ready_for_pilot_outcome_access = false`.

The exact typed artifact is `activation_readiness_v4.json`, with hash
`sha256:7a4f633ffad1ef43bf2bb4cc69cf4b9821148f86f410496a96fdd417d0a770b4`.
The provider, production input, formal factory binding, new pair schedule,
source replay, production-arm resource isolation, cycle protocol registration,
and cycle applicability registration gates are not all satisfied. Shadow mode
is mandatory.

## Pilot

- Planned pairs: `0` (scheduling is forbidden before readiness).
- Complete pairs: `0`.
- Incomplete pairs: `0`.
- Treatment fidelity: not observed.
- Variance/dispersion/completion/resource evidence: not opened.

No pilot uplift exists and no SESOI was changed.

## Confirmatory plan and execution

No `PreregisteredConfirmatoryActivationPlanV2` was minted because there is no
source-complete pilot dispersion artifact. The implementation supports fixed
SESOI, conservative variance upper bounds, simulation/power identity,
counterbalanced seeds/orders, and an explicit infeasible outcome, but none of
those schemas is an outcome artifact for this blocked cycle.

- Confirmatory complete pairs: `0`.
- Invalid/incomplete pairs: `0` (execution did not start).
- Source/replay result: unavailable before formal inputs.
- Primary effect: not estimated.
- Confidence interval: not estimated.
- Randomization p-value: not computed.
- Safety/resource/failure/diversity gates: unresolved.
- Power/completeness: insufficient/not opened.

## Final verdict and policy

- Final verdict: `inconclusive` (pre-outcome readiness blocked; no experiment
  was opened or invalidated).
- Official search policy: `flat_with_topology_shadow`.
- Active research-only influence: `false`.
- No live, broker, order, final-feedback, or forward-feedback meaning is
  implied.

## Reuse and no-parallel-implementation audit

The PRE-FLIGHT Reuse Matrix is the authoritative capability inventory. Added
runtime modules are limited to the permitted candidate factory adapter, pair
coordinator, pair evidence projector, statistical analyzer/protocol support,
governance/readiness service, and provider field PIT audit. No second ledger,
event store, artifact repository, factor parser, identity service, candidate
evaluator, scorecard, execution engine, Claim Matrix, or QualityDecision runner
was created.

## Validation notes

Pre-merge validation from the cycle worktree:

- Alpha Foundry, excluding the separately reproduced release-manifest baseline
  failure: `338 passed in 79.80s`.
- Alpha Quality + Research Ledger: `622 passed, 1 warning in 1119.27s`.
- Contracts + security + acceptance + performance + feature-off identity:
  `95 passed, 21 warnings in 9.87s`.
- Final focused Activation/provider/event suite: `197 passed in 7.03s`.
- Changed-file Ruff: passed.
- Affected-source mypy (`--follow-imports=skip`, 8 source files): passed.
- `python -m compileall -q agent/src agent/tests`: passed.
- `python -m pip check`: `No broken requirements found`.
- `git diff --check`: passed; Git emitted only expected local LF/CRLF notices.

The repository-wide Ruff audit was also executed and reported 258 pre-existing
errors across unrelated legacy modules and examples. Changed-file Ruff is
clean; unrelated files were not mass-reformatted.

`test_release_manifest.py` was run without exclusion and reproduced the known
baseline result: `1 failed, 7 passed`. The sole failure is
`test_tracked_release_manifest_matches_deterministic_rebuild`, because the
release manifest reads the user-owned root `problem.md` whose blob differs from
the tracked manifest. The same failure was reproduced on accepted main before
this work; neither the file nor the tracked release manifest was changed.

Post-merge staged verification is recorded in local Git/task output.
