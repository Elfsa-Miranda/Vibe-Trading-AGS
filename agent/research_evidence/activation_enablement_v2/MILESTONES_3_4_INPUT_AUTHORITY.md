# Milestones 3–4 — Golden Slice and Production Run Inputs

Branch `codex/ags-v32-activation-inputs-v1` was created fresh from accepted
`codex/ags-v32-main@dc18b1fa37e6a1e758a880ac3987190a90817a4a`.

## Golden production slice

No production slice was opened or fabricated. The registered field-level audit
currently caps the local Tushare provider below strict authority. Consequently,
`ProductionGoldenSliceReadinessV1` deterministically records:

- `PROVIDER_FIELD_PIT_AUDIT_INSUFFICIENT`;
- `PRODUCTION_GOLDEN_SLICE_SNAPSHOT_UNAVAILABLE`;
- missing factor-output, production-evaluator, evaluator-DAG, and replay sources.

The readiness producer accepts only exact protected event hashes for an existing
PIT snapshot, `FactorOutputRecordedV3`, `EvaluationRecorded`, and
`TrialTerminalDossierRecorded`. It has no provider client, DataFrame, formula,
evaluator injection, or report input. A fake/injected client therefore cannot
obtain production authority through this path.

## Production run input boundary

`ProductionActivationRunInputBundleV1` binds the exact resolved contract,
strict provider authority decision, decision-grade train/valid PIT snapshot,
golden-slice readiness, split plan, existing generator/executable grammar,
existing serial/DAG evaluator factories, policies, common budgets, source
watermark, namespace, and canonical hash spec.

It cannot contain DataFrames, IC, scores, decisions, success counts, or
test/final/forward references. `ProductionActivationRunInputServiceV1` refuses
to register a bundle while the provider or golden slice is blocked. Thus the
historical Phase 11 input blocker is narrowed and typed but is not falsely
declared resolved.

## Files and focused gates

Touched files are limited to the run-input adapter, two additive protected event
schemas/registrations, corresponding event-closure tests, focused input tests,
and this record.

- input/golden-slice tests: 5 passed;
- event payload/capability closure tests: 127 passed;
- isolated mypy: passed;
- Ruff: passed;
- compileall and `git diff --check`: passed.

Affected full regressions and staged-merge verification are recorded in the
final outcome. The unavailable real provider evidence is not represented as a
passing golden slice.
