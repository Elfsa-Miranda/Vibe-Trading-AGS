# Vibe-Trading AGS Engineering Agent Contract

**Status:** Normative repository-wide instruction  
**Scope:** The entire repository unless a nearer `AGENTS.md` adds stricter path-specific rules  
**Primary objective:** Deliver observable, reproducible, reviewable working behavior without weakening the existing research-only, evidence-governed, fail-closed safety model  
**Plan convention:** Every substantial change is governed by exactly one active stage `ExecPlan.md`

This file is the repository-level engineering constitution for human and AI contributors. It defines non-waivable safety rules, the execution-plan protocol, evidence requirements, review gates, and the meaning of “complete.” A stage `ExecPlan.md` defines the task-specific scope and design choices, but it may not weaken this file.

## 1. Instruction authority and conflict handling

Apply instructions in this order:

1. Explicit instructions from the current user or maintainer.
2. Safety, legal, security, and external-service authorization boundaries.
3. The nearest path-specific `AGENTS.override.md` or `AGENTS.md`.
4. This root `AGENTS.md`.
5. The active stage `ExecPlan.md`.
6. Other repository documentation.

A lower item may clarify or narrow a higher item, but may not weaken a non-waivable rule in this file. When two instructions conflict, follow the stricter safety or verification requirement and record the conflict in the active plan’s `Decision Log`. Do not silently choose the easier interpretation.

`AGENT_CONTRIBUTOR_GUIDE.md`, `CONTRIBUTING.md`, `SECURITY.md`, the pull-request template, and architecture documents remain relevant background. This `AGENTS.md` is the authoritative agent-facing workflow contract. Critical rules must be present here rather than delegated only by link.

## 2. Repository mission and permanent boundaries

Vibe-Trading AGS is a research and evaluation system. It may evaluate agents, factor research, evidence production, recovery behavior, and software reliability. It is not authorized by this contract to place live orders, move money, alter broker state, publish releases, deploy externally reachable services, rotate credentials, or modify external systems.

Permanent boundaries:

- Preserve the distinction between the **Evidence Plane**, **Telemetry Plane**, and **Evaluation Plane**.
- Research events and content-addressed artifacts remain the authoritative evidence source for research claims.
- Traces and spans describe runtime behavior; they must not silently become a second authority for research conclusions.
- Evaluators may grade outputs and trajectories; callers may not submit their own authoritative score, warning, verdict, or pass state.
- Live trading, broker-write, wallet, payment, or destructive external actions are forbidden in routine development and validation.
- Safety-critical routes remain fail-closed.
- Missing, corrupt, stale, unavailable, skipped, or inconclusive evidence never becomes a pass.
- Do not persist private chain-of-thought. Record decisions, selected actions, tool inputs after redaction, outputs after redaction, constraints, and evidence references instead.

## 3. Mandatory startup protocol

Before editing files for a substantial task:

1. Read this file completely.
2. Locate the active stage plan under `.agent/execplans/<stage>/ExecPlan.md`.
3. Read the active plan completely, including its current `Progress`, `Decision Log`, and unresolved risks.
4. Inspect the working tree and branch with:

       git status --short --branch
       git diff --check

5. Identify the exact base commit and record it in the plan. Do not rely on a branch name alone.
6. Inspect every file named by the plan before changing it.
7. Classify the requested work as one of:
   - `PLAN_ONLY`
   - `IMPLEMENT`
   - `REVIEW`
   - `VERIFY`
   - `DOC_ONLY`
8. For `IMPLEMENT`, write or confirm the plan’s **Implementation Scope Contract** before modifying runtime code.
9. Confirm that prerequisite stages are complete. If not, mark the plan `BLOCKED`; do not emulate missing prerequisites with stubs.
10. Record pre-existing failures separately from failures caused by the change.

Do not ask for generic “next steps” when the active plan already determines them. Continue to the next milestone unless a concrete decision, permission, credential, external service, or destructive action is required.

## 4. When an ExecPlan is mandatory

An ExecPlan is mandatory when any of the following is true:

- The work spans multiple files or modules.
- Runtime behavior, public APIs, persisted schemas, trace formats, event formats, tool contracts, CI gates, security boundaries, or user-visible behavior change.
- The work introduces a new feature, refactor, migration, compatibility layer, benchmark, fault model, or dependency.
- The task is expected to take more than roughly one hour.
- A change affects `agent/src/agent/loop.py`, `agent/src/agent/trace.py`, `agent/src/agent/tools.py`, `agent/src/agent/skills.py`, `agent/src/research_ledger/`, `agent/src/alpha_foundry/`, order/broker safety, evaluation gates, or GitHub Actions.
- A reviewer would need design context that is not obvious from the diff.

A trivial typo or narrowly scoped editorial correction may skip an ExecPlan. If a substantial task skips one, the final handoff must state why.

Each worktree has at most one active implementation ExecPlan. Parallel plans require separate worktrees and explicit authorization. Do not let two plans modify the same source-of-truth module concurrently.

## 5. ExecPlan identity and lifecycle

Store plans at:

    .agent/execplans/<NN>-<short-slug>/ExecPlan.md

Every plan begins with these fields:

- `Plan-ID`
- `Status`: `PROPOSED`, `ACTIVE`, `BLOCKED`, `COMPLETE`, or `SUPERSEDED`
- `Stage`
- `Owner`
- `Created`
- `Last-Updated`
- `Base-Ref`
- `Base-SHA`
- `Depends-On`
- `Supersedes`
- `Target-Outcome`

Lifecycle rules:

- `PROPOSED`: researched and decision-complete, but implementation has not started.
- `ACTIVE`: prerequisites are satisfied and implementation is in progress.
- `BLOCKED`: a named external condition or prerequisite prevents progress.
- `COMPLETE`: every mandatory acceptance criterion has passing evidence and no open blocking risk.
- `SUPERSEDED`: another named plan replaced it; preserve the old plan for auditability.

A plan may move to `COMPLETE` only after its acceptance matrix, evidence index, living sections, and final review are updated. “Code written,” “tests mostly pass,” or “CI should pass” are not completion states.

## 6. Required sections in every ExecPlan

Every stage plan must be self-contained and include all of the following:

1. **Purpose / Big Picture** — what becomes possible and how a human observes it.
2. **Status and Metadata** — the identity fields above.
3. **Implementation Scope Contract** — required behavior, compatibility boundary, intentionally unsupported cases, failure behavior, and in-scope files.
4. **Current System Evidence** — exact current paths, functions, commands, and known limitations.
5. **Terminology** — plain-language definitions for project-specific terms.
6. **Dependencies and Prerequisite Gate** — prior plans, packages, data, permissions, and conditions.
7. **Invariants** — properties that must remain true before and after the change.
8. **Deliverables** — concrete runtime, test, documentation, and evidence outputs.
9. **Non-Goals and Prohibited Changes** — what this stage must not implement.
10. **Architecture and Data Flow** — components, ownership, source-of-truth boundaries, and call sequence.
11. **Interfaces and Schemas** — required classes, functions, fields, enums, validation, and versioning.
12. **Milestones** — independently verifiable slices, each with goal, work, result, and proof.
13. **Detailed Tasks** — file-level edits and test additions.
14. **Failure Semantics** — typed errors, retry rules, partial-state handling, and terminal states.
15. **Security, Privacy, and Threat Model** — assets, trust boundaries, abuse paths, redaction, retention, and authorization.
16. **Test Strategy** — unit, contract, integration, end-to-end, adversarial, property, concurrency, and regression coverage as applicable.
17. **Validation and Acceptance** — behavioral criteria with exact commands and expected evidence.
18. **Requirement Traceability Matrix** — requirement to code to test to evidence.
19. **Concrete Execution Commands** — working directory, command, and expected result.
20. **Idempotence, Rollback, and Recovery** — safe re-run, revert, migration, and cleanup behavior.
21. **Observability and Evidence Artifacts** — logs, manifests, reports, traces, and retention.
22. **Risks and Mitigations** — likelihood, impact, detection, and mitigation.
23. **Progress** — timestamped checkbox list reflecting reality.
24. **Surprises & Discoveries** — unexpected findings with evidence.
25. **Decision Log** — decision, alternatives, rationale, date, and author.
26. **Outcomes & Retrospective** — achieved outcome, remaining gaps, and lessons.
27. **Plan Revision Log** — what changed in the plan and why.

A template may assist authoring, but this file is the normative schema. Do not treat the template as a separate authority.

## 7. Requirement and evidence identifiers

Use stable identifiers:

- `REQ-<stage>-NN`: required behavior
- `INV-<stage>-NN`: invariant
- `DEL-<stage>-NN`: deliverable
- `NC-<stage>-NN`: prohibited change or non-goal
- `RISK-<stage>-NN`: risk
- `AC-<stage>-NN`: acceptance criterion
- `TEST-<stage>-NN`: named validation
- `EVID-<stage>-NN`: evidence artifact

Every `REQ` must map to at least one implementation location, one `TEST`, and one `EVID`. Every `INV` must have a positive or negative test. Every `NC` that could be accidentally violated must have a static check, negative test, diff review rule, or explicit human review item.

The traceability matrix is mandatory. A criterion without evidence is `INCONCLUSIVE`, not `PASS`.

## 8. Acceptance states

Use only these states:

- `PASS`: The named command ran on the recorded commit and produced the expected observable result.
- `FAIL`: The observed result violated the criterion.
- `BLOCKED`: A named external condition prevented execution; include the condition and owner.
- `INCONCLUSIVE`: Evidence exists but is insufficient, ambiguous, flaky, statistically underpowered, or not source-complete.

Rules:

- `skipped`, `xfail`, `neutral`, `not run`, fixture-only, mocked-only, or manually asserted results do not automatically count as `PASS`.
- A flaky pass is `INCONCLUSIVE` until the source of flakiness is resolved or bounded by a documented statistical protocol.
- A test count alone is not evidence that the required behavior was tested.
- For new tests, demonstrate that the test fails against the pre-change behavior when practical and passes after the change.
- For statistical comparisons, record the estimator, repetitions, confidence interval, practical non-inferiority margin, seeds, model/version, dataset hash, and raw results.
- Hard safety invariants use zero-tolerance gates; averages cannot compensate for a safety violation.

## 9. Anti-shortcut and anti-false-proof rules

The following are prohibited for satisfying a stage:

- Adding `pass`, `TODO`, `FIXME`, `NotImplementedError`, empty handlers, placeholder returns, fake reports, or dead code in a required path.
- Claiming production behavior from fixture-only, toy-only, mock-only, or unit-only evidence when the plan requires an integrated path.
- Replacing the implementation under test with a test double at the exact boundary being claimed.
- Disabling, deleting, weakening, skipping, marking `xfail`, or narrowing existing tests to obtain a green run without a documented root cause and approved scope change.
- Lowering coverage, quality, latency, reliability, security, or evaluation thresholds merely to make a candidate pass.
- Updating golden files, snapshots, expected outputs, baselines, or grader scores only to match candidate output without an independent correctness argument.
- Catching broad exceptions and converting them to success, empty output, or an untyped warning.
- Introducing hidden bypass flags, undocumented environment variables, test-only production branches, or caller-controlled verdicts.
- Duplicating a source of truth for events, schemas, metrics, scores, warnings, decisions, or artifacts.
- Using generated documentation, comments, type declarations, or test fixtures as proof that runtime behavior exists.
- Fabricating command output, benchmark results, timestamps, commit SHAs, trace IDs, or evidence paths.
- Ignoring failed or abandoned attempts; record them in the plan or failure ledger.
- Expanding scope with unrelated cleanup, broad formatting, speculative abstractions, or compatibility layers.
- Adding a production dependency without a plan decision that records need, alternatives, security implications, and lockfile impact.
- Persisting raw secrets, credentials, private financial exports, unredacted prompts/tool outputs, or private chain-of-thought.
- Performing external writes, pushes, merges, releases, deployments, live trading, broker actions, or account changes without explicit authorization.

If a requested implementation cannot meet a requirement without violating one of these rules, mark the criterion `BLOCKED` and explain the exact conflict.

## 10. Scope discipline and complexity reset

Implement the narrowest architecture that satisfies the plan.

Trigger a mandatory complexity reset when any of these occurs:

- The diff spreads into two or more modules not declared in the scope contract.
- A second compatibility branch is proposed for an unreleased interface.
- A second reviewer finding adds another condition, resolver path, dispatch mode, or test permutation to the same abstraction.
- New metadata is duplicated across layers.
- A new source of truth appears.
- A simple requirement produces a combinatorial test matrix.
- The implementation needs a hidden bypass or caller override.
- The plan’s acceptance cannot be expressed as observable behavior.

At a complexity reset:

1. Stop adding code.
2. Re-read the original requirement and current plan.
3. Group findings by root cause.
4. Compare the complete diff with the recorded base SHA.
5. Remove speculative branches and unreleased compatibility machinery.
6. Update the scope contract and decision log.
7. Re-run focused tests from a clean state.

Unreleased branch-local code is not a sunk cost.

## 11. Implementation workflow

For each milestone:

1. Confirm the milestone’s acceptance criteria before coding.
2. Add or identify a failing test or reproducible scenario.
3. Make the smallest coherent production change.
4. Run focused checks.
5. Inspect the complete diff for the milestone.
6. Run negative, boundary, and failure-path checks.
7. Update `Progress`, `Surprises & Discoveries`, and `Decision Log`.
8. Record evidence files and command outputs.
9. Perform an independent review pass focused on design, correctness, complexity, security, concurrency, and test validity.
10. Run the broader gate only after focused review is clean.
11. Proceed to the next milestone without asking for generic confirmation.

Prefer additive migrations with a clear retirement step. Parallel implementations are allowed only when the plan states why, how both paths are validated, which path is authoritative, and how the old path is removed.

## 12. Test and verification contract

Always begin with the narrowest relevant checks, then run the required broader gate before completion.

Repository-wide baseline commands:

    git diff --check
    python -m compileall -q agent/src agent/cli
    python -m py_compile agent/api_server.py agent/mcp_server.py

Backend regression command:

    pytest --ignore=agent/tests/e2e_backtest \
      --ignore=agent/tests/test_e2e_harness_v2.py \
      --cov=agent --cov-report=term-missing --cov-report=xml \
      --tb=short -q

Frontend commands:

    cd frontend
    npm ci
    npx vitest run --reporter=verbose
    npm run build

Safety-focused checks when relevant:

    pytest agent/tests/test_sdk_order_gate.py \
      agent/tests/test_mandate_enforcement.py \
      agent/tests/test_killswitch_blocks_orders.py \
      agent/tests/test_readonly_default.py -q

Factor integrity checks when relevant:

    pytest agent/tests/factors/test_alpha_purity.py \
      agent/tests/factors/test_lookahead.py -q

Additional requirements:

- Record the working directory, environment, commit SHA, command, exit code, duration, and concise output.
- Never run live e2e tests unless explicitly authorized and configured with sanitized credentials.
- Network-dependent or live-model tests must be separated from deterministic PR gates.
- Tests must validate both success and failure paths.
- Concurrency changes require repeated race/deadlock/idempotency tests.
- Persisted-schema changes require old-read/new-write, corruption, and migration or explicit incompatibility tests.
- Security-boundary changes require adversarial tests.
- Evaluation changes require grader tests proving the grader rejects incorrect outputs.
- A test that always passes, only checks object existence, or asserts implementation details without behavior does not satisfy acceptance.

## 13. High-risk surfaces and authorization

Explicit maintainer or operator approval is required before:

- placing, cancelling, approving, flattening, or otherwise affecting broker orders;
- authorizing broker, OAuth, MCP, exchange, payment, wallet, or cloud accounts;
- writing real credentials to `.env`, `~/.vibe-trading/`, token caches, or external stores;
- starting externally reachable API, MCP, SSE, webhook, dashboard, or telemetry collectors;
- deploying the wiki, publishing packages, creating releases, changing CI secrets, or editing repository rulesets;
- force-pushing, rewriting shared history, deleting backups, or removing persistent runs or memory data.

Safe local reads, in-scope edits, deterministic tests, static analysis, and loopback-only execution are allowed when requested implementation work requires them.

## 14. Security, privacy, and data handling

- Redact secrets before traces, logs, reports, fixtures, and failure cases are written.
- Treat tool arguments and outputs as potentially sensitive.
- Use sanitized synthetic or public fixtures, not private trading exports.
- Record content hashes and references where full content should not be duplicated.
- Trace exporters must fail soft: telemetry failure must not change the authoritative runtime result.
- Research evidence writers must fail closed where evidence completeness is required.
- Side-effecting tools need idempotency keys or explicit `side_effect_state`.
- Unknown side-effect state forbids blind retry.
- Path handling must reject traversal, unsafe symlinks, NULs, and writes outside the declared root.
- Retention and deletion behavior must be explicit for traces, eval results, failure cases, and sidecar blobs.

## 15. Git, branch, and worktree execution model

For the Agent Reliability program defined by this repository, the maintainer explicitly authorizes local branch and Git worktree creation for isolation. This authorization does **not** authorize pushing, opening pull requests, merging remote branches, publishing, deploying, tagging, or changing repository settings.

### 15.1 Long-lived integration worktree

The governance ZIP and all Agent Reliability program changes must first be installed into a **new dedicated Git worktree** created from the repository's current accepted base commit. Do not unpack or develop the program directly in the user's original checkout.

Use one long-lived local integration branch, recommended name:

    codex/agent-reliability-main

and one dedicated integration worktree, for example:

    ../Vibe-Trading-AGS-agent-reliability

The integration branch is the local source of truth for this program. It must contain the extracted `AGENTS.md`, `.agent/`, stage plans, and all stage results accepted so far.

Before creating it:

1. Inspect and record the original checkout branch, SHA, and dirty state.
2. Do not move, reset, clean, overwrite, or stage unrelated user changes.
3. Create the integration branch/worktree from the exact accepted base SHA.
4. Extract the governance ZIP **inside the integration worktree root**.
5. Verify the ZIP paths, manifest hashes, and `BUNDLE_VALIDATION.json` before beginning Stage 00.
6. Commit the governance bootstrap on the integration branch only after verifying the extracted files and repository diff.

### 15.2 One isolated branch/worktree per stage

Every implementation stage `00` through `07` must execute on its own fresh local branch and worktree created from the **current head of `codex/agent-reliability-main`**.

Recommended branch naming:

    codex/agent-reliability-00-governance
    codex/agent-reliability-01-trace-v2
    codex/agent-reliability-02-runtime-instrumentation
    codex/agent-reliability-03-offline-evals
    codex/agent-reliability-04-reliability-chaos
    codex/agent-reliability-05-eval-ci-gates
    codex/agent-reliability-06-failure-loop
    codex/agent-reliability-07-flagship-release

Rules:

- Never implement two stages on the same stage branch.
- Never start a stage branch from the original repository branch once the program has begun.
- Never start Stage `N+1` from an unmerged Stage `N` branch.
- A stage branch may be merged locally into `codex/agent-reliability-main` only after its ExecPlan is `COMPLETE`, every mandatory acceptance criterion is `PASS`, required evidence is bound to the final stage SHA, and the independent review/broad verification gates are clean.
- If a stage is `FAIL`, `BLOCKED`, or materially `INCONCLUSIVE`, do not merge it. Preserve the branch/worktree for diagnosis or supersede it with a fresh stage branch after recording the decision.
- After a successful local merge, rerun the stage's merge-sensitive smoke/verification checks on the integration branch. The merged integration SHA becomes the base SHA for the next stage.
- Do not delete the stage branch/worktree until the integration-branch post-merge verification passes and evidence records the merged SHA.
- Merge conflicts must be resolved from architecture/source-of-truth principles, not by choosing whichever side makes tests green. Re-run the complete affected acceptance matrix after conflict resolution.

### 15.3 Commit and staging discipline

- Stage only explicitly intended paths with `git add -- <paths>`.
- Do not use `git add .`, `git add -A`, or `git add --all`.
- Keep commits stage-scoped and reviewable; do not mix unrelated cleanup.
- Preserve user changes. Never reset or overwrite work you did not create.
- Keep generated run data, local traces, secrets, caches, and benchmark scratch files out of version control unless the active ExecPlan names them as sanitized fixtures.
- Before every local stage merge, inspect `git status`, the full diff from the integration base, and the commit list.
- At every handoff, report the current worktree path, branch, base SHA, head SHA, modified/untracked files, and whether the stage has been merged into the integration branch.

### 15.4 Remote and destructive operations remain unauthorized

Local branch/worktree creation and local stage-to-integration merges are authorized by this program contract. The following still require separate explicit maintainer authorization:

- pushing any branch;
- force-pushing or rewriting shared history;
- opening, editing, or merging a pull request;
- changing branch protection, CODEOWNERS enforcement, Actions secrets, or repository settings;
- creating tags/releases or publishing packages;
- deploying services;
- deleting backups or persistent evidence;
- performing live trading or any external write action.

## 16. Review contract

A clean test run is necessary but not sufficient.

Review the complete diff for:

- overall design and fit with existing architecture;
- actual user or operator behavior;
- edge cases, concurrency, ordering, race, and partial failure;
- unnecessary complexity or speculative generality;
- correctness and usefulness of tests;
- source-of-truth duplication;
- compatibility and schema impact;
- security, privacy, redaction, and authorization;
- operational rollback and diagnostics;
- documentation accuracy;
- every human-written line in the changed scope.

A reviewer must be independent from the implementation pass when practical. Findings must be resolved or explicitly classified in the plan. Do not mark review complete while material uncertainty remains.

## 17. Completion protocol

A stage is `COMPLETE` only when all of the following are true:

- Every mandatory deliverable exists and is exercised through its intended path.
- Every required acceptance criterion is `PASS`.
- No hard invariant is `FAIL`, `BLOCKED`, or `INCONCLUSIVE`.
- Required focused, regression, security, and build commands ran on the final commit.
- No required test was skipped.
- The traceability matrix is complete.
- Evidence artifacts are present, readable, and bound to the final commit and dataset/config hashes.
- The complete diff was reviewed.
- The active plan’s living sections and retrospective are current.
- Documentation and migration notes are accurate.
- Rollback is defined and, where practical, tested.
- No unrelated files or local secrets are included.
- Known limitations are explicit and do not contradict the claimed outcome.

The final handoff must include:

1. Outcome and status.
2. Files changed.
3. Commands run and exact results.
4. Acceptance matrix summary.
5. Evidence artifact paths and hashes.
6. Remaining limitations or risks.
7. Rollback path.
8. Whether any requested action was not performed.

Never use “complete,” “done,” “production-ready,” “verified,” or equivalent language without the evidence above.

## 18. Stage roadmap for the Agent Reliability program

Execute these plans in order:

1. `00-governance-and-baseline`
2. `01-trace-contract-v2`
3. `02-runtime-instrumentation`
4. `03-offline-eval-harness`
5. `04-failure-recovery-and-chaos`
6. `05-regression-gates-and-ci`
7. `06-production-failure-loop`
8. `07-flagship-hardening-and-release`

A later stage may research in parallel, but implementation may not bypass incomplete prerequisite gates. Cross-stage changes require an amendment to both affected plans and a decision-log entry identifying the new dependency.

## 19. Code Review Rules

When reviewing changes governed by this file, flag:

- post-hoc acceptance criteria written to match an implementation;
- tests that cannot fail for the intended defect;
- mocks replacing the exact boundary under claim;
- silent fallback from authoritative evidence to caller data;
- broad exceptions converted to success;
- unbounded retries or retries after unknown side effects;
- trace or evaluation data promoted to research truth;
- manually edited benchmark results or README badges;
- hidden schema or prompt changes without version/hash updates;
- thresholds lowered without an evidence-backed decision;
- production claims derived only from demos or fixtures;
- changes whose complexity exceeds the requirement;
- plans whose `Progress` or evidence does not match the working tree.

The safe path is always to narrow the claim, add observable evidence, or mark the result `INCONCLUSIVE` rather than manufacture certainty.
