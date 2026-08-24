# Design rationale: how `AGENTS.md` and `ExecPlan.md` cooperate

## The central design choice

This bundle deliberately uses two authoritative layers rather than three:

1. `AGENTS.md` is the durable repository constitution.
2. Each stage’s `ExecPlan.md` is the self-contained implementation contract.

The template and `.agent/README.md` are non-normative. This prevents a common failure mode in which `AGENTS.md`, `PLANS.md`, a template, and the active plan all contain slightly different mandatory rules.

## What belongs in `AGENTS.md`

Only rules that should remain true across every substantial stage:

- instruction precedence and safety boundaries;
- when a plan is mandatory;
- required plan sections and identifier conventions;
- four-state acceptance semantics;
- anti-shortcut and anti-false-proof rules;
- review, test, git, security, and completion protocols;
- the dependency order of the overall program.

This file should be stable and relatively small. The generated version is under the default 32 KiB Codex project-instruction budget.

## What belongs in each `ExecPlan.md`

Everything specific to one implementation stage:

- exact required behavior and compatibility boundary;
- current code evidence and affected paths;
- final interfaces/schemas;
- required deliverables;
- prohibited changes and how violations are detected;
- milestone sequence and file-level tasks;
- failure semantics, threat model, test strategy;
- observable acceptance criteria;
- requirement-to-code-to-test-to-evidence traceability;
- commands, rollback, risks, progress, discoveries, decisions, and retrospective.

A plan is written so that a new implementer can continue from it without inventing design decisions.

## Why prose alone is insufficient

“Test thoroughly” and “do not take shortcuts” are not enforceable. The bundle turns them into mechanical obligations:

- stable `REQ`, `INV`, `NC`, `AC`, `TEST`, and `EVID` identifiers;
- a mandatory traceability matrix;
- exact `PASS`, `FAIL`, `BLOCKED`, and `INCONCLUSIVE` states;
- explicit anti-cheat constraints;
- commit-bound evidence;
- Stage 00 tooling that validates the plans themselves;
- later CI gates that reject missing, stale, tampered, or statistically inconclusive evidence.

## Why stages are separated

The dependency order prevents circular proof:

- tracing exists before trajectory evaluation;
- evaluation exists before claiming recovery;
- typed recovery exists before chaos metrics;
- source-complete metrics exist before CI promotion gates;
- protected gates exist before closing production-derived failures;
- all evidence paths exist before public flagship claims.

A later stage may not manufacture an earlier capability through test doubles. This is the main defense against superficially green but architecturally false delivery.

## Completion philosophy

A stage is not complete because code exists or many tests passed. It is complete only when the intended behavior is observable through its real path, the failure path is tested, the evidence is reopened and verified, the complete diff is reviewed, and every mandatory acceptance row is bound to the final commit.
