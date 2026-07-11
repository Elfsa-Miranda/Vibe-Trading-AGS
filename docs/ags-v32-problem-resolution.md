# AGS v3.2 Problem Resolution Ledger

This file records implementation status only. The unique audit input remains
`D:\Vibe-Trading\problem.md`; this ledger does not add, remove, downgrade, or
reword its requirements.

## Frozen inputs

- Accepted code branch: `codex/ags-v32-main`
- Accepted code commit: `e11630c33f07600b5e9fd6af7894dbd6432ff566`
- Dedicated repair branch: `codex/ags-v32-m0-canonical-evidence-baseline`
- Repair merge-base: `e11630c33f07600b5e9fd6af7894dbd6432ff566`
- Audit document worktree commit:
  `5f71c8d6599533b4cde8d900e71990b1b3ed5747`
- Latest audit input: 1,350 UTF-8 lines, SHA-256
  `38dce160d10cda051bde5e6d280b34c00682c75c07b686dc005d70d5d96f6732`
- Formal Activation outcomes: unopened

Status meanings:

- `audit_reported`: present in the audit input; current code verification and
  remediation remain required.
- `in_progress`: production code or tests are being changed on the named
  milestone branch.
- `partial`: a bounded prerequisite landed, but the audit item's complete
  acceptance evidence is still absent.
- `verified`: current code and the complete named adversarial matrix prove the
  audit requirement. No item is marked verified from prose or a narrow test.

## P0/P1 dependency ledger

| Item | Planned milestone | Status | Current authoritative evidence |
|---|---|---|---|
| P0-01 | M1 split/timing | partial | frozen timing/split primitives exist; scorecard capability migration remains open |
| P0-02 | M1 evidence/Decision | audit_reported | none |
| P0-03 | M1 production evaluator | audit_reported | none |
| P0-04 | M1/M2 PIT snapshot | audit_reported | none |
| P0-05 | M2 A-share adapter | audit_reported | none |
| P0-06 | M2 stateful execution | audit_reported | none |
| P0-07 | M1 split registry | partial | content-hashed calendar/policy/plan models pass adversarial tests; producer-scoped registration remains open |
| P0-08 | M1/M2 execution policy | audit_reported | none |
| P0-09 | M1 immutable output | partial | exact-axis immutable byte content passes adversarial tests; formal Parquet/Arrow refs and evaluator consumption remain open |
| P0-10 | M1 scorecard/final boundary | audit_reported | none |
| P0-11 | M1 split-safe scorecard | audit_reported | none |
| P0-12 | M1 final eligibility/provider | audit_reported | none |
| P0-13 | M1 falsification v2 | audit_reported | none |
| P0-14 | M1 weighting policy | audit_reported | none |
| P0-15 | M1 Decision invariants | audit_reported | none |
| P0-16 | M1 exact projection bundle | audit_reported | none |
| P0-17 | M1 atomic artifact writer | partial | canonical create-if-absent writer passes collision/concurrency tests; legacy writers and event transitions remain unmigrated |
| P0-18 | M1 forward authority/vintage | audit_reported | none |
| P1-01 | M1 production timing binding | audit_reported | none |
| P1-02 | M1 executable grammar | partial | runtime grammar/backend intersection and typed pre-compute skip pass; production lifecycle integration remains open |
| P1-03 | M2 partitioned artifacts | audit_reported | none |
| P1-04 | M1 scorecard v2 | audit_reported | none |
| P1-05 | M1 production search factory | audit_reported | none |
| P1-06 | M1 worker/resource evidence | audit_reported | none |
| P1-07 | M0 release baseline, then M1 report binding | partial | deterministic manifest and adversarial tests; event-bound GET/CLI refs remain open |
| P1-08 | after M1/M2 authority closure | audit_reported | active path remains fail-closed |
| P1-09 | M2 formal cache isolation | audit_reported | none |
| P1-10 | M1/M2 Complement policy | audit_reported | none |
| P1-11 | M1 regime producer | audit_reported | none |
| P1-12 | M1 execution/calendar policy | partial | horizon/holding/rebalance/entry/exit/calendar are explicit and hashed; production evaluator binding remains open |
| P1-13 | M1 flag/route defense | audit_reported | none |
| P1-14 | M1 final typed terminal | audit_reported | none |

P2-01 through P2-06 remain experiment and claim-scope gates. They cannot be
closed by fixture-only engineering tests. P2-06 specifically requires
pre-final external-validity design, one-shot family binding, regime/universe
coverage, valid-to-test attenuation reporting, and claim narrowing when
coverage is insufficient.

## M0 evidence

`ReleaseManifestBuilderV1` deterministically rebuilds
`agent/research_evidence/release_manifest.json` from the tracked legacy
Activation artifacts and historical claim documents. It:

- reopens strict content-addressed plan/result/decision JSON;
- verifies semantic hashes and plan-result-decision relationships;
- binds the latest audit-input hash and accepted code commit;
- compares the plan treatment policy with the current v3 policy;
- labels every legacy artifact unverified and superseded;
- fixes the empirical status at `not_established` because source-event and
  closed test-run records are not tracked; and
- has no input for a verdict, empirical status, warning, cap, source event, or
  test result.

Verification on this branch:

- release-manifest adversarial tests: `8 passed`, including checkout
  CRLF/LF invariance for historical Markdown evidence;
- Alpha Foundry plus Research Ledger: `351 passed in 70.65s` after the
  checkout-invariance correction;
- Alpha Quality, contracts, security and acceptance:
  `245 passed in 66.80s`, with 21 existing deprecation warnings;
- one-source mypy: passed;
- isolated-prefix compileall and diff checks: passed.

M0 does not make any market, Retriever-efficacy, candidate-zoo, final-test, or
forward claim. P1-07 remains only partial until GET/CLI formal conclusions are
restricted to event/release-bound canonical references.

## M1 authority-primitives evidence

Branch `codex/ags-v32-m1-authority-primitives` was created from accepted M0
main commit `5f9fb2141c13b01668bd822d9ae9f21947d93ef1`.

The additive `AtomicContentAddressedArtifactWriter` validates strict finite
closed JSON, recomputes the semantic hash, derives the semantic path, creates
the path without overwrite from a fully fsynced staging file, and reopens the
artifact to bind canonical bytes and blob hash. Existing wrong or noncanonical
content is a typed conflict. Same-content concurrent writes are idempotent.
The constructor performs no directory creation, preserving feature-off IO
boundaries for future gated services.

`FrozenTradingCalendarV1`, `EvaluationTimePolicyV1`, and `FrozenSplitPlanV1`
make calendar source, signal/order/entry/exit timing, horizons, holding period,
rebalance cadence, purge and embargo explicit and content hashed. Split gaps
and outcome-contained signal endpoints are derived in trading-day positions;
reversed, overlapping, unregistered-calendar and insufficient-gap plans fail.

These are prerequisites, not closure claims. Legacy scorecard, generic event
append, existing artifact writers, PIT data, production evaluator and Decision
evidence do not yet consume these objects, so P0-01/P0-07/P0-17/P1-12 remain
partial.

Current verification:

- focused atomic-writer and evaluation-policy matrix: `19 passed`;
- same-content 32-writer concurrency case: `10/10` independent repetitions
  passed after the Windows path correction;
- Alpha Quality plus Research Ledger: `277 passed in 44.19s`;
- Alpha Foundry, contracts, security and acceptance:
  `338 passed in 65.48s`, with 21 existing deprecation warnings;
- two-source cold mypy: passed.

The first concurrent writer run exposed a Windows extended-path (`\\?\`)
normalization race. The implementation now normalizes extended paths and
rechecks the resolved parent immediately before linking, preserving both
concurrency and junction/symlink containment. The complete focused and broad
matrices were rerun after the correction.

## M1 executable-input/output evidence

Branch `codex/ags-v32-m1-executable-output` was created from accepted main
`0fccd8fadc85384453120a54ba96c03c23e9b43f`.

`ExecutableGrammarSnapshotV1` is rebuilt from the frozen safe grammar and the
actual core backend registry. The seven grammar-only operators are excluded
and produce typed `BACKEND_OPERATOR_UNAVAILABLE` before backend computation.
Every advertised operator and retained alias is exercised against the real
backend in tests. Runtime snapshot fields cannot be changed with
`dataclasses.replace` while preserving a valid snapshot.

`FrozenFactorOutputV2` rejects duplicate/unsorted axes, shape mismatch,
Infinity, nullable/non-bool masks and NaN observations marked valid. It stores
canonical float/mask bytes rather than caller DataFrames, so mutations to input
or returned frames do not alter frozen content. It has no intersection helper
and requires exact snapshot axes.

The current repository environment and declared dependencies contain no
Parquet/Arrow engine. The object is therefore explicitly
`fixture_only_partition_artifact_unavailable` and `decision_grade=false`.
No pickle, NPY or full-panel JSON fallback was introduced. P0-09 and P1-03
remain open until the M2 partitioned producer writes and reopens formal refs;
P1-02 remains partial until the production lifecycle consumes the snapshot.

Current verification:

- executable grammar plus frozen output adversarial matrix: `29 passed`;
- full Alpha Foundry plus Alpha Quality: `444 passed in 67.94s`;
- Research Ledger, contracts, security and acceptance:
  `200 passed in 22.79s`, with 21 existing deprecation warnings;
- three-source cold mypy: passed.
