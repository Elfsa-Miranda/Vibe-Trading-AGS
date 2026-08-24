# Turn evaluation and reliability evidence into protected regression gates

**Plan-ID:** AGS-AR-05  
**Status:** PROPOSED  
**Stage:** 05  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-04 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-04`  
**Depends-On:** AGS-AR-00 through AGS-AR-04 `COMPLETE`  
**Supersedes:** Manual, unprotected interpretation of Agent benchmark results for pull-request/release decisions  
**Target-Outcome:** Pull requests, nightly experiments, and releases receive source-bound, non-compensatory Agent quality gates with immutable baselines and complete evidence artifacts.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a pull request cannot silently regress hard safety invariants, task success, recovery, cost, or latency without a visible gate. Deterministic smoke cases run on every relevant change; stochastic/live-model cases run on a separate scheduled path; release decisions use paired source-complete evidence rather than a hand-edited README score.

The shortest demonstration compares an accepted baseline against:
1. an equivalent candidate, which passes;
2. a candidate with one forbidden action, which fails regardless of average score;
3. an underpowered/noisy candidate, which becomes `INCONCLUSIVE`, not pass.

## Implementation Scope Contract

### Required behavior

- `REQ-CIG-01`: Define separate PR, nightly, and release evaluation profiles with frozen datasets, graders, models/providers where applicable, repetitions, budgets, and acceptance policy.
- `REQ-CIG-02`: PR gates are deterministic, offline, secret-free, bounded, and run only affected smoke/core slices plus required hard invariants.
- `REQ-CIG-03`: Nightly gates may run live/stochastic model evaluations with explicit secrets, sampling, retries, cost limits, and result retention; failures cannot be hidden from release evidence.
- `REQ-CIG-04`: Release gates run the complete approved benchmark/fault suites and compare a candidate against an immutable accepted baseline on paired cases/schedules.
- `REQ-CIG-05`: Baseline manifests are content-addressed and separately controlled. A candidate change cannot modify both implementation and accepted baseline without an explicit baseline-update workflow/review.
- `REQ-CIG-06`: Hard safety, policy, evidence-integrity, trace-integrity, and unsafe-recovery violations have zero tolerance and cannot be compensated.
- `REQ-CIG-07`: Soft quality/resource metrics use a predeclared paired comparison with practical margins, confidence intervals, unavailable handling, and minimum sample requirements.
- `REQ-CIG-08`: Comparison outcomes are exactly `PASS`, `FAIL`, `BLOCKED`, or `INCONCLUSIVE`; an underpowered or incomplete run cannot pass.
- `REQ-CIG-09`: Every workflow uploads complete machine-readable and human-readable artifacts, including raw case results, manifests, hashes, environment, command, and gate decision.
- `REQ-CIG-10`: Required checks run against the final candidate SHA; stale successful runs do not satisfy a moved head.
- `REQ-CIG-11`: Workflow permissions are least-privilege, untrusted pull requests do not receive secrets, and artifact/report rendering is safe.
- `REQ-CIG-12`: Gate policy, baseline, dataset, grader, and workflow changes are independently reviewed and themselves tested against pass/fail/inconclusive fixtures.
- `REQ-CIG-13`: Coverage and static/security gates may be tightened from the truthful baseline but may not be lowered to pass a candidate.
- `REQ-CIG-14`: Branch filters include the actual protected/default target branches after explicit repository-policy review.

### Compatibility boundary

Existing CI tests/builds remain and are not replaced by eval gates. Workflow names may be additive before branch protection changes. No repository settings are changed through code alone. Existing baseline artifacts remain readable and immutable.

Live-model nightly results are not required for external untrusted pull requests. Release eligibility may depend on a recent complete nightly run, as defined before implementation.

### Intentionally unsupported cases and failure behavior

- GitHub branch protection, required checks, rulesets, and CODEOWNERS enforcement require explicit maintainer authorization outside ordinary file changes.
- Missing live credentials make nightly profile `BLOCKED`, not PR failure and not release pass.
- A baseline update caused by intentional product change requires a dedicated review and evidence; automatic “bless candidate” is unsupported.
- Formal statistical superiority is not required; non-inferiority plus hard-gate safety may be sufficient when predeclared.
- Historical experiment artifacts are not rewritten to the new format.

### In-scope files

- `.github/workflows/agent-eval-smoke.yml`
- `.github/workflows/agent-eval-nightly.yml`
- `.github/workflows/agent-eval-release.yml`
- `.github/workflows/test.yml` only for branch/coordination changes
- `.github/CODEOWNERS` only with explicit approval
- `agent/src/evals/gates.py`
- `agent/src/evals/statistics.py`
- `agent/src/evals/baseline.py`
- `agent/src/evals/ci.py`
- `agent/evals/profiles/`
- `scripts/run_agent_eval_gate.py`
- `scripts/verify_agent_eval_gate.py`
- `agent/tests/evals/gates/`
- baseline/evidence documentation

### Out-of-scope files and behavior

No automatic merging, deployment, release creation, branch-protection mutation, model prompt optimization, production trace ingestion, or baseline self-update.

## Current System Evidence

Current CI runs syntax checks, a broad pytest command, frontend build, and frontend tests, and is filtered to `main`. The observed public default branch at plan-authoring time is different and must be reverified. Coverage is collected but `fail_under = 0`. There are no Agent evaluation or fault-recovery gates.

Stages 03–04 provide source-bound experiment and recovery artifacts. This stage turns those outputs into decision policy without changing graders post hoc.

## Terminology

- **PR profile:** Fast deterministic checks on pull requests.
- **Nightly profile:** Scheduled stochastic/live-model experiment.
- **Release profile:** Full benchmark required for release evidence.
- **Accepted baseline:** Immutable comparison reference approved through a separate process.
- **Non-inferiority margin:** Maximum practically acceptable degradation fixed before candidate results.
- **Paired bootstrap:** Resampling paired case-level differences to estimate uncertainty.
- **Hard gate:** Zero-tolerance invariant.
- **Stale check:** A result produced for an older commit than the current candidate head.

## Dependencies and Prerequisite Gate

- Stages 03 and 04 produce deterministic benchmark and chaos evidence.
- Baseline experiment is complete and source-verified.
- Statistical method, margins, repetitions, and minimum sample size are preregistered.
- CI runtime/cost budgets are measured.
- Workflow target branches and repository setting ownership are confirmed.
- No plan placeholder remains when activated.

## Invariants

- `INV-CIG-01`: Candidate cannot rewrite its accepted baseline in the normal implementation workflow.
- `INV-CIG-02`: Any hard-gate violation fails.
- `INV-CIG-03`: Missing/incomplete/underpowered evidence cannot pass.
- `INV-CIG-04`: Gate decision is derived from raw case evidence and frozen policy.
- `INV-CIG-05`: Required checks bind the final candidate SHA.
- `INV-CIG-06`: Untrusted pull requests receive no secrets.
- `INV-CIG-07`: CI failures and skipped cases remain visible.
- `INV-CIG-08`: Thresholds/margins are not relaxed after candidate results.
- `INV-CIG-09`: Existing software/security tests remain.
- `INV-CIG-10`: Baseline/dataset/grader changes trigger their own validation.
- `INV-CIG-11`: Workflow artifacts are tamper-verified before gate decision.
- `INV-CIG-12`: Live-model cost is hard bounded.
- `INV-CIG-13`: Manual override, if repository policy later permits it, is audited and cannot rewrite evidence.
- `INV-CIG-14`: Readme badges/metrics are generated from accepted manifests.

## Deliverables

- `DEL-CIG-01`: Versioned gate-policy schemas and profiles.
- `DEL-CIG-02`: Paired comparison/statistics implementation.
- `DEL-CIG-03`: Accepted-baseline registry and verification.
- `DEL-CIG-04`: Deterministic PR workflow.
- `DEL-CIG-05`: Scheduled nightly workflow.
- `DEL-CIG-06`: Full release-evidence workflow.
- `DEL-CIG-07`: Gate policy adversarial fixtures.
- `DEL-CIG-08`: Workflow security tests/lint.
- `DEL-CIG-09`: Artifact upload/verification and summary renderer.
- `DEL-CIG-10`: Maintainer checklist for branch protection/required checks/CODEOWNERS.
- `DEL-CIG-11`: Initial accepted baseline and gate decision evidence.

## Non-Goals and Prohibited Changes

- `NC-CIG-01`: Do not auto-bless the candidate as baseline.
- `NC-CIG-02`: Do not lower thresholds/margins to produce a pass.
- `NC-CIG-03`: Do not average away a hard safety/evidence failure.
- `NC-CIG-04`: Do not treat missing cases as successes or remove them from denominators.
- `NC-CIG-05`: Do not expose secrets to fork pull requests.
- `NC-CIG-06`: Do not run paid/live models in the deterministic PR profile.
- `NC-CIG-07`: Do not rely only on aggregate metrics.
- `NC-CIG-08`: Do not hand-edit workflow summaries or README metrics.
- `NC-CIG-09`: Do not delete failing experiment artifacts.
- `NC-CIG-10`: Do not mutate repository settings without explicit approval.
- `NC-CIG-11`: Do not accept a successful check from an old SHA.
- `NC-CIG-12`: Do not let a workflow use broad write permissions without documented necessity.
- `NC-CIG-13`: Do not make nightly flakiness invisible through unconditional retry.

## Architecture and Data Flow

    frozen EvalProfile + GatePolicy + AcceptedBaseline
                       │
             workflow selects profile
                       ▼
    run experiment on candidate SHA
                       │
                       ▼
    verify experiment artifacts/hashes
                       │
                       ▼
    hard-gate evaluator
       ├─ any violation ──► FAIL
       └─ none
            ▼
    paired metric comparison
       ├─ complete and within margins ──► PASS
       ├─ clear regression ─────────────► FAIL
       ├─ missing prerequisite ─────────► BLOCKED
       └─ underpowered/ambiguous ───────► INCONCLUSIVE
                       │
                       ▼
    upload immutable artifact bundle + concise job summary

Baseline selection is trusted configuration reviewed separately from candidate code.

## Interfaces and Schemas

`GatePolicyV1` includes:

- policy ID/version/hash;
- profile;
- hard-gate grader IDs;
- soft metrics and direction;
- practical margin per metric;
- statistical method/confidence;
- minimum paired cases/repetitions;
- blocked/inconclusive behavior;
- maximum cost/time;
- required baseline age and nightly evidence;
- accepted dataset/grader/model/tool/skill hashes.

`GateDecisionV1` includes candidate/baseline SHA, policy hash, experiment refs, per-hard-gate results, per-metric effect/interval/margin, missing evidence, final state, and reason codes.

Baseline registry entries are immutable content-addressed manifests. Updating a logical baseline pointer requires a dedicated command/workflow and review.

## Milestones

### Milestone 1 — Preregister policy and baseline ownership

Freeze profile contents, hard/soft metrics, margins, statistical method, artifact requirements, and baseline update procedure. Build pass/fail/inconclusive synthetic fixtures before evaluating real candidates.

### Milestone 2 — Implement gate engine and baseline verifier

Build paired comparison, hard-gate decision, artifact/source verification, and stale-SHA rejection. Falsify it with manipulated baseline, missing cases, tampered artifacts, and threshold changes.

### Milestone 3 — Add PR and nightly workflows

Create least-privilege workflows, cache safely, upload artifacts, and separate secret-free PR from live nightly. Test branch filters and untrusted PR behavior.

### Milestone 4 — Add release gate and repository-policy handoff

Run the complete profile, require current evidence, generate release summary, and provide exact settings checklist. Do not mutate settings without approval.

## Detailed Tasks

1. Inventory current workflow triggers, permissions, durations, and artifacts.
2. Freeze initial profiles and policy in versioned files.
3. Implement deterministic paired bootstrap or approved alternative using per-case outcomes.
4. Predeclare non-inferiority margins from product/reliability needs, not candidate performance.
5. Implement hard-gate short-circuit preserving all result details.
6. Implement baseline registry with detached verification from candidate workspace.
7. Reject candidate changes to the referenced baseline in ordinary gate execution.
8. Verify dataset/grader/policy/experiment hashes before comparison.
9. Implement final-SHA check through workflow context.
10. Add PR workflow with path filters that cannot skip hard governance/runtime changes.
11. Add nightly concurrency limits, cost/time ceilings, explicit retries, and complete artifact retention.
12. Add release workflow that consumes a named accepted nightly/full result.
13. Add synthetic decision fixtures: pass, hard fail, statistical fail, blocked, inconclusive, tampered, stale.
14. Add workflow static analysis/security tests.
15. Measure CI duration/cost and tune only dataset selection, not acceptance rigor.
16. Document optional branch protection/CODEOWNERS changes for maintainer application.
17. Run workflows on final SHA and archive evidence.

## Failure Semantics

- `GATE_HARD_FAILURE`
- `GATE_SOFT_REGRESSION`
- `GATE_INCONCLUSIVE`
- `GATE_BLOCKED`
- `GATE_BASELINE_INVALID`
- `GATE_BASELINE_CHANGED`
- `GATE_DATASET_MISMATCH`
- `GATE_GRADER_MISMATCH`
- `GATE_POLICY_MISMATCH`
- `GATE_ARTIFACT_TAMPERED`
- `GATE_RESULT_INCOMPLETE`
- `GATE_STALE_SHA`
- `GATE_SAMPLE_TOO_SMALL`
- `GATE_COST_EXCEEDED`
- `GATE_TIMEOUT`
- `GATE_SECRET_UNAVAILABLE`

No workflow-level retry converts a reproducible gate failure into pass. Infrastructure retries retain all attempts.

## Security, Privacy, and Threat Model

Threats include malicious pull requests reading secrets, workflow command injection, artifact poisoning, cache poisoning, baseline replacement, unsafe HTML, excessive permissions, stale-check races, and cost denial of service.

Controls:

- `pull_request` secret-free execution; avoid unsafe `pull_request_target` code execution;
- least permissions;
- pin/approve action versions according to repo policy;
- sanitize dynamic values and paths;
- separate trusted baseline artifacts;
- verify hashes after download;
- escape job summaries;
- concurrency/cost/time limits;
- final-SHA binding;
- workflow changes owned/reviewed separately;
- no live credentials in artifacts/logs.

## Test Strategy

- Unit/property tests for statistical calculations and states.
- Synthetic gate-decision fixtures.
- Baseline/artifact tamper and stale-SHA tests.
- Minimum-sample and missing-case tests.
- Hard-gate non-compensation tests.
- Threshold post-hoc change detection through policy hash.
- Workflow syntax/static security analysis.
- Local action/workflow execution where feasible.
- Fork/untrusted permissions review.
- Actual smoke/nightly/release dry runs on a controlled branch.
- Mutation test that drops a failing case or changes a margin and must alter policy hash/fail review.

## Validation and Acceptance

- `AC-CIG-01`: Deterministic PR profile runs without secrets/network/live providers and stays within frozen runtime budget.
- `AC-CIG-02`: One hard safety violation yields `FAIL` regardless of aggregate quality.
- `AC-CIG-03`: An underpowered/incomplete comparison yields `INCONCLUSIVE`.
- `AC-CIG-04`: Missing prerequisites/credentials yield `BLOCKED`, not pass.
- `AC-CIG-05`: Equivalent/non-inferior synthetic candidate passes under frozen policy.
- `AC-CIG-06`: Clear paired regression fails with effect/interval evidence.
- `AC-CIG-07`: Candidate baseline edit, artifact tamper, dataset/grader mismatch, and stale SHA are rejected.
- `AC-CIG-08`: Every workflow uploads complete raw/summary artifacts bound to candidate SHA.
- `AC-CIG-09`: Untrusted pull request cannot access nightly/release secrets.
- `AC-CIG-10`: Existing tests/build/security checks remain active.
- `AC-CIG-11`: Branch triggers cover the actual target branches.
- `AC-CIG-12`: Thresholds are equal to preregistered policy; no post-hoc relaxation.
- `AC-CIG-13`: Actual final-SHA workflow runs reach the expected state.
- `AC-CIG-14`: Independent review covers workflow security, statistics, baseline ownership, and repository settings handoff.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-CIG-01 | `agent/evals/profiles/**` | TEST-CIG-01 profile schema | EVID-CIG-01 profile manifest | PROPOSED |
| REQ-CIG-02 | smoke workflow | TEST-CIG-02 offline/no-secret run | EVID-CIG-02 PR artifact | PROPOSED |
| REQ-CIG-03 | nightly workflow | TEST-CIG-03 budget/credential behavior | EVID-CIG-03 nightly artifact | PROPOSED |
| REQ-CIG-04 | release workflow/gate engine | TEST-CIG-04 complete paired run | EVID-CIG-04 release artifact | PROPOSED |
| REQ-CIG-05 | `baseline.py`/registry | TEST-CIG-05 baseline mutation matrix | EVID-CIG-05 baseline report | PROPOSED |
| REQ-CIG-06 | hard-gate engine | TEST-CIG-06 non-compensation | EVID-CIG-06 decision fixtures | PROPOSED |
| REQ-CIG-07 | `statistics.py` | TEST-CIG-07 effect/CI/margin matrix | EVID-CIG-07 statistics report | PROPOSED |
| REQ-CIG-08 | `gates.py` states | TEST-CIG-08 four-state matrix | EVID-CIG-08 decisions | PROPOSED |
| REQ-CIG-09 | artifact bundler | TEST-CIG-09 completeness/tamper | EVID-CIG-09 uploaded bundle | PROPOSED |
| REQ-CIG-10 | CI SHA verifier | TEST-CIG-10 moved-head fixture | EVID-CIG-10 SHA report | PROPOSED |
| REQ-CIG-11 | workflow permissions | TEST-CIG-11 security review/static test | EVID-CIG-11 permission report | PROPOSED |
| REQ-CIG-12 | policy ownership/tests | TEST-CIG-12 policy-change matrix | EVID-CIG-12 review record | PROPOSED |
| REQ-CIG-13 | coverage/static configs | TEST-CIG-13 no-threshold-lowering | EVID-CIG-13 config diff report | PROPOSED |
| REQ-CIG-14 | workflow triggers | TEST-CIG-14 branch-trigger audit | EVID-CIG-14 trigger report | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/evals/gates -q --tb=short
    python scripts/run_agent_eval_gate.py --profile pr-smoke --candidate-sha <sha>
    python scripts/verify_agent_eval_gate.py <gate-decision.json>
    python scripts/test_workflow_policies.py .github/workflows
    python -m compileall -q agent/src/evals
    git diff --check

Execute controlled GitHub workflow runs and record their run IDs. Repository settings remain manual until explicitly authorized.

## Idempotence, Rollback, and Recovery

Gate experiments and decisions are content-addressed. Rerunning the same candidate/profile creates another attempt linked to the same inputs; it does not overwrite prior results. Baseline logical pointers change only through dedicated reviewed update.

Rollback removes workflows from required-check configuration first (manual authorized action), then reverts files. Preserve accepted baselines and experiment evidence.

## Observability and Evidence Artifacts

- accepted baseline manifests;
- profile/policy hashes;
- PR/nightly/release experiment bundles;
- gate decisions;
- raw paired samples;
- workflow metadata and final SHA;
- generated job summaries and optional badges.

Artifacts use retention appropriate to profile; release evidence is retained long-term.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-CIG-01 — Baseline laundering | Medium | Critical | baseline-diff gate | Separate immutable baseline workflow |
| RISK-CIG-02 — Hard failure averaged away | Medium | Critical | non-compensation tests | Zero-tolerance first stage |
| RISK-CIG-03 — Flaky live eval blocks work | High | Medium | repeated/state analysis | Separate nightly; use inconclusive |
| RISK-CIG-04 — Secret exposure | Low | Critical | workflow security review | Secret-free PR, least privilege |
| RISK-CIG-05 — Statistical gate is post hoc | Medium | High | policy hash/history | Preregister method/margins |
| RISK-CIG-06 — CI too slow/costly | Medium | High | runtime/cost evidence | Fast deterministic slice; full nightly/release |
| RISK-CIG-07 — Default branch not covered | Medium | High | trigger audit | Explicitly align branches/settings |
| RISK-CIG-08 — Stale green check | Medium | High | final-SHA verifier | Bind every decision to head SHA |

## Progress

- [ ] Record Stage 04 accepted SHA and current CI inventory.
- [ ] Preregister profiles, policies, margins, and baseline process.
- [ ] Implement gate/statistics/baseline verification.
- [ ] Add PR/nightly/release workflows and security tests.
- [ ] Run synthetic and real controlled gate cases.
- [ ] Complete independent statistical/security review.
- [ ] Provide authorized repository-settings checklist.
- [ ] Bind final workflow evidence and update acceptance states.

## Surprises & Discoveries

- Observation: Existing CI collects coverage but has no minimum; a truthful staged threshold policy is needed rather than an arbitrary jump.
  Evidence: `pyproject.toml`.
- Observation: Workflow branch filters and observed default branch may not align.
  Evidence: repository metadata and current workflow; reverify at activation.

## Decision Log

- Decision: Separate deterministic PR gates from stochastic nightly/release evidence.
  Alternatives: Run live model eval on every PR; omit live eval.
  Rationale: PR checks must be safe/fast/reproducible while model behavior still needs recurring measurement.
  Date/Author: 2026-08-24 / plan author.
- Decision: Use four-state decisions and predeclared non-inferiority.
  Alternatives: Compare raw means; convert ambiguity to pass.
  Rationale: Noise and missing evidence must not produce false certainty.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, report gate runtime/cost, detected regressions, flakiness/inconclusive rates, baseline governance, and remaining manual settings.

## Plan Revision Log

- 2026-08-24: Initial plan created.
