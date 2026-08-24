# Build a source-bound offline Agent evaluation harness

**Plan-ID:** AGS-AR-03  
**Status:** PROPOSED  
**Stage:** 03  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-02 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-02`  
**Depends-On:** AGS-AR-00, AGS-AR-01, and AGS-AR-02 `COMPLETE`  
**Supersedes:** Ad-hoc demo-only or final-answer-only evaluation for the Agent runtime  
**Target-Outcome:** A reproducible offline harness evaluates task outcome, trajectory, tools, evidence, artifacts, cost, latency, and trace integrity from versioned datasets and graders.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a developer can run a frozen Agent benchmark against a baseline and candidate, receive case-level and aggregate results, inspect why each score was assigned, and prove that a prompt/model/tool/skill/runtime change did or did not improve the system.

The shortest demonstration runs a deterministic smoke dataset, intentionally evaluates one correct and several incorrect trajectories, and produces a report in which each grader rejects the defect it is designed to catch.

## Implementation Scope Contract

### Required behavior

- `REQ-EVL-01`: Define strict versioned schemas for `EvalDataset`, `EvalCase`, `EvalRun`, `CaseResult`, `GraderResult`, `Experiment`, and `Comparison`.
- `REQ-EVL-02`: Every case binds dataset/case version, provenance, input/setup, allowed capabilities, oracle/constraints, budgets, grader configuration, and anti-cheat metadata.
- `REQ-EVL-03`: The runner creates isolated per-case workspaces, deterministic seeds where supported, timeout/resource budgets, and source-bound trace/artifact references.
- `REQ-EVL-04`: Deterministic graders evaluate task outcome, tool selection, tool arguments, trajectory constraints, evidence validity, artifact validity, trace integrity, policy compliance, latency, token usage, and tool-call budget.
- `REQ-EVL-05`: Trajectory grading is constraint-based—required/forbidden operations, partial ordering, budgets, and terminal conditions—not exact golden-sequence matching.
- `REQ-EVL-06`: Each grader emits a typed state, score where meaningful, reasons, evidence references, version/hash, and failure codes; callers cannot submit authoritative scores.
- `REQ-EVL-07`: LLM-as-judge is optional, isolated from deterministic hard gates, versioned, calibrated against labeled cases, and never the sole authority for safety or evidence validity.
- `REQ-EVL-08`: Reports preserve case-level results, aggregate metrics, denominators, unavailable/blocked/inconclusive counts, configuration/model/tool/skill/code hashes, and raw artifact references.
- `REQ-EVL-09`: A comparison supports paired baseline/candidate analysis while preserving raw per-case outcomes; this stage may compute descriptive deltas but does not create CI promotion gates.
- `REQ-EVL-10`: Initial datasets include normal, boundary, adversarial, long-context, evidence, and tool-use cases with explicit coverage and contamination controls.
- `REQ-EVL-11`: Grader tests prove false positives and false negatives are detected; a grader is not accepted only because it scores the happy path.
- `REQ-EVL-12`: Evaluation runs are read-only with respect to external systems and cannot place trades or use private production data.

### Compatibility boundary

- No change to authoritative research evaluation/Claim Matrix semantics.
- Agent runtime result and trace contracts from prior stages remain.
- Existing pytest suites remain software tests; the new harness supplements rather than replaces them.
- New evaluation APIs may be internal and CLI-driven in this stage.
- No required live model/provider in PR-deterministic evaluation.

### Intentionally unsupported cases and failure behavior

- Free-form subjective quality without a defined rubric is `UNSUPPORTED_GRADER` or optional LLM-judge evidence, not a hard pass.
- Cases requiring unavailable external services are `BLOCKED`, separated from deterministic datasets.
- Stochastic model comparison is descriptive in this stage; formal non-inferiority gates arrive in Stage 05.
- Exact chain-of-thought grading is prohibited.
- A case with missing source trace/artifact evidence cannot receive evidence-valid success.

### In-scope files

- `agent/src/evals/__init__.py`
- `agent/src/evals/schema.py`
- `agent/src/evals/datasets.py`
- `agent/src/evals/runner.py`
- `agent/src/evals/experiment.py`
- `agent/src/evals/compare.py`
- `agent/src/evals/report.py`
- `agent/src/evals/graders/`
- `agent/evals/datasets/`
- `agent/tests/evals/`
- `agent/cli/` for an additive eval command
- `scripts/` for deterministic sample generation/verification
- evidence/report directories

### Out-of-scope files and behavior

No runtime recovery changes, fault injection framework, CI required checks, online trace ingestion, automatic prompt optimization, automatic code modification, or live trading.

## Current System Evidence

The repository already has extensive pytest coverage and domain-specific research evaluation, dossiers, events, replay, and evidence governance. These validate code and quantitative claims but do not provide a unified Agent-level benchmark across final outcome, trajectory, tools, cost, latency, recovery, and trace integrity.

Stage 02 provides source-bound runtime traces required for trajectory graders. This stage must reuse existing artifact and research-event verifiers rather than reimplement them as a second truth.

## Terminology

- **Eval case:** One frozen task and its acceptance constraints.
- **Oracle:** The expected facts, artifacts, invariants, or constraints used by graders.
- **Grader:** A versioned function that converts source evidence into a typed assessment.
- **Trajectory:** Observable ordered operations, not private reasoning.
- **Hard gate:** A criterion that cannot be compensated by other scores.
- **Anti-cheat metadata:** Hidden or separately protected information that prevents the Agent from reading its expected answer.
- **Contamination:** The evaluated Agent sees test answers, graders, or final-only evidence.
- **Paired comparison:** Baseline and candidate evaluated on the same cases/schedules.

## Dependencies and Prerequisite Gate

- Stage 02 produces verified, source-complete traces.
- Stage 00 governance and evidence conventions pass.
- Existing research artifact/event verifiers have documented interfaces.
- Dataset licensing/provenance and private-data policy are accepted.
- At least one deterministic Agent test adapter can execute without external network access.
- Initial rubric and case taxonomy are reviewed before case generation.

## Invariants

- `INV-EVL-01`: Callers cannot override grader scores, reasons, or final verdicts.
- `INV-EVL-02`: Missing source evidence cannot become a pass.
- `INV-EVL-03`: Deterministic hard graders do not call an LLM or network.
- `INV-EVL-04`: Dataset and grader hashes are recorded before execution.
- `INV-EVL-05`: Baseline and candidate raw results remain immutable.
- `INV-EVL-06`: No evaluator exposes hidden oracle content to the Agent.
- `INV-EVL-07`: Safety/policy failures are non-compensatory.
- `INV-EVL-08`: A trajectory may vary while satisfying required/forbidden/order constraints.
- `INV-EVL-09`: Reports retain all cases, including failure, error, timeout, blocked, and inconclusive.
- `INV-EVL-10`: Evaluation cannot write to brokers, wallets, production databases, or external services.
- `INV-EVL-11`: Research evidence is verified by existing authority, not reconstructed from telemetry claims.
- `INV-EVL-12`: Dataset edits change the dataset version/hash.

## Deliverables

- `DEL-EVL-01`: Strict eval schemas and canonical serialization.
- `DEL-EVL-02`: Dataset registry/loader with provenance and hash verification.
- `DEL-EVL-03`: Isolated runner with budgets, retries disabled by default at harness level, and complete case lifecycle.
- `DEL-EVL-04`: Deterministic grader suite.
- `DEL-EVL-05`: Optional calibrated LLM judge adapter.
- `DEL-EVL-06`: Experiment and paired comparison engine.
- `DEL-EVL-07`: JSON plus human-readable report renderer.
- `DEL-EVL-08`: Initial benchmark datasets and coverage manifest.
- `DEL-EVL-09`: Grader adversarial/calibration fixtures.
- `DEL-EVL-10`: CLI and reproducible demo.
- `DEL-EVL-11`: Evaluation evidence manifest bound to final commit.

## Non-Goals and Prohibited Changes

- `NC-EVL-01`: Do not grade exact private reasoning.
- `NC-EVL-02`: Do not declare quality from one aggregate average.
- `NC-EVL-03`: Do not allow a high task-success score to compensate for policy/evidence violations.
- `NC-EVL-04`: Do not use an LLM judge as sole authority.
- `NC-EVL-05`: Do not place expected answers in Agent-visible workspace/context.
- `NC-EVL-06`: Do not hand-edit case results or report metrics.
- `NC-EVL-07`: Do not make all tests pass by weakening or removing hard cases.
- `NC-EVL-08`: Do not require paid/live external services for deterministic smoke/core datasets.
- `NC-EVL-09`: Do not copy research scoring logic into eval graders.
- `NC-EVL-10`: Do not promote the descriptive comparison to a CI release decision yet.
- `NC-EVL-11`: Do not count fixture quantity as evidence of benchmark coverage.

## Architecture and Data Flow

    EvalDataset (frozen hash)
         │ cases + hidden oracle refs
         ▼
    EvalRunner
      ├─ isolated workspace
      ├─ Agent runtime
      ├─ verified trace
      └─ authoritative artifacts/event refs
         │
         ▼
    GraderPipeline
      ├─ deterministic outcome/tool/trajectory graders
      ├─ evidence/artifact authority adapters
      ├─ resource/policy graders
      └─ optional calibrated LLM judge
         │
         ▼
    CaseResult (immutable)
         │
         ├─► Experiment report
         └─► Paired Comparison (descriptive in Stage 03)

Graders consume references and reopened sources. They do not trust caller summaries.

## Interfaces and Schemas

Core types:

    class EvalState(StrEnum):
        PASS = "pass"
        FAIL = "fail"
        BLOCKED = "blocked"
        INCONCLUSIVE = "inconclusive"
        ERROR = "error"

    @dataclass(frozen=True)
    class TrajectoryConstraints:
        required_operations: tuple[str, ...]
        forbidden_operations: tuple[str, ...]
        precedence: tuple[tuple[str, str], ...]
        max_tool_calls: int | None
        max_turns: int | None
        allowed_tools: tuple[str, ...] | None

    class Grader(Protocol):
        grader_id: str
        version: str
        def grade(self, context: GradeContext) -> GraderResult: ...

`EvalCase` includes case ID/version/category, prompt/task, fixture/setup refs, capability allowlist, hidden oracle refs, constraints, budgets, expected terminal states, required graders, tags, provenance/license, and contamination boundary.

`GraderResult` includes state, optional score/threshold, hard-gate boolean, reason/failure codes, evidence refs, grader hash, duration, and warnings.

## Milestones

### Milestone 1 — Freeze eval contracts and anti-contamination boundary

Define schemas, case lifecycle, grader ownership, hidden oracle separation, dataset provenance, and report semantics. Add invalid-schema and contamination tests before runner implementation.

### Milestone 2 — Implement isolated runner and source adapters

Create per-case workspaces, deterministic config, budgets, trace verification, and adapters to existing research/artifact authorities. Demonstrate correct handling of success, timeout, runtime error, missing trace, and corrupt artifact.

### Milestone 3 — Implement and falsify deterministic graders

Build each grader with positive, negative, boundary, and adversarial fixtures. Deliberately feed incorrect outputs/trajectories and show rejection. Calibrate any optional LLM judge separately.

### Milestone 4 — Build initial benchmark and reports

Author a coverage-driven initial dataset, run baseline experiment, render reports, and execute paired descriptive comparison. Produce a coverage manifest and identify missing domains without inflating claims.

## Detailed Tasks

1. Define schemas with strict unknown-field rejection and content hashes.
2. Implement canonical dataset loader and path containment.
3. Separate Agent-visible fixture material from grader-only oracle material.
4. Implement isolated temporary/run workspace setup and teardown.
5. Bind each case run to code/model/provider/prompt/tool/skill/dataset/grader hashes.
6. Implement task outcome grader using explicit case oracle.
7. Implement tool selection and argument-schema/semantic graders.
8. Implement partial-order trajectory grader and forbidden-operation hard gate.
9. Implement trace integrity grader through Stage 01 verifier.
10. Implement artifact/evidence graders as adapters to existing verifiers.
11. Implement latency/token/tool/turn budget graders with explicit unavailable semantics.
12. Implement policy grader with non-compensatory failure.
13. Build optional LLM judge with frozen prompt/rubric/model and labeled calibration set.
14. Implement aggregate report preserving denominators and four-state counts.
15. Implement paired comparison preserving raw per-case results.
16. Author initial datasets only after grader contracts are frozen.
17. Minimum initial target: at least 36 reviewed cases, with at least 6 cases in each of six categories: core task, tool/trajectory, evidence/artifact, boundary/failure, long-context, and adversarial/policy. This is a floor, not proof of coverage.
18. Add dataset review checklist for realism, uniqueness, provenance, anti-cheat, and defect-detection value.
19. Run full grader falsification matrix and baseline experiment.
20. Independently review cases and graders before accepting benchmark results.

## Failure Semantics

- `EVAL_CASE_INVALID`
- `EVAL_DATASET_HASH_MISMATCH`
- `EVAL_ORACLE_EXPOSED`
- `EVAL_RUN_TIMEOUT`
- `EVAL_TRACE_INVALID`
- `EVAL_ARTIFACT_MISSING`
- `EVAL_GRADER_ERROR`
- `EVAL_GRADER_UNAVAILABLE`
- `EVAL_HARD_GATE_FAILED`
- `EVAL_RESULT_INCOMPLETE`
- `EVAL_COMPARISON_UNPAIRED`
- `EVAL_CONTAMINATION_DETECTED`

Harness infrastructure error is distinct from Agent failure. Missing provider usage yields an unavailable resource metric, not zero. Grader disagreement is retained and may make the criterion `INCONCLUSIVE`.

## Security, Privacy, and Threat Model

Threats include oracle leakage, prompt injection targeting graders, malicious fixture paths, private data in promoted cases, grader manipulation, report tampering, and unsafe tool capabilities during eval.

Controls:

- Agent-visible and grader-only roots are physically separate.
- Runner uses a strict tool/capability allowlist and research-only mode.
- No broker/write credentials are present.
- Cases use public/synthetic/sanitized data with provenance.
- Grader inputs are source refs and redacted outputs.
- Results are content-addressed and immutable.
- Dataset/report HTML is escaped.
- LLM judge receives only allowed redacted content.
- Case fixtures cannot configure host commands or external endpoints.

## Test Strategy

- Schema and canonicalization tests.
- Invalid/unknown field and hash-tamper tests.
- Workspace isolation and cleanup tests.
- Oracle non-reachability tests.
- Each grader: true positive, true negative, boundary, adversarial, and missing-evidence case.
- Mutation-style checks that intentionally corrupt a trajectory/artifact and observe failure.
- Runner timeout/resource tests.
- Deterministic repeated run equality.
- LLM judge calibration (when enabled): labeled agreement, bias slices, and unavailable model behavior.
- Report arithmetic tests retaining all denominators/states.
- End-to-end smoke dataset using fake LLM/tools.
- Safety tests proving forbidden tools cannot execute.

## Validation and Acceptance

- `AC-EVL-01`: Every schema rejects unknown fields, invalid hashes, duplicate case IDs, unsafe paths, and unsupported lifecycle states.
- `AC-EVL-02`: The Agent cannot access grader-only oracle files or fields.
- `AC-EVL-03`: Every deterministic grader rejects at least one targeted incorrect fixture and accepts a valid counterpart.
- `AC-EVL-04`: Trajectory grader accepts two different valid orderings and rejects forbidden/order-violating trajectories.
- `AC-EVL-05`: Missing/corrupt evidence cannot receive evidence-valid success.
- `AC-EVL-06`: Policy violation remains a failed hard gate even when other scores are high.
- `AC-EVL-07`: Repeated deterministic runs yield identical case/result hashes.
- `AC-EVL-08`: Reports preserve every case and correct pass/fail/blocked/inconclusive/error denominators.
- `AC-EVL-09`: Initial dataset meets reviewed category coverage floor and has a signed coverage/provenance manifest.
- `AC-EVL-10`: Optional LLM judge is absent from required deterministic gates and passes calibration or remains `INCONCLUSIVE`.
- `AC-EVL-11`: Paired comparison uses identical case versions/schedules and rejects unpaired inputs.
- `AC-EVL-12`: Evaluation cannot invoke forbidden/live tools.
- `AC-EVL-13`: Applicable regression/security/build gates pass on the final commit.
- `AC-EVL-14`: Independent review confirms no duplicated research authority or grader self-scoring.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-EVL-01 | `agent/src/evals/schema.py` | TEST-EVL-01 schema matrix | EVID-EVL-01 contract report | PROPOSED |
| REQ-EVL-02 | `schema.py`, dataset files | TEST-EVL-02 provenance/anti-cheat | EVID-EVL-02 dataset manifest | PROPOSED |
| REQ-EVL-03 | `runner.py` | TEST-EVL-03 isolation/lifecycle | EVID-EVL-03 run manifest | PROPOSED |
| REQ-EVL-04 | `graders/**` | TEST-EVL-04 grader falsification | EVID-EVL-04 grader report | PROPOSED |
| REQ-EVL-05 | trajectory grader | TEST-EVL-05 alternative-order matrix | EVID-EVL-05 trajectory results | PROPOSED |
| REQ-EVL-06 | `GraderResult` pipeline | TEST-EVL-06 caller override rejection | EVID-EVL-06 immutable results | PROPOSED |
| REQ-EVL-07 | optional judge adapter | TEST-EVL-07 calibration/unavailable | EVID-EVL-07 calibration report | PROPOSED |
| REQ-EVL-08 | `report.py` | TEST-EVL-08 arithmetic/retention | EVID-EVL-08 report bundle | PROPOSED |
| REQ-EVL-09 | `compare.py` | TEST-EVL-09 paired/unpaired matrix | EVID-EVL-09 comparison report | PROPOSED |
| REQ-EVL-10 | `agent/evals/datasets/**` | TEST-EVL-10 coverage review | EVID-EVL-10 coverage manifest | PROPOSED |
| REQ-EVL-11 | grader tests | TEST-EVL-11 mutation/falsification | EVID-EVL-11 focused test output | PROPOSED |
| REQ-EVL-12 | runner capability gate | TEST-EVL-12 forbidden-tool matrix | EVID-EVL-12 safety report | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/evals -q --tb=short
    python -m src.evals.cli validate-dataset agent/evals/datasets/smoke
    python -m src.evals.cli run \
      --dataset agent/evals/datasets/smoke \
      --deterministic \
      --output <evidence-dir>
    python -m src.evals.cli verify <evidence-dir>/experiment_manifest.json
    python -m src.evals.cli compare <baseline> <candidate> --paired
    python -m compileall -q agent/src/evals
    git diff --check

Run existing Agent/runtime, research evidence, security, and broad regression gates afterward.

## Idempotence, Rollback, and Recovery

Dataset and grader versions are immutable. Changing semantic content requires a new version/hash. Runs write to unique experiment IDs and atomically finalize manifests. Interrupted runs remain incomplete and cannot be compared as complete experiments.

Rollback removes the additive CLI/harness without changing Agent runtime or research evidence. Preserve completed experiment bundles for auditability.

## Observability and Evidence Artifacts

- `agent/research_evidence/agent_evals/contracts/`
- `agent/research_evidence/agent_evals/datasets/<dataset-hash>/coverage_manifest.json`
- `agent/research_evidence/agent_evals/experiments/<experiment-id>/`
- case results, verified traces, grader results, aggregate report, and manifest
- LLM judge calibration report only when enabled

Every aggregate metric links back to case-level evidence.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-EVL-01 — Benchmark rewards superficial output | High | High | adversarial grader/case review | Multi-dimensional source-bound graders |
| RISK-EVL-02 — Oracle leakage | Medium | Critical | reachability/contamination tests | Separate roots and protected metadata |
| RISK-EVL-03 — LLM judge bias/instability | High | High | calibration/repeats | Optional only; deterministic hard gates |
| RISK-EVL-04 — Dataset quantity masquerades as coverage | High | Medium | coverage manifest/review | Category floor plus case-quality checklist |
| RISK-EVL-05 — Research authority duplicated | Medium | Critical | architecture review | Adapter to existing event/artifact verifiers |
| RISK-EVL-06 — Aggregate hides failures | High | High | denominator tests | Preserve case states and hard gates |
| RISK-EVL-07 — Eval executes unsafe tools | Low | Critical | capability-gate adversarial tests | Research-only allowlist, no credentials |

## Progress

- [ ] Record accepted Stage 02 SHA and current evaluation inventory.
- [ ] Freeze schemas, anti-cheat boundary, and grader ownership.
- [ ] Implement isolated runner/source adapters.
- [ ] Implement and falsify deterministic graders.
- [ ] Calibrate optional judge or mark it unavailable.
- [ ] Author and independently review initial datasets.
- [ ] Run baseline/paired descriptive experiment.
- [ ] Complete full review, gates, evidence, and acceptance states.

## Surprises & Discoveries

- Observation: The repository already contains strong research evidence and replay primitives; the Agent eval harness should consume them rather than recreate them.
  Evidence: `agent/src/research_ledger/` and Alpha Genesis architecture inspection.
- Observation: Existing software tests are broad, but test pass counts do not answer Agent task/trajectory quality.
  Evidence: current test tree and CI workflow.

## Decision Log

- Decision: Use constraint-based trajectory grading instead of exact trace matching.
  Alternatives: Golden exact sequence; final-answer-only grading.
  Rationale: Multiple valid Agent paths can satisfy the same required operations and safety order.
  Date/Author: 2026-08-24 / plan author.
- Decision: Separate LLM judge from deterministic hard gates.
  Alternatives: One model-generated overall score.
  Rationale: Safety/evidence correctness must remain reproducible and source-bound.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, report grader defect-detection rates, dataset coverage, deterministic reproducibility, blocked domains, and remaining evaluation blind spots.

## Plan Revision Log

- 2026-08-24: Initial plan created.
