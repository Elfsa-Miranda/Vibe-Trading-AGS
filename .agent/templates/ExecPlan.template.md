# <Action-oriented stage title>

**Plan-ID:** <AGS-AR-XX>  
**Status:** PROPOSED  
**Stage:** <NN>  
**Owner:** <name or agent identity>  
**Created:** <YYYY-MM-DD>  
**Last-Updated:** <YYYY-MM-DD HH:MMZ>  
**Base-Ref:** <branch or tag>  
**Base-SHA:** <full commit SHA>  
**Depends-On:** <plan IDs or NONE>  
**Supersedes:** NONE  
**Target-Outcome:** <one sentence>

This ExecPlan is a self-contained living specification governed by the repository-root `AGENTS.md`. `Progress`, `Surprises & Discoveries`, `Decision Log`, `Outcomes & Retrospective`, and `Plan Revision Log` must remain current.

## Purpose / Big Picture

Explain what a user, developer, reviewer, or operator can do after this stage that cannot be done before, and give the shortest observable demonstration.

## Implementation Scope Contract

### Required behavior

List stable `REQ-XX-NN` requirements.

### Compatibility boundary

State released or durable interfaces that must remain compatible. State which unreleased interfaces may be replaced directly.

### Intentionally unsupported cases and failure behavior

Name unsupported cases and the exact typed failure or fail-closed behavior.

### In-scope files

Name exact paths or path families.

### Out-of-scope files and behavior

Name what must not change.

## Current System Evidence

Describe the current implementation with exact paths, functions, schemas, commands, and known limitations. Include evidence gathered from the recorded base SHA.

## Terminology

Define every non-obvious term in plain language.

## Dependencies and Prerequisite Gate

State required prior plans, packages, data, permissions, and the observable gate proving each dependency is ready.

## Invariants

- `INV-XX-01`: ...
- `INV-XX-02`: ...

Each invariant must map to a positive or negative test.

## Deliverables

- `DEL-XX-01`: ...
- `DEL-XX-02`: ...

Distinguish runtime deliverables, test deliverables, documentation, and evidence artifacts.

## Non-Goals and Prohibited Changes

- `NC-XX-01`: ...
- `NC-XX-02`: ...

State how accidental violation will be detected.

## Architecture and Data Flow

Describe ownership, call sequence, source-of-truth boundaries, and failure propagation. Include a simple indented diagram when useful.

## Interfaces and Schemas

Prescribe final modules, classes, functions, fields, enums, validation, versioning, and error behavior.

## Milestones

### Milestone 1 — <name>

Describe goal, exact work, observable result, and proof. Each milestone must be independently verifiable.

### Milestone 2 — <name>

Describe goal, exact work, observable result, and proof.

## Detailed Tasks

Name each file, symbol, edit, test, and evidence artifact. Do not use vague instructions such as “add support” or “improve tests.”

## Failure Semantics

Define error taxonomy, retryability, side-effect state, partial results, terminal states, and escalation.

## Security, Privacy, and Threat Model

Describe assets, trust boundaries, threat actors, abuse paths, redaction, retention, path safety, and authorization.

## Test Strategy

Define unit, contract, integration, end-to-end, adversarial, property, concurrency, regression, and statistical checks as applicable. State why each test can detect the intended defect.

## Validation and Acceptance

- `AC-XX-01` — Input/action, expected observable behavior, command, and evidence path.
- `AC-XX-02` — Input/action, expected observable behavior, command, and evidence path.

Use only `PASS`, `FAIL`, `BLOCKED`, or `INCONCLUSIVE`.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-XX-01 | `<path:symbol>` | TEST-XX-01 | EVID-XX-01 | PROPOSED |

## Concrete Execution Commands

State working directory, exact commands, expected exit code, and concise expected output.

## Idempotence, Rollback, and Recovery

Explain safe re-run, cleanup, schema rollback, feature disablement, and recovery from interruption.

## Observability and Evidence Artifacts

List artifact paths, formats, hashes, retention, and the authoritative source for each conclusion.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-XX-01 | ... | ... | ... | ... |

## Progress

- [ ] (<timestamp>) Plan researched and base evidence recorded.
- [ ] (<timestamp>) Milestone 1 complete with evidence.
- [ ] (<timestamp>) Milestone 2 complete with evidence.
- [ ] (<timestamp>) Final review and broad verification complete.

## Surprises & Discoveries

- Observation: None yet.
  Evidence: N/A.

## Decision Log

- Decision: <decision>
  Alternatives: <alternatives>
  Rationale: <why>
  Date/Author: <timestamp and identity>

## Outcomes & Retrospective

Not started. At completion, compare measured outcomes with the original purpose and state remaining gaps.

## Plan Revision Log

- <timestamp>: Initial plan created. Reason: <reason>.
