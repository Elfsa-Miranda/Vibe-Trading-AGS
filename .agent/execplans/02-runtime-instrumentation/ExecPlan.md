# Instrument the Agent runtime with complete, behavior-preserving spans

**Plan-ID:** AGS-AR-02  
**Status:** PROPOSED  
**Stage:** 02  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-01 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-01`  
**Depends-On:** AGS-AR-00 and AGS-AR-01 `COMPLETE`  
**Supersedes:** Ad-hoc flat runtime instrumentation only after equivalence acceptance  
**Target-Outcome:** Agent runs expose a verified end-to-end operation graph and resource usage without changing decisions, outputs, ordering, safety, or failure semantics.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a developer can replay a deterministic test run and inspect exactly how the Agent moved through run, turn, model, tool, skill, memory, context-compaction, and finalization operations. The trace includes parentage, outcome, duration, token usage, tool arguments/results after redaction, and artifact references.

The shortest demonstration executes the same deterministic scenario with tracing disabled and enabled, proves byte-equivalent Agent result and tool order, and verifies the complete trace graph.

## Implementation Scope Contract

### Required behavior

- `REQ-INS-01`: `AgentLoop.run()` creates exactly one `agent.run` root span and one `agent.turn` span per actual model iteration.
- `REQ-INS-02`: Every model invocation/retry creates a `model.invoke` span with provider/model identity, usage when provider-reported, stream reset events, and terminal status.
- `REQ-INS-03`: Every tool attempt creates a `tool.execute` span with tool identity, redacted validated arguments, attempt number, read/write classification, elapsed time, result status, and typed error metadata when available.
- `REQ-INS-04`: Parallel read-only tools inherit the correct run/turn context and remain isolated from sibling spans.
- `REQ-INS-05`: Skill load/application, memory read/write, context collapse/compaction, goal continuation, cancellation, content-filter handling, and finalization emit their prescribed operation spans/events.
- `REQ-INS-06`: Trace, run, session, call, artifact, and research-event references are correlated without copying or changing authoritative evidence.
- `REQ-INS-07`: Instrumentation can be disabled, sampled, or switched to no-op through trusted configuration; all modes preserve runtime behavior.
- `REQ-INS-08`: Telemetry failures and exporter latency cannot alter success/failure, tool ordering, retries, final answer, research artifacts, or state-store transitions.
- `REQ-INS-09`: Trace completeness verification understands all normal and abnormal terminal paths: success, cancellation, provider error, empty response, content-filter circuit breaker, max iterations, tool timeout, and uncaught error.
- `REQ-INS-10`: Runtime overhead is measured against a deterministic no-op scenario; the plan records thresholds before measurement and cannot relax them after seeing candidate results.
- `REQ-INS-11`: Existing legacy trace consumers continue to receive supported records until explicitly migrated.
- `REQ-INS-12`: The runtime records selected actions and observable decisions, not private chain-of-thought.

### Compatibility boundary

Preserve:

- Agent result schema/status/reason semantics.
- Existing tool execution ordering, parallel read batching, write serialization, timeouts, heartbeat/progress behavior, goal continuation, cancellation, context compaction, content-filter breaker, and state-store updates.
- Existing session and run directory layout unless an additive telemetry subdirectory is used.
- Existing API/CLI/frontend behavior.

Instrumentation hooks are internal and must not broaden public APIs unless explicitly approved. Current v1 trace compatibility remains.

### Intentionally unsupported cases and failure behavior

- External distributed collectors are not required.
- Provider token usage is recorded only when reported; it is `unavailable`, not estimated as authoritative usage.
- Private chain-of-thought is not captured.
- Unknown tools receive a typed result/span but are not made retryable.
- Background tasks outside an active run context may use a linked trace only through explicit propagation; implicit global attachment is forbidden.

### In-scope files

- `agent/src/agent/loop.py`
- `agent/src/agent/trace.py`
- `agent/src/agent/skills.py`
- `agent/src/agent/memory.py`
- `agent/src/agent/context.py`
- `agent/src/agent/progress.py`
- `agent/src/agent/tools.py` only for instrumentation adapters, not typed-result migration
- `agent/src/providers/` only for safe identity/usage extraction
- `agent/src/observability/`
- `agent/tests/agent_runtime_observability/`
- existing AgentLoop tests that require additive assertions

### Out-of-scope files and behavior

No recovery-policy redesign, tool result v2 migration, fault injector, grader/eval harness, research-event schema changes, live provider requirement, or UI dashboard.

## Current System Evidence

The current AgentLoop already:

- records start/messages/thinking/tool calls/tool results/answer/end;
- tracks provider-reported LLM token usage;
- retries one retryable provider stream failure;
- handles content-filter skips and circuit breaker;
- compacts context in several layers;
- executes consecutive read-only tools in parallel and write tools serially;
- gives read-only tools a bounded timeout and warns rather than kills timed-out write tools;
- emits progress and heartbeat events;
- marks state-store success/failure.

These branches are precisely where instrumentation can accidentally change behavior. Before coding, create a branch/terminal-path inventory and deterministic fixtures for each path.

## Terminology

- **Behavioral equivalence:** Same status, reason, final content, tool order/arguments, state transitions, and authoritative artifacts for the same deterministic inputs.
- **Instrumentation:** Recording runtime behavior without deciding or changing it.
- **Sampling:** Trusted configuration selecting which traces are retained.
- **Attempt span:** One actual model or tool execution try.
- **Terminal path:** How a run ends, including failure and cancellation.
- **No-op exporter:** An exporter that records nothing and should add minimal overhead.

## Dependencies and Prerequisite Gate

- Stage 01 trace contract and verifier pass.
- Stage 00 baseline and plan validation pass.
- A deterministic fake LLM and tool harness covers every terminal path named above.
- Behavior-equivalence fields and predeclared performance thresholds are recorded before integration.
- Current AgentLoop tests pass or existing failures are explicitly baselined.

## Invariants

- `INV-INS-01`: Tracing on/off yields equivalent deterministic runtime behavior.
- `INV-INS-02`: Read-only tool parallelism and write-tool serialization are unchanged.
- `INV-INS-03`: Telemetry exporter errors never escape into Agent result.
- `INV-INS-04`: A tool call is represented exactly once per attempt.
- `INV-INS-05`: Every started span has one terminal close, including exceptions/cancellation.
- `INV-INS-06`: Context from one concurrent run never appears in another.
- `INV-INS-07`: No raw chain-of-thought is persisted.
- `INV-INS-08`: State-store transitions remain authoritative and unchanged.
- `INV-INS-09`: Research-event and artifact references are not minted by telemetry.
- `INV-INS-10`: Instrumentation does not add model/tool retries.
- `INV-INS-11`: Sampling decisions come from trusted configuration, not user/tool content.
- `INV-INS-12`: Existing API/CLI/frontend contracts are unchanged.

## Deliverables

- `DEL-INS-01`: Run/turn/model/tool lifecycle instrumentation.
- `DEL-INS-02`: Skill/memory/context/goal/finalization instrumentation.
- `DEL-INS-03`: Explicit thread context propagation for parallel tools.
- `DEL-INS-04`: Instrumentation configuration with no-op default/fallback and bounded sampling.
- `DEL-INS-05`: Deterministic runtime scenario harness and equivalence comparator.
- `DEL-INS-06`: Terminal-path trace fixtures and verification report.
- `DEL-INS-07`: Performance/overhead benchmark report with predeclared limits.
- `DEL-INS-08`: Compatibility evidence for legacy trace consumers.

## Non-Goals and Prohibited Changes

- `NC-INS-01`: Do not refactor Agent planning or tool batching merely to simplify tracing.
- `NC-INS-02`: Do not change timeout/retry values.
- `NC-INS-03`: Do not add automatic recovery or fault injection.
- `NC-INS-04`: Do not log full prompts/results by default.
- `NC-INS-05`: Do not treat reasoning text as an action explanation.
- `NC-INS-06`: Do not add external network export to deterministic tests.
- `NC-INS-07`: Do not modify research evaluator decisions or events.
- `NC-INS-08`: Do not hide missing spans by having the verifier auto-create them.
- `NC-INS-09`: Do not weaken existing tests/snapshots to accommodate changed behavior.
- `NC-INS-10`: Do not accept performance thresholds chosen after the benchmark result.

## Architecture and Data Flow

    AgentLoop.run()
      └─ span agent.run
          ├─ span agent.turn[N]
          │   ├─ span context.compact? 
          │   ├─ span model.invoke[attempt]
          │   ├─ event model.stream_reset?
          │   ├─ span tool.execute[A] ─┐
          │   ├─ span tool.execute[B] ─┤ parallel siblings
          │   ├─ span skill.load?
          │   ├─ span memory.read/write?
          │   └─ event goal.continue?
          └─ event/run terminal status

    authoritative run state/artifacts
             ▲ references only
             │
    trace graph ── verified separately

Instrumentation is applied through a narrow helper/facade rather than scattering raw JSON writes across branches.

## Interfaces and Schemas

Add a runtime-facing facade such as:

    class AgentTelemetry:
        def run_span(...): ...
        def turn_span(...): ...
        def model_span(...): ...
        def tool_span(...): ...
        def event(...): ...
        def current_context(self) -> TraceContext | None: ...
        def copy_context(self) -> Context: ...

Configuration:

    @dataclass(frozen=True)
    class TelemetryConfig:
        enabled: bool = True
        sample_rate: float = 1.0
        capture_model_content: bool = False
        capture_tool_content: bool = False
        exporter: str = "jsonl"

Content flags are trusted operator config and still pass redaction. `capture_reasoning` must not exist.

A `RuntimeEquivalenceRecord` captures fields compared in on/off tests and hashes authoritative artifacts.

## Milestones

### Milestone 1 — Freeze behavioral fixtures

Before touching runtime code, implement deterministic scenarios for every terminal path and snapshot only semantic outputs. Run them against the base SHA and store a base equivalence manifest.

### Milestone 2 — Instrument run, turn, and model lifecycle

Add root/turn/model spans with exception-safe boundaries. Preserve provider retry, token accounting, and stream behavior. Prove on/off equivalence.

### Milestone 3 — Instrument tool, skill, memory, and context operations

Add tool attempt spans and explicit thread context propagation. Add bounded operation events for compaction, skill/memory, goal continuation, cancellation, and finalization. Prove no ordering/concurrency changes.

### Milestone 4 — Complete terminal-path and overhead acceptance

Verify all traces, inject exporter failures, execute concurrent runs, and measure no-op/JSONL overhead using predeclared limits. Produce final evidence report.

## Detailed Tasks

1. Enumerate each AgentLoop branch and map it to a span/event.
2. Add a single `AgentTelemetry` dependency, injected or constructed once per run.
3. Open root span only after run identity exists; ensure state creation failures are represented safely.
4. Wrap each actual iteration with a turn span.
5. Wrap each model call and retry separately; attach provider-reported usage and reset events.
6. Wrap tool invocation at the actual execution boundary, not only when the model requests it.
7. Propagate context into `ThreadPoolExecutor` and manual tool threads with copied context.
8. Attach redacted arguments/result descriptors and sidecar references through Stage 01 policies.
9. Replace v2 raw thinking records with action/decision metadata; keep legacy behavior only in the v1 compatibility path if required and documented.
10. Add spans/events for context compaction and goal continuation without exposing summaries unless explicitly safe.
11. Record terminal status once and verify state-store outcome matches trace status without making trace authoritative.
12. Add concurrent run tests and deliberately failing exporter.
13. Benchmark tracing disabled/no-op/JSONL over enough repetitions to bound noise.
14. Review all `try/finally` paths for unclosed spans and double close.
15. Run focused and broad tests.

## Failure Semantics

Instrumentation-specific errors:

- `TELEMETRY_CONTEXT_MISSING`
- `TELEMETRY_CONTEXT_LEAK`
- `TELEMETRY_EXPORT_FAILED`
- `TRACE_TERMINAL_MISMATCH`
- `TRACE_SPAN_INCOMPLETE`
- `TRACE_DUPLICATE_ATTEMPT`
- `TRACE_BEHAVIOR_DIVERGENCE`
- `TRACE_OVERHEAD_EXCEEDED`

Telemetry errors are recorded separately and do not replace the runtime failure. A terminal mismatch causes trace verification to fail, not the original Agent result to be rewritten.

## Security, Privacy, and Threat Model

New risks arise because instrumentation observes prompts, tools, memory, and summaries. Default content capture is off. Safe metadata includes content hashes, lengths, safe labels, status/error codes, and source references. Redaction occurs before worker queues and exporter buffers.

Test:

- secrets in nested tool arguments/results;
- sensitive memory entries;
- prompt injection attempting to set trace/export fields;
- malicious tool names/attribute keys;
- concurrent context leakage;
- sampling manipulation through user input.

Exporter endpoints and sampling config are trusted process configuration only.

## Test Strategy

- Deterministic equivalence tests with tracing off/on and exporter success/failure.
- Terminal-path integration matrix.
- Tool-order and parallelism timing/barrier tests.
- Context propagation/isolation tests across multiple runs and worker threads.
- Span-count/parentage assertions from reopened JSONL.
- Property tests over model/tool attempt sequences.
- Secret corpus scans.
- Performance benchmark with warmup, fixed workload, repetitions, median/p95, and raw samples.
- Existing AgentLoop, provider, tool, session, API/CLI tests.
- Negative test in which a required instrumentation point is removed or a malformed trace fixture is verified, proving completeness checks work.

## Validation and Acceptance

- `AC-INS-01`: Tracing off/on produces identical semantic Agent result, tool call sequence, state transitions, and authoritative artifact hashes for every deterministic scenario.
- `AC-INS-02`: Every normal and abnormal terminal path produces a verifier-accepted trace with one root and complete children.
- `AC-INS-03`: Parallel tools have correct parents and never share sibling context.
- `AC-INS-04`: Exporter failure produces the same Agent result and no additional tool/model attempts.
- `AC-INS-05`: No raw reasoning or seeded secret appears in persisted/exported trace content.
- `AC-INS-06`: Tool span count equals actual attempts, including retry/timeout cases.
- `AC-INS-07`: Model spans distinguish first call and provider retry.
- `AC-INS-08`: State-store outcome and trace terminal status correlate; deliberate mismatch is rejected by verifier.
- `AC-INS-09`: No-op and JSONL overhead remain within predeclared limits or result is `INCONCLUSIVE`; thresholds are not changed post hoc.
- `AC-INS-10`: Legacy trace consumers continue to pass.
- `AC-INS-11`: Applicable broad backend/frontend/security gates pass.
- `AC-INS-12`: Independent review confirms instrumentation has not changed scheduling, retries, safety, or research truth.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-INS-01 | `agent/src/agent/loop.py` run/turn scopes | TEST-INS-01 lifecycle matrix | EVID-INS-01 verified traces | PROPOSED |
| REQ-INS-02 | model invocation wrapper | TEST-INS-02 retry/usage matrix | EVID-INS-02 model-span report | PROPOSED |
| REQ-INS-03 | tool execution wrapper | TEST-INS-03 attempt/result matrix | EVID-INS-03 tool-span report | PROPOSED |
| REQ-INS-04 | copied context in parallel executors | TEST-INS-04 concurrency isolation | EVID-INS-04 concurrency report | PROPOSED |
| REQ-INS-05 | telemetry facade call sites | TEST-INS-05 operation coverage | EVID-INS-05 coverage map | PROPOSED |
| REQ-INS-06 | reference attributes | TEST-INS-06 correlation validation | EVID-INS-06 sample dossier | PROPOSED |
| REQ-INS-07 | `TelemetryConfig` | TEST-INS-07 mode equivalence | EVID-INS-07 equivalence report | PROPOSED |
| REQ-INS-08 | fail-soft facade | TEST-INS-08 exporter-failure injection | EVID-INS-08 failure report | PROPOSED |
| REQ-INS-09 | verifier terminal rules | TEST-INS-09 terminal matrix | EVID-INS-09 verification summary | PROPOSED |
| REQ-INS-10 | benchmark harness | TEST-INS-10 overhead benchmark | EVID-INS-10 raw/perf report | PROPOSED |
| REQ-INS-11 | compatibility facade | TEST-INS-11 legacy consumer regression | EVID-INS-11 compatibility output | PROPOSED |
| REQ-INS-12 | redacted action records | TEST-INS-12 CoT/secret absence | EVID-INS-12 privacy scan | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/agent_runtime_observability -q --tb=short
    python -m pytest agent/tests/test_agent_loop.py agent/tests/test_trace.py -q --tb=short
    python scripts/run_agent_trace_matrix.py --deterministic --output <evidence-dir>
    python scripts/compare_agent_runtime.py <off-manifest> <on-manifest>
    python scripts/benchmark_agent_telemetry.py --config <frozen-config>
    python -m compileall -q agent/src/agent agent/src/observability
    git diff --check

Then run applicable full backend, security, and frontend gates from root `AGENTS.md`.

## Idempotence, Rollback, and Recovery

Instrumentation is feature-configurable and additive. Turning it off returns the runtime to a no-op facade without changing call signatures. Trace files use run IDs and cannot overwrite another run.

Rollback removes integration call sites while retaining Stage 01 readers. Interrupted runs close via `finally` where possible; process crashes leave an explicit incomplete trace rather than a fabricated success.

## Observability and Evidence Artifacts

- `agent/research_evidence/agent_runtime_instrumentation/base_equivalence.json`
- `agent/research_evidence/agent_runtime_instrumentation/candidate_equivalence.json`
- `agent/research_evidence/agent_runtime_instrumentation/terminal_matrix.json`
- `agent/research_evidence/agent_runtime_instrumentation/overhead_report.json`
- sample verified traces and content hashes

Raw model/tool content is not committed.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-INS-01 — Instrumentation changes control flow | Medium | Critical | on/off equivalence | Narrow facade, `try/finally`, no policy logic |
| RISK-INS-02 — Parallel context is wrong | High | High | barrier/concurrency tests | Explicit copied context |
| RISK-INS-03 — Sensitive data capture | Medium | Critical | seeded-secret scans | metadata-only defaults, central redaction |
| RISK-INS-04 — Excess overhead | Medium | Medium | frozen benchmark | no-op mode, bounded records/sidecars |
| RISK-INS-05 — Span gaps on abnormal paths | High | High | terminal-path matrix | branch inventory and verifier |
| RISK-INS-06 — Legacy consumer breakage | Medium | Medium | compatibility tests | additive facade/migration |

## Progress

- [ ] Record accepted Stage 01 SHA and branch inventory.
- [ ] Freeze deterministic behavior fixtures and thresholds.
- [ ] Instrument run/turn/model lifecycle.
- [ ] Instrument tool/skill/memory/context lifecycle.
- [ ] Complete concurrency, failure, privacy, and equivalence tests.
- [ ] Run overhead benchmark.
- [ ] Complete independent review and broad gates.
- [ ] Bind final evidence and update acceptance states.

## Surprises & Discoveries

- Observation: The current runtime already separates read and write timeout behavior; instrumentation must preserve this distinction exactly.
  Evidence: `agent/src/agent/loop.py` tool invocation path.
- Observation: Existing token accounting can provide usage metadata without estimating missing provider usage.
  Evidence: current LLM usage artifact logic.

## Decision Log

- Decision: Prove behavior preservation with paired on/off deterministic runs.
  Alternatives: Rely on existing unit tests; inspect diff only.
  Rationale: Instrumentation bugs often change exception and concurrency behavior without breaking narrow tests.
  Date/Author: 2026-08-24 / plan author.
- Decision: Instrument actual execution attempts, not only requested tool calls.
  Alternatives: One span per model request.
  Rationale: Retries/timeouts and skipped/blocked calls must be distinguishable.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, report operation coverage, equivalence, overhead, privacy findings, and remaining uninstrumented boundaries.

## Plan Revision Log

- 2026-08-24: Initial plan created.
