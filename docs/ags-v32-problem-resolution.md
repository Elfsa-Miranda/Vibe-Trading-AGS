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
| P0-01 | M1 split/timing | audit_reported | none |
| P0-02 | M1 evidence/Decision | audit_reported | none |
| P0-03 | M1 production evaluator | audit_reported | none |
| P0-04 | M1/M2 PIT snapshot | audit_reported | none |
| P0-05 | M2 A-share adapter | audit_reported | none |
| P0-06 | M2 stateful execution | audit_reported | none |
| P0-07 | M1 split registry | audit_reported | none |
| P0-08 | M1/M2 execution policy | audit_reported | none |
| P0-09 | M1 immutable output | audit_reported | none |
| P0-10 | M1 scorecard/final boundary | audit_reported | none |
| P0-11 | M1 split-safe scorecard | audit_reported | none |
| P0-12 | M1 final eligibility/provider | audit_reported | none |
| P0-13 | M1 falsification v2 | audit_reported | none |
| P0-14 | M1 weighting policy | audit_reported | none |
| P0-15 | M1 Decision invariants | audit_reported | none |
| P0-16 | M1 exact projection bundle | audit_reported | none |
| P0-17 | M1 atomic artifact writer | audit_reported | none |
| P0-18 | M1 forward authority/vintage | audit_reported | none |
| P1-01 | M1 production timing binding | audit_reported | none |
| P1-02 | M1 executable grammar | audit_reported | none |
| P1-03 | M2 partitioned artifacts | audit_reported | none |
| P1-04 | M1 scorecard v2 | audit_reported | none |
| P1-05 | M1 production search factory | audit_reported | none |
| P1-06 | M1 worker/resource evidence | audit_reported | none |
| P1-07 | M0 release baseline, then M1 report binding | partial | deterministic manifest and adversarial tests; event-bound GET/CLI refs remain open |
| P1-08 | after M1/M2 authority closure | audit_reported | active path remains fail-closed |
| P1-09 | M2 formal cache isolation | audit_reported | none |
| P1-10 | M1/M2 Complement policy | audit_reported | none |
| P1-11 | M1 regime producer | audit_reported | none |
| P1-12 | M1 execution/calendar policy | audit_reported | none |
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
