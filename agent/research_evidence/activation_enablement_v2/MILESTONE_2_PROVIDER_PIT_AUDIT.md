# Milestone 2 — Provider Field-Level PIT Audit

## Scope

Branch `codex/ags-v32-provider-pit-audit-v1` was created fresh from accepted
`codex/ags-v32-main@e8f42fccd086920f6c528029fbd7443e6e1fe23e`.

Touched implementation files:

- `agent/src/alpha_foundry/activation/provider_pit_audit_v1.py`
- `agent/src/research_ledger/events/payloads.py`
- `agent/src/research_ledger/events/store.py`
- `agent/tests/alpha_foundry/test_provider_pit_audit_v1.py`
- event registry closure tests
- this audit record

No snapshot producer, PIT adapter, ledger, artifact writer, evaluator, scorecard,
execution engine, Claim Matrix, or QualityDecision implementation was copied or
replaced.

## Local Tushare conclusion

The audit catalog distinguishes reporting/event time, effective time, provider
availability time, and retrieval vintage. In particular, `end_date` is not
treated as announcement time, and `ann_date`, `f_ann_date`, `end_date`, and
`update_flag` have separate typed semantics.

The currently implemented Tushare path is not eligible for
`verified_strict` Activation authority:

- daily rows do not expose a per-row provider availability timestamp;
- financial-statement revision history and `update_flag` chains are not consumed
  or independently replay-audited by the current adapter;
- cross-interface consistency is not independently verified;
- provider rate-limit/retry behavior does not yet prove a complete closed batch;
- local implementation review and adapter registration hashes are not
  authentication or independent historical-as-of evidence.

The resulting authority decision must therefore remain `blocked`, with a
`best_effort` or `unavailable` claim ceiling by field. The implementation cannot
upgrade this result from adapter name, registry authority, or a configuration
boolean.

Evidence still required for strict authority includes receipt-bound provider
availability timestamps, raw announcement partitions, revision-complete
financial statement vintages, independent cross-interface samples, and a
rate-limit/retry completeness manifest.

## Gates executed on the feature worktree

- `python -m pytest tests/alpha_foundry/test_provider_pit_audit_v1.py -q` — 7 passed.
- `python -m pytest tests/research_ledger/test_event_capability_closure.py tests/research_ledger/test_research_events.py -q` — 125 passed.
- `python -m pytest tests/alpha_quality/test_pit_adapter_artifact_v2.py tests/alpha_quality/test_pit_service_v2.py tests/research_ledger -q` — 186 passed.

Static checks and staged-merge verification are recorded in the final task
outcome; unexecuted checks are never represented as passing.
