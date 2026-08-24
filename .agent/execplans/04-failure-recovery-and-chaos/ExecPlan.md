# Add typed failure semantics, safe recovery policies, and deterministic fault injection

**Plan-ID:** AGS-AR-04  
**Status:** PROPOSED  
**Stage:** 04  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-03 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-03`  
**Depends-On:** AGS-AR-00 through AGS-AR-03 `COMPLETE`  
**Supersedes:** Generic untyped tool/runtime error handling and ad-hoc retry behavior only after compatibility acceptance  
**Target-Outcome:** The Agent classifies failures, applies bounded side-effect-aware recovery, verifies repair outcomes, and proves behavior under a deterministic chaos matrix.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, an operator can distinguish a timeout from an invalid argument, a transient provider failure from a permanent policy violation, and a safely retryable read from a write whose side effect is unknown. The system can apply a frozen recovery policy, verify whether the task truly recovered, and retain the original failure plus every attempt.

The shortest demonstration runs one task under a frozen fault schedule:

    first read-tool call -> timeout
    second attempt -> succeeds
    final verifier -> recovered

and a second task:

    write-tool call -> timeout with side_effect_state=unknown
    blind retry -> prohibited
    reconciliation -> required
    final state -> escalated or safely resumed

## Implementation Scope Contract

### Required behavior

- `REQ-REL-01`: Define a closed, versioned failure taxonomy across `MODEL`, `TOOL`, `PLANNING`, `CONTEXT`, `RETRIEVAL`, `MEMORY`, `ARTIFACT`, `EVIDENCE`, `POLICY`, `RUNTIME`, and `RESOURCE`.
- `REQ-REL-02`: Replace generic tool-error strings at the Agent boundary with a typed `ToolExecutionResultV2` that records status, value/reference, error code/type, retryability, safe-to-retry, side-effect state, attempt, latency, and artifact references.
- `REQ-REL-03`: Tool metadata explicitly declares read/write class, idempotency semantics, replay safety, timeout policy, and optional reconciliation capability.
- `REQ-REL-04`: Recovery decisions are made by a frozen `RecoveryPolicy`, not by caller-provided flags or free-form model claims.
- `REQ-REL-05`: Retries are bounded by attempts, elapsed time, and resource budget; they use deterministic or recorded backoff/jitter and terminate in a typed state.
- `REQ-REL-06`: Unknown or partial side effects prohibit blind retry. The runtime must reconcile by idempotency key or operation-status query, compensate through an explicitly registered action, request human approval, or fail closed.
- `REQ-REL-07`: Every failure, recovery decision, attempt, verification, and terminal result is represented in Stage 02 traces and immutable evaluation evidence.
- `REQ-REL-08`: A deterministic `FaultInjector` can inject faults at named runtime boundaries without modifying production code paths or relying on sleeps/network flakiness.
- `REQ-REL-09`: Fault schedules are versioned, source-bound, scoped to one case/run, and cannot be configured by untrusted user/tool content.
- `REQ-REL-10`: Recovery success is independently verified against task/evidence/artifact acceptance, not inferred from “the retry returned.”
- `REQ-REL-11`: Planning loop/no-progress detection is explicit and bounded; recovery cannot create an infinite repair loop.
- `REQ-REL-12`: Existing successful tool behavior remains compatible, and legacy JSON tool results remain readable through an explicit adapter until callers migrate.
- `REQ-REL-13`: The evaluation harness reports recovery success, unrecovered failure, incorrect recovery, attempts, and time-to-recovery by failure class.
- `REQ-REL-14`: Chaos tests cover timeout, rate limit, transient server error, malformed output, missing/corrupt artifact, empty retrieval, stale/contradictory memory, partial result, context loss, provider disconnect, and concurrency/race conditions.

### Compatibility boundary

Preserve existing tool names, parameter schemas, successful result semantics, read-only parallel execution, write serialization, state-store behavior, and public API/CLI output. A compatibility adapter may project legacy JSON strings into typed results, but all new recovery decisions must consume typed semantics.

The current write-tool timeout behavior—which warns and awaits completion rather than killing unknown side effects—must not be weakened.

### Intentionally unsupported cases and failure behavior

- Tools without declared side-effect/idempotency semantics default to conservative `write_or_unknown`, `safe_to_retry=false`.
- Automatic compensation is unsupported unless a registered compensator has its own contract, authorization, and tests.
- Human approval is represented as `HUMAN_ACTION_REQUIRED`; this stage does not build a complete approval UI.
- Process-level hard crashes may leave recovery pending; checkpoint/resume beyond the current durable boundaries is not silently claimed.
- Real provider/broker chaos is not run in deterministic CI.

### In-scope files

- `agent/src/reliability/__init__.py`
- `agent/src/reliability/errors.py`
- `agent/src/reliability/result.py`
- `agent/src/reliability/policy.py`
- `agent/src/reliability/recovery.py`
- `agent/src/reliability/reconcile.py`
- `agent/src/reliability/faults.py`
- `agent/src/agent/tools.py`
- `agent/src/agent/loop.py`
- `agent/src/agent/trace.py` / observability integration
- tool definitions requiring metadata adapters
- `agent/src/evals/graders/recovery.py`
- `agent/tests/reliability/`
- `agent/evals/datasets/fault_injection/`
- CLI/script entry points for deterministic chaos runs

### Out-of-scope files and behavior

No live broker fault injection, arbitrary compensation, automated code changes, CI promotion decisions, online failure clustering, full HITL UI, or distributed workflow engine.

## Current System Evidence

The current `ToolRegistry.execute()` guarantees a JSON string but generally collapses exceptions into `{status: error, tool, error}`. The AgentLoop infers success from a broad output check. Read-only tools have bounded timeout behavior; write tools are not forcibly killed after timeout because their side-effect state may be unknown. Provider streaming has one retryable reset path. These are strong primitives but not a unified failure/recovery contract.

Stage 03 supplies the evaluator needed to distinguish real recovery from a returned result. Stage 02 supplies spans for attempts and recovery.

## Terminology

- **Failure taxonomy:** Closed identifiers describing where and why an operation failed.
- **Retryable:** Another attempt might succeed.
- **Safe to retry:** Another attempt cannot duplicate/compound an unsafe side effect under the declared contract.
- **Side-effect state:** `none`, `not_started`, `unknown`, `partial`, `committed`, `compensated`, or `failed`.
- **Reconciliation:** Querying durable operation state to determine what actually happened.
- **Compensation:** An explicit authorized action that semantically reverses a committed effect.
- **Recovery:** A bounded policy-controlled attempt to reach the original task acceptance.
- **Incorrect recovery:** Runtime reports success but independent acceptance fails.
- **Fault schedule:** Frozen instructions for injecting a defined fault at a defined boundary/attempt.

## Dependencies and Prerequisite Gate

- Stage 03 can run and grade deterministic cases.
- Stage 02 trace verifier captures attempts and terminal paths.
- All tools are inventoried for read/write, idempotency, timeout, and reconciliation semantics.
- A compatibility plan for legacy JSON results is accepted.
- Recovery budgets and no-progress rules are frozen before candidate experiments.
- Fault injection is disabled by default and impossible to activate from user input.

## Invariants

- `INV-REL-01`: Unknown side-effect state never triggers blind retry.
- `INV-REL-02`: Recovery attempts are finite.
- `INV-REL-03`: Policy/evidence failures are not retried into success.
- `INV-REL-04`: Recovery cannot erase the original failure.
- `INV-REL-05`: A successful retry is not a recovered task until independent verification passes.
- `INV-REL-06`: Fault injection is run-scoped and feature-off by default.
- `INV-REL-07`: Faults cannot leak into later tests/runs.
- `INV-REL-08`: Successful no-fault behavior remains equivalent.
- `INV-REL-09`: Tool side-effect metadata comes from trusted registration.
- `INV-REL-10`: Recovery trace includes policy version/hash and decision reason.
- `INV-REL-11`: Read-only parallelism and write serialization remain.
- `INV-REL-12`: Retry/backoff cannot exceed case/run resource budgets.
- `INV-REL-13`: Compensation is never inferred or improvised by the model.
- `INV-REL-14`: A recovery grader cannot accept caller-provided “recovered=true.”

## Deliverables

- `DEL-REL-01`: Failure taxonomy and typed errors.
- `DEL-REL-02`: `ToolExecutionResultV2` and legacy adapter.
- `DEL-REL-03`: Tool reliability metadata registry.
- `DEL-REL-04`: Frozen recovery policy and decision engine.
- `DEL-REL-05`: Reconciliation/compensation interfaces with conservative defaults.
- `DEL-REL-06`: Loop/no-progress detector.
- `DEL-REL-07`: Deterministic fault injector and schedule schema.
- `DEL-REL-08`: Recovery instrumentation and evaluator/grader.
- `DEL-REL-09`: Fault-injection dataset and chaos matrix.
- `DEL-REL-10`: Recovery benchmark/report with raw case evidence.
- `DEL-REL-11`: Compatibility and no-fault equivalence evidence.

## Non-Goals and Prohibited Changes

- `NC-REL-01`: Do not retry unknown writes.
- `NC-REL-02`: Do not mark a failure transient merely from exception text supplied by a tool/provider.
- `NC-REL-03`: Do not let the model choose unlimited attempts or override policy.
- `NC-REL-04`: Do not convert policy violations, bad evidence, or invalid arguments into retry loops.
- `NC-REL-05`: Do not inject faults using nondeterministic external outages in required tests.
- `NC-REL-06`: Do not make test-only branches inside production tools; inject through declared boundary adapters.
- `NC-REL-07`: Do not swallow original exceptions/evidence.
- `NC-REL-08`: Do not treat eventual tool response as proof of task recovery.
- `NC-REL-09`: Do not add automatic compensation for broker/payment/wallet/external writes.
- `NC-REL-10`: Do not expose fault-injection controls through public untrusted APIs.
- `NC-REL-11`: Do not weaken current timeout safeguards.
- `NC-REL-12`: Do not use sleeps as the primary timeout/race test mechanism when deterministic barriers/fake clocks are possible.

## Architecture and Data Flow

    Tool registration
      └─ ReliabilityMetadata
          ├─ access: read/write/unknown
          ├─ idempotency: none/keyed/inherent
          ├─ safe_to_retry rules
          ├─ timeout policy
          └─ reconciler/compensator refs

    Agent tool attempt
      ▼
    ToolExecutionResultV2
      ├─ success ──► continue
      └─ failure
          ▼
    FailureClassifier (closed taxonomy)
          ▼
    RecoveryPolicyV1
      ├─ do_not_retry ──► terminal/escalate
      ├─ retry ──► bounded next attempt
      ├─ reconcile ──► operation status
      ├─ compensate ──► registered, authorized only
      └─ human_action_required
          ▼
    Independent recovery verification
          ├─ recovered
          ├─ unrecovered
          └─ incorrect_recovery

    FaultInjector intercepts declared boundaries under a frozen run-local schedule.

## Interfaces and Schemas

Core types:

    class SideEffectState(StrEnum):
        NONE = "none"
        NOT_STARTED = "not_started"
        UNKNOWN = "unknown"
        PARTIAL = "partial"
        COMMITTED = "committed"
        COMPENSATED = "compensated"
        FAILED = "failed"

    @dataclass(frozen=True)
    class ToolExecutionResultV2:
        status: Literal["success", "error"]
        value: Any | None
        value_ref: str | None
        error_code: str | None
        error_type: str | None
        retryable: bool
        safe_to_retry: bool
        side_effect_state: SideEffectState
        attempt: int
        latency_ms: int
        artifact_refs: tuple[str, ...]
        warnings: tuple[str, ...]

    @dataclass(frozen=True)
    class RecoveryDecision:
        action: Literal[
            "retry", "do_not_retry", "reconcile",
            "compensate", "human_action_required", "abort"
        ]
        reason_code: str
        policy_version: str
        remaining_attempts: int
        remaining_budget_ms: int

`FaultScheduleV1` binds fault ID, boundary, target tool/model/operation, attempt predicate, fault payload, maximum injections, case/run ID, seed, and schedule hash.

## Milestones

### Milestone 1 — Freeze taxonomy and tool contracts

Inventory real errors and tool semantics. Define closed error codes, typed result, reliability metadata, and legacy adapter. Tests must prove conservative defaults and caller-override rejection.

### Milestone 2 — Implement bounded recovery and reconciliation

Build policy evaluation, attempt budgets, no-progress detection, retry/backoff, reconciliation hooks, and terminal states. Integrate without changing no-fault behavior.

### Milestone 3 — Implement deterministic fault injection

Create boundary adapters and frozen schedules. Support the required fault classes with fake clocks/barriers and ensure injection cannot escape its run.

### Milestone 4 — Prove recovery, non-recovery, and incorrect recovery

Add evaluator cases and independent verification. Run the chaos matrix, measure metrics by failure class, and preserve all failed attempts. Review write-side-effect paths separately.

## Detailed Tasks

1. Inventory exceptions/error JSON and current retry behavior across AgentLoop, providers, tools, retrieval, memory, artifacts, and evidence adapters.
2. Define stable taxonomy codes and ownership.
3. Implement typed result and strict legacy adapter; malformed legacy JSON becomes typed error.
4. Extend trusted tool registration with reliability metadata and validate contradictions at startup.
5. Implement failure classifier from trusted exception/result types, not free-form text alone.
6. Implement frozen recovery policy with closed decision table.
7. Implement fake clock and bounded backoff.
8. Add no-progress fingerprint using observable state/action/evidence references, not chain-of-thought.
9. Add reconciliation protocol and default unsupported implementation.
10. Add fault boundary protocol around model/tool/artifact/retrieval/memory/context operations.
11. Implement required deterministic faults and cleanup.
12. Instrument recovery spans and policy decisions.
13. Add recovery grader that reruns task acceptance/evidence checks.
14. Add no-fault equivalence tests and legacy adapter tests.
15. Add write-timeout/unknown-side-effect adversarial tests.
16. Run full fault dataset with repeated deterministic seeds where relevant.
17. Complete independent reliability/security review and broad regression.

## Failure Semantics

Representative codes:

- `MODEL.RATE_LIMIT`
- `MODEL.TRANSIENT_STREAM`
- `MODEL.EMPTY_RESPONSE`
- `TOOL.NOT_FOUND`
- `TOOL.INVALID_ARGUMENT`
- `TOOL.TIMEOUT`
- `TOOL.MALFORMED_RESULT`
- `TOOL.TRANSIENT_FAILURE`
- `TOOL.PERMANENT_FAILURE`
- `PLANNING.LOOP`
- `PLANNING.NO_PROGRESS`
- `CONTEXT.COMPACTION_LOSS`
- `RETRIEVAL.EMPTY`
- `MEMORY.STALE`
- `MEMORY.CONTRADICTORY`
- `ARTIFACT.MISSING`
- `ARTIFACT.CORRUPTED`
- `EVIDENCE.UNBOUND`
- `POLICY.FORBIDDEN_ACTION`
- `RUNTIME.BUDGET_EXCEEDED`
- `RESOURCE.TIMEOUT`

Terminal recovery states:

- `RECOVERED`
- `UNRECOVERED`
- `INCORRECT_RECOVERY`
- `HUMAN_ACTION_REQUIRED`
- `ABORTED_POLICY`
- `BUDGET_EXHAUSTED`
- `INCONCLUSIVE`

## Security, Privacy, and Threat Model

Threats include retry amplification, duplicate writes, model-induced policy override, fault-injection abuse, exception-content injection, compensation abuse, denial of service, and trace leakage.

Controls:

- closed trusted policy and metadata;
- maximum attempts/time/cost;
- no blind retry after unknown side effect;
- no public fault-control surface;
- run-local signed/hash-bound fault schedule;
- compensation allowlist and explicit authorization;
- redacted failure content;
- circuit breakers and no-progress detection;
- deterministic cleanup after injection;
- no real external-write credentials in tests.

## Test Strategy

- Unit tests for every taxonomy/decision row.
- Property tests proving attempts/budgets are bounded.
- Legacy-result adapter positive/negative matrix.
- No-fault semantic equivalence.
- Read timeout retry and success verification.
- Read permanent failure no-retry.
- Invalid argument no-retry.
- Rate-limit bounded retry/backoff.
- Write timeout with unknown side effect: no blind retry.
- Keyed-idempotent write reconciliation.
- Malformed result and partial artifact cases.
- Loop/no-progress termination.
- Fault run isolation and cleanup.
- Concurrency/race tests with deterministic barriers.
- Recovery grader false-success tests.
- Chaos dataset end-to-end.
- Mutation test that flips `safe_to_retry` or skips verification and must be caught.

## Validation and Acceptance

- `AC-REL-01`: Every registered tool has valid reliability metadata or conservative unknown defaults.
- `AC-REL-02`: Typed result round-trip and legacy adapter preserve successful semantics and reject malformed/conflicting input.
- `AC-REL-03`: Read-only retryable timeout recovers within frozen budget and independent task acceptance passes.
- `AC-REL-04`: Unknown-side-effect write timeout performs zero blind retries.
- `AC-REL-05`: Reconciliation distinguishes committed/not-started/unknown and chooses only allowed actions.
- `AC-REL-06`: Invalid argument, policy, and evidence failures are not retried.
- `AC-REL-07`: Retry/no-progress limits terminate all generated failure sequences.
- `AC-REL-08`: Original failure and every recovery attempt remain in trace/evidence.
- `AC-REL-09`: Deliberate false recovery is classified `INCORRECT_RECOVERY`.
- `AC-REL-10`: Every required fault class is deterministically injected and detected.
- `AC-REL-11`: Fault schedules cannot be activated by user/tool content or leak across runs.
- `AC-REL-12`: No-fault Agent behavior remains equivalent.
- `AC-REL-13`: Recovery report retains failure-class denominators and raw case refs.
- `AC-REL-14`: Applicable broad regression, security, and build gates pass.
- `AC-REL-15`: Independent review finds no unsafe retry/compensation path.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-REL-01 | `reliability/errors.py` | TEST-REL-01 taxonomy matrix | EVID-REL-01 taxonomy report | PROPOSED |
| REQ-REL-02 | `reliability/result.py`, `agent/tools.py` | TEST-REL-02 typed/legacy matrix | EVID-REL-02 compatibility report | PROPOSED |
| REQ-REL-03 | tool metadata registry | TEST-REL-03 registration validation | EVID-REL-03 inventory | PROPOSED |
| REQ-REL-04 | `reliability/policy.py` | TEST-REL-04 decision table | EVID-REL-04 policy report | PROPOSED |
| REQ-REL-05 | `recovery.py` | TEST-REL-05 bounded sequence property | EVID-REL-05 attempts report | PROPOSED |
| REQ-REL-06 | `reconcile.py`, loop integration | TEST-REL-06 unknown-write matrix | EVID-REL-06 write safety report | PROPOSED |
| REQ-REL-07 | observability integration | TEST-REL-07 trace completeness | EVID-REL-07 verified traces | PROPOSED |
| REQ-REL-08 | `faults.py` | TEST-REL-08 fault matrix | EVID-REL-08 chaos report | PROPOSED |
| REQ-REL-09 | `FaultScheduleV1` | TEST-REL-09 scope/override rejection | EVID-REL-09 schedule manifests | PROPOSED |
| REQ-REL-10 | recovery grader | TEST-REL-10 false-recovery matrix | EVID-REL-10 grader results | PROPOSED |
| REQ-REL-11 | no-progress detector | TEST-REL-11 loop termination | EVID-REL-11 termination report | PROPOSED |
| REQ-REL-12 | legacy adapter/equivalence | TEST-REL-12 no-fault regression | EVID-REL-12 equivalence report | PROPOSED |
| REQ-REL-13 | eval report extension | TEST-REL-13 metric arithmetic | EVID-REL-13 benchmark report | PROPOSED |
| REQ-REL-14 | fault dataset | TEST-REL-14 end-to-end chaos suite | EVID-REL-14 experiment bundle | PROPOSED |

| INV-REL-01, INV-REL-02, INV-REL-03, INV-REL-04, INV-REL-05, INV-REL-06, INV-REL-07, INV-REL-08, INV-REL-09, INV-REL-10, INV-REL-11, INV-REL-12, INV-REL-13, INV-REL-14 | Recovery invariants | TEST-REL-01, TEST-REL-02, TEST-REL-03, TEST-REL-04, TEST-REL-05, TEST-REL-06, TEST-REL-07, TEST-REL-08, TEST-REL-09, TEST-REL-10, TEST-REL-11, TEST-REL-12, TEST-REL-13, TEST-REL-14 | EVID-REL-01, EVID-REL-02, EVID-REL-03, EVID-REL-04, EVID-REL-05, EVID-REL-06, EVID-REL-07, EVID-REL-08, EVID-REL-09, EVID-REL-10, EVID-REL-11, EVID-REL-12, EVID-REL-13, EVID-REL-14 | PROPOSED |
| AC-REL-01, AC-REL-02, AC-REL-03, AC-REL-04, AC-REL-05, AC-REL-06, AC-REL-07, AC-REL-08, AC-REL-09, AC-REL-10, AC-REL-11, AC-REL-12, AC-REL-13, AC-REL-14, AC-REL-15 | Acceptance gates | TEST-REL-01, TEST-REL-02, TEST-REL-03, TEST-REL-04, TEST-REL-05, TEST-REL-06, TEST-REL-07, TEST-REL-08, TEST-REL-09, TEST-REL-10, TEST-REL-11, TEST-REL-12, TEST-REL-13, TEST-REL-14 | EVID-REL-01, EVID-REL-02, EVID-REL-03, EVID-REL-04, EVID-REL-05, EVID-REL-06, EVID-REL-07, EVID-REL-08, EVID-REL-09, EVID-REL-10, EVID-REL-11, EVID-REL-12, EVID-REL-13, EVID-REL-14 | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/reliability -q --tb=short
    python -m pytest agent/tests/evals -q --tb=short
    python -m src.evals.cli run \
      --dataset agent/evals/datasets/fault_injection \
      --deterministic \
      --output <evidence-dir>
    python scripts/verify_recovery_experiment.py <evidence-dir>/experiment_manifest.json
    python -m compileall -q agent/src/reliability agent/src/agent
    git diff --check

Then execute applicable Agent runtime, research evidence, security, frontend, and broad backend gates.

## Idempotence, Rollback, and Recovery

Recovery records use run/attempt IDs and cannot overwrite prior attempts. Fault schedules are immutable. Same idempotency key and operation contract produce a reconciliation result rather than a duplicate action.

Rollback disables the recovery engine and routes through the legacy adapter while retaining typed evidence. Tools added after this stage must still declare conservative metadata. No persisted research schema is migrated in place.

## Observability and Evidence Artifacts

- `agent/research_evidence/agent_reliability/tool_inventory.json`
- `agent/research_evidence/agent_reliability/recovery_policy.json`
- `agent/research_evidence/agent_reliability/fault_schedules/`
- `agent/research_evidence/agent_reliability/experiments/<id>/`
- recovery metrics, case results, verified traces, and no-fault equivalence manifest

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-REL-01 — Duplicate side effects | Medium | Critical | unknown-write tests/reconciliation | Conservative defaults, no blind retry |
| RISK-REL-02 — Retry storm | Medium | High | property/budget tests | Hard attempt/time/cost limits |
| RISK-REL-03 — False recovery | High | High | independent grader | Re-run original acceptance |
| RISK-REL-04 — Fault hooks affect production | Medium | Critical | feature-off/import/scope tests | Trusted run-local injector |
| RISK-REL-05 — Taxonomy becomes exception-text parsing | Medium | Medium | classifier tests/review | Trusted typed mappings |
| RISK-REL-06 — Compensation broadens authority | Low | Critical | allowlist/review | Unsupported by default; explicit approval |
| RISK-REL-07 — Legacy result migration breaks tools | Medium | High | adapter/no-fault equivalence | Additive compatibility period |

## Progress

- [ ] Record Stage 03 accepted SHA and tool/error inventory.
- [ ] Freeze taxonomy, typed result, metadata, and policy.
- [ ] Implement bounded recovery and reconciliation.
- [ ] Implement deterministic fault injector.
- [ ] Add recovery grader and chaos dataset.
- [ ] Complete no-fault equivalence and write-safety review.
- [ ] Run broad gates and bind evidence.
- [ ] Update all acceptance states and retrospective.

## Surprises & Discoveries

- Observation: Current write-tool timeout behavior already recognizes the unknown-side-effect problem and should be formalized rather than replaced.
  Evidence: `agent/src/agent/loop.py`.
- Observation: Current generic ToolRegistry errors lack enough semantics for safe recovery.
  Evidence: `agent/src/agent/tools.py`.

## Decision Log

- Decision: Separate `retryable` from `safe_to_retry`.
  Alternatives: One retry boolean.
  Rationale: A write may be transiently failed but unsafe to repeat.
  Date/Author: 2026-08-24 / plan author.
- Decision: Verify recovery with the Stage 03 task/evidence graders.
  Alternatives: Treat any successful retry response as recovered.
  Rationale: Recovery is an outcome claim and requires independent proof.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, report per-class recovery, incorrect recovery, boundedness, side-effect safety findings, and remaining manual escalation paths.

## Plan Revision Log

- 2026-08-24: Initial plan created.
