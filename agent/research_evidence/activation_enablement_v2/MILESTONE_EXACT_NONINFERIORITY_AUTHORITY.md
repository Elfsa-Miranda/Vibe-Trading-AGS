# Activation Enablement V2 - Exact Noninferiority Authority Milestone

## Local lineage and permitted scope

- Base: `codex/ags-v32-main` at
  `5e1ae9b8` (`merge: bind activation resource source authority`).
- Feature branch: `codex/ags-v32-activation-noninferiority-authority`.
- Changes are limited to the existing Statistical Protocol/Analyzer and their
  Activation tests. No remote operation occurred.
- No candidate metric, decision, scorecard, execution result, or historical
  outcome artifact was modified.

## Reused authority path

```text
RecordedActivationPairEvidenceV2
-> exact flat/topology ActivationArmEventRefsV2
-> same typed ResearchEventStore
-> TrialTerminated + ActivationResourceMeasuredV2
-> preregistered endpoint catalog and margins
-> ActivationStatisticalAnalysisV2 gates
-> existing ActivationGovernanceService
```

The analyzer no longer consumes the pair projection's legacy
`safety_noninferiority_pass` or `resource_noninferiority_pass` fields. Those
fields remain compatible with the existing v2 artifact schema but have no
decision authority.

The closed endpoint catalog is:

- safety: `duplicate_rate`, `failure_rate`;
- resource: `cpu_seconds`, `peak_rss_mb`, `wall_seconds`.

New protocol registrations use that full catalog. The previously valid v2
`peak_rss_mb`/`wall_seconds` resource catalog remains accepted for deterministic
replay of historical artifacts; it is not silently rewritten to add CPU.

Rates are rebuilt from exact `TrialTerminated` statuses using the frozen
candidate budget. Resource values are read only from the exact protected
`ActivationResourceMeasuredV2` ref for each arm. The analyzer rejects as
unavailable any missing or wrong event family, multiple/missing resource ref,
`source_complete != true`, boolean/non-numeric value, non-finite value, or
negative resource value. Unavailable evidence yields an unresolved gate, never
a pass.

The existing protocol margins remain frozen before outcome access; CPU is now
included at the existing 30-second resource margin so the attachment's
CPU/wall/RSS requirement is not partially implemented. Every pair must remain
within every registered endpoint margin for the corresponding closed gate to
pass. A false safety/resource/failure gate remains a non-compensatory hard
governance rejection.

## No parallel implementation

- No new ledger, event store, artifact repository, candidate evaluator,
  scorecard, execution engine, Claim Matrix, QualityDecision runner, parser, or
  identity service was added.
- No new event or artifact schema was introduced. The analyzer reuses the exact
  refs already protected by Pair Evidence v2 and Run Source v3.
- Governance still consumes only the registered protocol, recorded analyzer
  artifact, and readiness evidence; it does not recompute candidate evidence.

## Validation

- Statistical/governance focused: `41 passed`.
- All Activation-selected tests: `146 passed, 5835 deselected`, with five
  existing FastAPI/Starlette deprecation warnings.
- Research-event schema/capability/concurrency regression: `137 passed`.
- Alpha Foundry full: `369 passed, 1 failed`. The sole failure is the unchanged
  accepted-baseline release-manifest mismatch caused by the user-owned root
  `D:\Vibe-Trading\problem.md`; neither that file nor
  `agent/research_evidence/release_manifest.json` was modified.
- Ruff on changed sources/tests: passed.
- mypy with `--follow-imports=skip` on both changed sources: passed.
- compileall on changed sources/test: passed.
- `pip check`: `No broken requirements found.`
- `git diff --check`: passed with only expected LF/CRLF worktree notices.

## Remaining non-compensatory blockers

- No strict provider field-level PIT authority or authority-qualified golden
  production slice/train-valid bundle is available locally.
- Existing `ActivationResourceMeasuredV2` remains intentionally
  `source_complete=false`, with no isolated process-tree RSS and no enforced
  whole-arm timeout. Therefore exact resource NI remains unavailable in real
  execution and cannot be inferred from the infrastructure-only v3 probe.
- Readiness remains false; no pilot or confirmatory outcomes were opened.
- Official policy remains `flat_with_topology_shadow`; active research-only
  influence remains false; live-trading meaning remains none.
