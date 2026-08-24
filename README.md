# Vibe-Trading AGS Agent Engineering Governance Bundle

This bundle contains a repository-root `AGENTS.md`, a non-normative authoring template, and eight stage-specific `ExecPlan.md` files for transforming Vibe-Trading AGS into an evaluation-driven, failure-closed flagship Agent system.

## Recommended repository placement

Copy the files into the repository without changing the relative paths:

    Vibe-Trading-AGS/
    ├── AGENTS.md
    └── .agent/
        ├── templates/
        │   └── ExecPlan.template.md
        └── execplans/
            ├── 00-governance-and-baseline/ExecPlan.md
            ├── 01-trace-contract-v2/ExecPlan.md
            ├── 02-runtime-instrumentation/ExecPlan.md
            ├── 03-offline-eval-harness/ExecPlan.md
            ├── 04-failure-recovery-and-chaos/ExecPlan.md
            ├── 05-regression-gates-and-ci/ExecPlan.md
            ├── 06-production-failure-loop/ExecPlan.md
            └── 07-flagship-hardening-and-release/ExecPlan.md

## Authority model

`AGENTS.md` is the sole normative process contract. Each stage plan is task-specific and may narrow implementation choices but may not weaken repository invariants. The template is only a convenience copy and is deliberately non-normative, preventing three conflicting sources of truth.

## Execution order

Start with stage 00. A later plan may be researched, but it must not enter `ACTIVE` implementation state until its dependency gate is satisfied. Every plan must be maintained as a living document and may be marked `COMPLETE` only with commit-bound acceptance evidence.

## Important spelling

The standard term is **ExecPlan**, not `ExecuPlan`. Each phase keeps the exact file name `ExecPlan.md` so tooling can locate it consistently.
