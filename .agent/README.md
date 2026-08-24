# Agent Reliability execution plans

This directory contains the staged execution system governed by the repository-root `AGENTS.md`.

## Authority

- `AGENTS.md` is normative and defines the non-waivable engineering, safety, evidence, review, and completion rules.
- Each `execplans/<stage>/ExecPlan.md` is self-contained and owns that stage’s implementation decisions, deliverables, prohibitions, and acceptance evidence.
- `templates/ExecPlan.template.md` is only an authoring aid. It is not a third source of truth.

## Stage order

| Stage | Plan | Outcome |
|---:|---|---|
| 00 | `00-governance-and-baseline` | Machine-enforced plan schema and truthful repository baseline |
| 01 | `01-trace-contract-v2` | Versioned hierarchical trace/span contract |
| 02 | `02-runtime-instrumentation` | Behavior-preserving Agent runtime instrumentation |
| 03 | `03-offline-eval-harness` | Source-bound offline Agent evaluation |
| 04 | `04-failure-recovery-and-chaos` | Typed failures, safe recovery, deterministic fault injection |
| 05 | `05-regression-gates-and-ci` | PR/nightly/release evaluation gates |
| 06 | `06-production-failure-loop` | Failure-to-regression-to-recurrence closed loop |
| 07 | `07-flagship-hardening-and-release` | Reproducible flagship evidence and release hardening |

## Activation procedure

The program runs in a dedicated integration worktree/branch (`codex/agent-reliability-main`). Each stage runs in its own fresh stage worktree/branch created from the current integration head, and is merged locally back only after full acceptance.

Only one implementation plan may be `ACTIVE` in a given stage worktree.

1. Confirm the dedicated integration worktree exists and the governance ZIP has been extracted and verified there.
2. Confirm every dependency plan is `COMPLETE`.
2. Replace the plan’s placeholder `Base-SHA` with the exact `git rev-parse HEAD` value.
3. Record `git status --short --branch` and the accepted baseline evidence.
4. Change the plan status from `PROPOSED` to `ACTIVE`.
5. Execute milestones continuously; keep all living sections current.
6. Do not mark the plan `COMPLETE` until every mandatory acceptance criterion is `PASS` and its evidence is bound to the final commit.

A later plan may be researched while an earlier plan is active, but it may not implement or claim prerequisite behavior through stubs, mocks, copied fixtures, or prose.

## Plan state semantics

- `PROPOSED`: Decision-complete design; implementation not started.
- `ACTIVE`: Prerequisites met; implementation in progress.
- `BLOCKED`: Named external condition or prerequisite prevents progress.
- `COMPLETE`: All mandatory acceptance evidence passes.
- `SUPERSEDED`: Replaced by a named later plan; retained for auditability.

## Evidence rule

The minimum valid proof chain is:

    Requirement
      -> production implementation
      -> defect-detecting test
      -> reopened evidence artifact
      -> acceptance decision bound to final SHA

A missing link is `INCONCLUSIVE`, never `PASS`.
