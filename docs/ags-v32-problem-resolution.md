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
| P0-01 | M1 split/timing | partial | fixture-only train/valid capability enforces outcome-contained split dates and rejects test/gap/intraday axes; production evaluator migration remains open |
| P0-02 | M1 evidence/Decision | partial | source-bound Decision v3 caps caller-constructable v2 evidence at research_only; the first producer-scoped v3 ledger mint now reopens exact event/artifact sources, while scorecard/execution/snapshot/complement mints and the narrow Decision consumer remain open |
| P0-03 | M1 production evaluator | audit_reported | none |
| P0-04 | M1/M2 PIT snapshot | audit_reported | none |
| P0-05 | M2 A-share adapter | audit_reported | none |
| P0-06 | M2 stateful execution | partial | strict actual-holdings/trades kernel exists; A-share fills/T+1/unavailable-holding producer remains open |
| P0-07 | M1 split registry | partial | producer-scoped registration now freezes deep-immutable calendar/timing/split content as the first event in a run; evaluator consumption and provider-authenticated trading-calendar provenance remain open |
| P0-08 | M1/M2 execution policy | partial | closed cost policy and strict missing-return/initial-exit cost kernel pass; production fill authority remains open |
| P0-09 | M1 immutable output | partial | exact-axis immutable byte content and pre-consumption byte/hash verification pass; formal Parquet/Arrow refs and producer-bound evaluator consumption remain open |
| P0-10 | M1 scorecard/final boundary | partial | fixture scorecard exposes train/valid only and contains no test/execution artifact; one-shot final producer remains open |
| P0-11 | M1 split-safe scorecard | partial | predictive and daily PIT-universe coverage artifacts are explicitly train/valid scoped; execution is absent and requires a separate producer; producer event lineage remains open |
| P0-12 | M1 final eligibility/provider | audit_reported | none |
| P0-13 | M1 falsification v2 | audit_reported | none |
| P0-14 | M1 weighting policy | partial | permutation-invariant tie-neutral v2 weighting passes; legacy scorecard migration remains open |
| P0-15 | M1 Decision invariants | audit_reported | none |
| P0-16 | M1 exact projection bundle | verified | DAG/factual/episodic bind one store-verified historical subsequence and three frozen projector policies; replace/forgery and pre-append replay matrix passes |
| P0-17 | M1 atomic artifact writer | partial | canonical create-if-absent writer passes collision/concurrency tests; legacy writers and event transitions remain unmigrated |
| P0-18 | M1 forward authority/vintage | audit_reported | none |
| P1-01 | M1 production timing binding | audit_reported | none |
| P1-02 | M1 executable grammar | partial | runtime grammar/backend intersection and typed pre-compute skip pass; production lifecycle integration remains open |
| P1-03 | M2 partitioned artifacts | audit_reported | none |
| P1-04 | M1 scorecard v2 | partial | additive v2 fixture object binds predictive/coverage input hashes and is explicitly non-decision-grade; exclusive ProductionCandidateEvaluator mint remains open |
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

## M1 weighting/execution-primitives evidence

Branch `codex/ags-v32-m1-execution-primitives` was created from accepted main
`a8de0d0b2cab9e1157fea501bba7b5c368bcc950`.

`WeightingPolicyV2` separates quantile and continuous-rank methods. Quantile
membership consumes whole equal-value groups and never splits a tie by ticker
or column order; constant factors and unresolved boundary ties produce typed
no-trade results. Long and short sets are disjoint and gross/net exposure is
recorded.

`ExecutionCostPolicyV1` is a closed finite non-negative policy with separate
commission, sell stamp duty and buy/sell slippage. The strict v2 kernel starts
from zero holdings, requires filled trades to reconcile to actual holdings,
charges initial and terminal trades, rejects non-flat terminal evidence, and
returns NaN/unavailable whenever any non-zero holding lacks its required
return. It never sums only the priced remainder of a partially missing
portfolio.

These primitives accept actual holdings/fills as inputs and therefore are not
producer authority by themselves. They cannot mint execution evidence or a
Decision record. A-share can-buy/can-sell, T+1 sellable quantity, delisting,
unavailable holdings and PIT state transitions remain M2 requirements; legacy
scorecard functions remain audit/feature-off compatibility paths until the
production evaluator migration.

Current verification:

- weighting/execution adversarial matrix: `13 passed`;
- full Alpha Quality: `187 passed in 16.63s`;
- Alpha Foundry, Research Ledger, contracts, security and acceptance:
  `470 passed in 66.17s`, with 21 existing deprecation warnings;
- one-source cold mypy: passed.

## M1 train/valid capability and scorecard-v2 evidence

Branch `codex/ags-v32-m1-scorecard-v2` was created fresh from accepted main
`5716f2dc6b4ed5a68499f025c46972b63a48e58e`.

`FixtureTrainValidDataCapabilityV1` retains only the train and validation
calendar slices and close bytes. It rejects full panels containing test or gap
dates, missing/reordered axes, intraday timestamps, unknown runtime split
values, Infinity and non-positive available prices. Its outcome lookup accepts
only outcome-contained train/valid signal dates under the frozen timing policy.
It is explicitly fixture-only and cannot be Decision evidence.

`AlphaQualityScorecardV2` computes predictive and coverage artifacts separately
for train and valid. Coverage uses each date's point-in-time universe count and
reports an empty universe as unavailable. The artifact contains no test metric
or execution metric; execution has the typed status `separate_producer_required`.
Factor and close bytes are rehashed immediately before consumption, axes and
snapshot/split/timing hashes must match exactly, and the scorecard has no raw
panel, caller score, warning, Decision or verdict argument.

These changes close neither producer authority nor PIT market-data authority.
The capability and scorecard remain fixture-only until the unique
`ProductionCandidateEvaluator`, producer-scoped event mint, formal partitioned
artifacts and replay validation are implemented. P0-01/P0-09/P0-10/P0-11 and
P1-04 therefore remain partial.

Current verification after all boundary hardening:

- frozen-output plus scorecard-v2 focused matrix: `22 passed`;
- full Alpha Quality: `201 passed in 21.90s`;
- Alpha Foundry plus Research Ledger: `381 passed in 62.85s`;
- contracts, security and acceptance: `89 passed in 9.31s`, with 21 existing
  deprecation warnings;
- two-source cold mypy and isolated-prefix compileall: passed.

A cold-cache run exposed that the pre-existing no-dynamic-execution test kept
the global `compile` replacement active during its pandas assertion. The test
now warms the exact third-party rank/delta primitives before the guard and
restores the runtime immediately after the complete DSL evaluation. The same
test passes with a fresh isolated bytecode prefix, so the guard measures DSL
execution rather than pandas/pytest lazy imports.

## M1 exact discovery-projection authority evidence

Branch `codex/ags-v32-m1-exact-projection-bundle` was created fresh from
accepted main `ce464f804eb0010d95d3264dab383ab28c7bd707`.

The production `DiscoveryEvidenceProjector` now issues discovery evidence from
one store-authorized `VerifiedEventSubsequence` at the last eligible discovery
watermark. Later final/forward monitoring events are outside both the event set
and the historical prefix provenance, so monitoring append operations cannot
change the discovery bundle.

Factor DAG, factual memory and episodic memory each carry the same
`source_subsequence_hash`, their own frozen `projector_policy_hash`, and a
canonical content/projection hash. Their authorized constructors are
`init=False`; `dataclasses.replace` cannot carry authority into modified
content. `DiscoveryEvidenceView` is the non-replaceable exact bundle and
rebuilds all three components from its original verified subsequence before
every Retriever score. Mutating a posterior, DAG or stored projection hash,
including via `object.__setattr__`, fails exact replay before selection.

The production `with_episodic_projection` mutation surface was removed. Pure
scoring tests that need synthetic observations now use a tests-only fixture
whose object deliberately has no runtime projection authority. Retriever v7
event append continues to rebuild the current historical projection and its
upstream scorecards before the event can enter the ledger.

Acceptance evidence:

- exact projection, DAG and process-memory focused matrix: `33 passed`;
- full Alpha Foundry: `273 passed in 50.90s`;
- full Alpha Quality: `201 passed in 23.78s`;
- Research Ledger: `111 passed in 18.01s`;
- contracts, security and acceptance: `89 passed in 10.20s`, with 21 existing
  deprecation warnings;
- eight-source cold mypy: passed.

This closes P0-16's projection-authority defect only. It does not establish
producer-bound Decision evidence, PIT data authority, Retriever efficacy or an
Activation outcome; those gates remain closed.

## M1 legacy Decision-evidence authority gate

Branch `codex/ags-v32-m1-producer-bound-decision-evidence` was created fresh
from accepted main `7ba2d5525c8603e8509bd5d5732dc80a79b2c3cb`.

`QualityDecisionV2Runner` remains unchanged as the feature-off/audit
compatibility baseline. The source-bound `QualityDecisionV3Service` and the
event store's append-time deterministic rebuild now both pass that legacy
result through `QualityDecisionAuthorityGateV1`. Because every embedded
`DecisionEvidenceRecord.v2` can be created by a caller, any non-reject result
is capped at `research_only` with
`LEGACY_CALLER_CONSTRUCTABLE_EVIDENCE` and
`PRODUCER_BOUND_DECISION_EVIDENCE_REQUIRED`. Hard rejection remains fail-safe.

An all-pass, self-authored evidence bundle can therefore no longer mint a
`candidate_zoo` `QualityDecisionV3Recorded` event. Rehashing or changing the
event does not bypass the cap because append-time validation reopens the input
bundle and reruns the same authority gate.

This is a fail-closed prerequisite, not full P0-02 closure. No v3
producer-scoped scorecard, execution, snapshot, complement or ledger evidence
mint exists yet, so no path can legitimately exceed `research_only`.

Current verification:

- Decision v2 compatibility plus source-bound authority gate: `30 passed`;
- full Alpha Quality: `202 passed in 29.21s`;
- full Alpha Foundry: `273 passed in 55.06s`;
- Research Ledger: `111 passed in 22.92s`;
- contracts, security and acceptance: `89 passed in 11.08s`, with 21 existing
  deprecation warnings.

## M1 producer-scoped ledger Decision evidence v3

Branch `codex/ags-v32-fix-producer-scoped-evidence-v3` was created fresh from
accepted main `943aa52f1136fb9c70b6b13d4c115195bf56ccad`.

The first additive `DecisionEvidenceRecord.v3` kind is now minted only by
`DecisionLedgerEvidenceServiceV3`. Its public entry point accepts a factor and
run identity, not caller-authored evidence payloads or selected terminal event
hashes. Within a frozen ledger watermark, the producer requires exactly one
eligible train/valid terminal for that factor/run and rejects ambiguous runs;
this prevents a caller from cherry-picking one of multiple terminal outcomes.

The resulting closed record binds the producer schema and policy, exact source
event hashes, source artifact blob hashes, run completeness, durability and
infrastructure-failure evidence. A protected `DecisionEvidenceV3Recorded`
event cannot be appended through the generic event API. Its producer append,
chain replay and idempotent read path reopen the evidence artifact, rebuild the
record from the historical event prefix, and verify source artifact paths and
bytes. An intervening write invalidates a pre-append watermark; later source
events in the same run make an existing evidence record stale rather than
silently returning it.

Feature-off construction fails before the evidence artifact store is created,
and the v2 audit path remains capped at `research_only`.

This remains partial P0-02 progress. Ledger evidence alone cannot authorize a
higher tier. Producer-scoped scorecard, execution, snapshot and complement
mints, followed by a Decision service that accepts only their narrow verified
view, are still required. It is not Activation evidence and does not unlock a
formal retriever experiment.

Accepted branch verification:

- focused evidence, payload-registry and capability-closure matrix:
  `88 passed in 9.15s`;
- full Alpha Quality: `212 passed in 43.33s`;
- full Alpha Foundry: `273 passed in 64.04s`;
- Research Ledger: `112 passed in 16.41s`;
- contracts, security and acceptance: `89 passed in 10.95s`, with 21 existing
  deprecation warnings and the Windows symlink test executed under a capable
  token;
- four-source mypy, compileall and staged diff check: passed.

## M1 producer-scoped evaluation policy registry v1

Branch `codex/ags-v32-fix-evaluation-policy-registry-v1` was created fresh
from accepted main `0fc5b048833c79a917a075cfca4915d0ab31c6ca`.

`EvaluationPolicyRegistryServiceV1` accepts only the pre-outcome calendar dates,
timing parameters and requested train/valid/test bounds. It derives every hash,
the execution-lag/max-horizon purge and embargo, and the content-addressed
artifact internally. The entry point has no caller hash, `FrozenSplitPlan`,
purge or embargo override channel.

The closed `EvaluationPolicyRegistered` event is producer-scoped and must be
the first event for its run. It records the exact prior global chain watermark,
so an intervening append fails transactionally. Append, replay and idempotent
read reopen the artifact and reconstruct the calendar, timing policy and split
plan. Overlap, reversal, insufficient derived embargo, late registration,
same-run replacement, artifact or database-payload tampering, duplicate JSON
keys and feature-off construction fail closed. The in-memory bundle is
recursively immutable rather than only a frozen outer dataclass.

This is partial P0-07 closure, not market-data authority. The registered date
content is content-addressed but is not yet authenticated by the P0-04/P0-05
PIT adapter. A later scorecard/evaluator may cite this event, but it must cap
the result while calendar/PIT provenance is unverified. No Activation outcome
was accessed.

Branch verification:

- dedicated registry adversarial tests: `14 passed`;
- staged payload/capability closure plus dedicated matrix:
  `93 passed in 12.72s`;
- full Alpha Quality: `226 passed in 45.44s`;
- full Alpha Foundry: `273 passed in 84.33s`;
- Research Ledger: `113 passed in 29.68s`;
- contracts, security and acceptance: `89 passed in 15.77s`, with 21 existing
  deprecation warnings;
- three-source mypy: passed.
