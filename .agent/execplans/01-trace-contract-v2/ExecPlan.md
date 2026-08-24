# Introduce a versioned hierarchical Trace Contract v2 without changing Agent behavior

**Plan-ID:** AGS-AR-01  
**Status:** PROPOSED  
**Stage:** 01  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Recorded Stage 00 accepted baseline  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-00`  
**Depends-On:** AGS-AR-00 `COMPLETE`  
**Supersedes:** The persisted shape of flat Agent trace records only after compatibility acceptance; it does not supersede research-ledger events  
**Target-Outcome:** Every Agent run can emit a deterministic, redacted, parent-linked trace/span graph with explicit version identity while legacy traces remain readable.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a developer can inspect one run and answer: which Agent run, turn, model call, tool call, skill, context operation, and artifact reference occurred; how they are related; when they started and ended; whether they succeeded; and which code, prompt, tool schema, and skill version produced them.

The shortest demonstration creates a deterministic fake run, reads its `trace.v2.jsonl`, verifies the graph, and renders:

    agent.run
      └─ agent.turn
          ├─ model.invoke
          └─ tool.execute

This stage defines and persists the contract. It does not yet instrument every runtime operation; Stage 02 does that.

## Implementation Scope Contract

### Required behavior

- `REQ-TRC-01`: Define `ags.trace.v2` with stable trace, span, event, status, identity, timing, resource, reference, and redaction fields.
- `REQ-TRC-02`: A trace contains one root span; every non-root span has a valid parent in the same trace or an explicit remote-parent marker.
- `REQ-TRC-03`: Trace IDs are 32 lowercase hexadecimal characters and span IDs are 16; caller-supplied malformed IDs are rejected.
- `REQ-TRC-04`: Span start/end operations are exception-safe, idempotent at close, monotonic in duration calculation, and persist a terminal status.
- `REQ-TRC-05`: JSONL persistence remains crash-tolerant, uses canonical serialized records, and offloads large fields to content-addressed sidecars within the trace root.
- `REQ-TRC-06`: Existing v1 `TraceWriter.read()` behavior remains available through a compatibility reader; v1 records are never silently reinterpreted as evidence-complete v2 spans.
- `REQ-TRC-07`: Sensitive fields pass through one redaction policy before persistence; raw private chain-of-thought is not stored.
- `REQ-TRC-08`: Exporters implement a narrow protocol; exporter failures are observable but fail soft and cannot alter the Agent result or research evidence.
- `REQ-TRC-09`: Trace records can bind `run_id`, `session_id`, code revision, provider/model identity, prompt hash, tool schema hash, skill hash, artifact references, and research-event references without copying authoritative evidence.
- `REQ-TRC-10`: A verifier detects orphan spans, cycles, duplicate IDs, invalid timestamps, missing terminal records, unsafe sidecars, hash mismatches, and schema-version confusion.

### Compatibility boundary

- Preserve the public behavior used by current callers of `src.agent.trace.TraceWriter`.
- Existing v1 traces remain readable. No in-place migration or rewrite is allowed.
- No research-ledger payload, event-store schema, Claim Matrix, factor evaluator, API response, CLI output, or frontend contract changes in this stage.
- New v2 symbols may remain internal until Stage 02 integration.

### Intentionally unsupported cases and failure behavior

- Distributed cross-process trace propagation is represented only by an optional remote-parent context; a collector/backend is not required.
- OTLP export is optional and disabled by default. Missing optional dependencies yield `EXPORTER_UNAVAILABLE`, not runtime failure.
- Arbitrary baggage and unbounded attributes are rejected by size/count limits.
- Wall-clock timestamps are descriptive; duration derives from a monotonic clock.
- v1-to-v2 conversion is an explicit lossy projection marked `source_schema=legacy`; it cannot mint missing parentage or identity.

### In-scope files

- `agent/src/observability/__init__.py`
- `agent/src/observability/schema.py`
- `agent/src/observability/context.py`
- `agent/src/observability/recorder.py`
- `agent/src/observability/redaction.py`
- `agent/src/observability/verify.py`
- `agent/src/observability/exporters/`
- `agent/src/agent/trace.py`
- `agent/tests/observability/`
- `docs/` or `wiki/` only for contract documentation whose release timing is accurate
- `pyproject.toml` only if an optional telemetry extra is approved

### Out-of-scope files and behavior

Do not instrument `AgentLoop` broadly, change scheduling, alter tool execution, add graders, add recovery logic, introduce production collectors, or change the research event store.

## Current System Evidence

The current `agent/src/agent/trace.py` writes one JSON record per line, flushes after append, offloads large text/tool results to sidecar files, and safely resolves sidecar paths inside the trace directory. It records event types such as start, message, thinking, tool call/result, answer, and end. It does not provide a typed trace/span hierarchy, parent IDs, operation semantics, or graph verification. The current Agent loop persists model thinking text; this stage must replace raw chain-of-thought persistence with bounded decision/action summaries or disabled content while retaining useful operational evidence.

Before activation, inspect all current `TraceWriter` call sites and tests. Record exact v1 behavior that must remain compatible.

## Terminology

- **Trace:** All telemetry for one logical Agent execution.
- **Span:** A timed operation inside a trace.
- **Event:** A point-in-time annotation attached to a span.
- **Root span:** The single top-level span representing the run.
- **Context propagation:** Carrying trace and parent-span identity across function/thread boundaries.
- **Exporter:** A sink receiving already-redacted records.
- **Telemetry Plane:** Runtime observation data; not authoritative research evidence.
- **Sidecar:** A separate content-addressed file for a large field.

## Dependencies and Prerequisite Gate

- AGS-AR-00 is `COMPLETE`.
- The active plan records the exact base SHA and baseline manifest reference.
- Current trace call sites and fixture formats are inventoried.
- A redaction threat model and compatibility matrix are accepted before coding.
- Adding OpenTelemetry packages requires a recorded dependency decision; the default implementation must work without them.

## Invariants

- `INV-TRC-01`: Telemetry never becomes a substitute for research-ledger evidence.
- `INV-TRC-02`: Exporter failure cannot change Agent output/status.
- `INV-TRC-03`: No raw secret or private chain-of-thought is persisted.
- `INV-TRC-04`: v1 traces remain readable.
- `INV-TRC-05`: Every persisted sidecar remains under the trace root and matches its hash.
- `INV-TRC-06`: Span IDs are unique within a trace and parent links are acyclic.
- `INV-TRC-07`: Closing a span twice does not emit two terminal closes.
- `INV-TRC-08`: Attribute limits prevent unbounded trace growth.
- `INV-TRC-09`: Redaction happens before every exporter receives data.
- `INV-TRC-10`: Runtime source outside declared observability/compatibility paths is unchanged.

## Deliverables

- `DEL-TRC-01`: Typed immutable trace/span/event schema and enums.
- `DEL-TRC-02`: Context manager and context propagation helpers.
- `DEL-TRC-03`: JSONL recorder with atomic/content-addressed sidecars.
- `DEL-TRC-04`: v1 compatibility facade and explicit legacy projection.
- `DEL-TRC-05`: No-op, JSONL, in-memory test, and optional OTLP exporters.
- `DEL-TRC-06`: Trace graph verifier and CLI inspection command or script.
- `DEL-TRC-07`: Redaction policy with field classifications and counters.
- `DEL-TRC-08`: Contract fixtures and adversarial test matrix.
- `DEL-TRC-09`: Versioned trace contract documentation and sample artifact.

## Non-Goals and Prohibited Changes

- `NC-TRC-01`: Do not instrument every AgentLoop branch; that belongs to Stage 02.
- `NC-TRC-02`: Do not persist raw reasoning or private chain-of-thought.
- `NC-TRC-03`: Do not make OTLP or an external observability vendor mandatory.
- `NC-TRC-04`: Do not store research scores/verdicts as telemetry authority.
- `NC-TRC-05`: Do not delete or rewrite v1 traces.
- `NC-TRC-06`: Do not accept caller-supplied trace statuses, code hashes, or evidence bindings as trusted without validation.
- `NC-TRC-07`: Do not allow arbitrary filesystem paths or symlink escapes for sidecars.
- `NC-TRC-08`: Do not swallow recorder corruption and report a verified trace.
- `NC-TRC-09`: Do not introduce global mutable trace context that leaks between concurrent runs.

## Architecture and Data Flow

    TraceContext(trace_id, current_span_id)
             │
             ▼
    TraceRecorder.start_span(operation, attributes)
             │ validates IDs, limits, redaction
             ▼
    SpanHandle ── events ──► canonical records
             │
             ├──► JsonlExporter ──► trace.v2.jsonl + sidecars
             ├──► InMemoryExporter (tests)
             ├──► NoopExporter
             └──► OptionalOtlpExporter (disabled by default)

    TraceVerifier
      ├─ validates schema/graph/timing
      ├─ reopens sidecars and hashes
      └─ emits a verification report

Research event references are IDs/hashes only. The recorder cannot mint or modify the referenced research event.

## Interfaces and Schemas

Prescribe these core interfaces, adjusted only through a recorded decision:

    class TraceOperation(StrEnum):
        AGENT_RUN = "agent.run"
        AGENT_TURN = "agent.turn"
        MODEL_INVOKE = "model.invoke"
        TOOL_EXECUTE = "tool.execute"
        SKILL_LOAD = "skill.load"
        CONTEXT_COMPACT = "context.compact"
        MEMORY_READ = "memory.read"
        MEMORY_WRITE = "memory.write"
        RECOVERY_ATTEMPT = "recovery.attempt"
        CUSTOM = "custom"

    class SpanStatus(StrEnum):
        UNSET = "unset"
        OK = "ok"
        ERROR = "error"
        CANCELLED = "cancelled"

    @dataclass(frozen=True)
    class TraceContext:
        trace_id: str
        span_id: str | None
        remote_parent: bool = False

    class TraceExporter(Protocol):
        def export(self, records: Sequence[RedactedTraceRecord]) -> ExportResult: ...
        def shutdown(self) -> None: ...

`TraceRecordV2` includes `schema_version`, `record_type`, IDs, operation, name, sequence, wall-clock timestamp, monotonic offset/duration, status, status code/message, bounded attributes, resource identity, references, redaction metadata, and optional sidecar descriptor.

Resource identity includes provider/model and code revision only when source-derived. Prompt/tool/skill identity is a content hash plus safe label, not unredacted full content.

## Milestones

### Milestone 1 — Freeze the contract and threat model

Inventory v1 call sites and fixtures. Write the v2 schema, field ownership table, operation taxonomy, sensitive-data classification, limits, and compatibility behavior. Validate schemas with fixtures before implementing runtime integration.

### Milestone 2 — Implement recorder, context, and verifier

Build immutable schemas, ID generation/validation, context management, JSONL recorder, sidecars, graph verifier, and no-op/in-memory exporters. Prove graph correctness and crash tolerance using deterministic tests.

### Milestone 3 — Preserve v1 behavior and add optional export

Adapt `TraceWriter` through a compatibility facade. Existing readers must still return legacy records. Add an explicit v2 path and optional OTLP adapter without importing optional dependencies on the default path.

### Milestone 4 — Adversarial hardening and contract evidence

Test redaction, traversal, symlink escape, malformed IDs, duplicate close, parent cycles, thread isolation, exporter failure, large attributes, truncated final line, and tampered sidecars. Produce a verified sample trace bound to the final commit.

## Detailed Tasks

1. Enumerate every current `TraceWriter` method and call site.
2. Add schema classes with strict construction-time validation and canonical JSON.
3. Implement cryptographically strong IDs; make deterministic generators injectable only for tests.
4. Define attribute key/value types and hard count/byte limits.
5. Implement context propagation with `contextvars`; provide an explicit copied context for worker threads.
6. Implement `SpanHandle` with `__enter__/__exit__`, explicit error recording, and idempotent close.
7. Write records through one redaction pipeline.
8. Reuse or safely migrate the sidecar path containment logic.
9. Add sequence numbers so append order can be checked after crashes.
10. Implement graph verification and report schema.
11. Implement v1 reader compatibility and loss-marked projection tests.
12. Add exporter failure counters/events without recursive export loops.
13. Make optional OTLP import lazy and feature-off by default.
14. Remove or disable raw `thinking` persistence from v2; document compatibility treatment for old traces.
15. Run focused tests, existing trace/AgentLoop tests, security tests, then the broad gate.

## Failure Semantics

Typed errors include:

- `TRACE_SCHEMA_INVALID`
- `TRACE_ID_INVALID`
- `SPAN_ID_DUPLICATE`
- `SPAN_PARENT_MISSING`
- `SPAN_GRAPH_CYCLE`
- `SPAN_ALREADY_CLOSED`
- `TRACE_ATTRIBUTE_LIMIT`
- `TRACE_SIDECAR_UNSAFE`
- `TRACE_SIDECAR_HASH_MISMATCH`
- `TRACE_RECORD_TRUNCATED`
- `EXPORTER_UNAVAILABLE`
- `EXPORTER_FAILED`
- `LEGACY_TRACE_INCOMPLETE`

Recorder/export failures are fail-soft relative to Agent execution but visible through local counters and a bounded fallback diagnostic. Verification failure is fail-closed for any claim that the trace is complete or valid.

## Security, Privacy, and Threat Model

Threats include prompt/tool secrets, private financial data, chain-of-thought exposure, malicious attribute keys, oversized payloads, traversal/symlink sidecars, forged evidence references, exporter exfiltration, and cross-run context leakage.

Controls:

- classify fields as safe metadata, sensitive content, or prohibited content;
- redact before buffering/export;
- prohibit raw chain-of-thought;
- cap record and attribute size;
- allowlist exporter destinations/configuration through trusted application config;
- do not let trace content configure an exporter;
- validate reference formats but do not claim referenced content exists unless separately verified;
- isolate `contextvars` and clear contexts at run end;
- never include environment variables by default.

## Test Strategy

- Schema unit tests for every field and boundary.
- Golden canonical serialization tests, with independent semantic checks before accepting updates.
- Property tests generating valid trees and malformed graphs.
- Crash tests with truncated final lines and sidecar write interruption.
- Thread isolation and copied-context tests.
- Exporter failure/non-recursion tests.
- Redaction tests seeded with representative key/token/credential patterns.
- Compatibility tests reading tracked v1 fixtures.
- Negative tests proving v1 projection remains marked incomplete.
- Static path-scope test preventing research-ledger changes.

A test that merely constructs a span is insufficient; tests must reopen persisted bytes and run the verifier.

## Validation and Acceptance

- `AC-TRC-01`: A deterministic sample run produces one root span and a valid parent-linked graph; verifier returns `PASS`.
- `AC-TRC-02`: Existing v1 fixtures remain readable with unchanged legacy semantics.
- `AC-TRC-03`: A v1 projection cannot be reported as a complete v2 trace.
- `AC-TRC-04`: Exporter failure leaves the deterministic Agent result byte-identical and records a bounded diagnostic.
- `AC-TRC-05`: Secrets and prohibited reasoning content do not appear in JSONL, sidecars, or exporter-captured records.
- `AC-TRC-06`: Traversal, symlink escape, tampered sidecar, duplicate ID, orphan, and cycle fixtures are rejected with exact codes.
- `AC-TRC-07`: Two concurrent trace contexts never exchange trace/span IDs.
- `AC-TRC-08`: Closing a span after an exception emits exactly one terminal error record.
- `AC-TRC-09`: Optional exporter absence does not break default import or runtime.
- `AC-TRC-10`: The complete diff does not modify research truth or Agent scheduling.
- `AC-TRC-11`: Focused and applicable broad regression gates pass on the final commit.
- `AC-TRC-12`: Contract/sample evidence is bound to final code revision and schema hash.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-TRC-01 | `agent/src/observability/schema.py` | TEST-TRC-01 schema matrix | EVID-TRC-01 contract fixture | PROPOSED |
| REQ-TRC-02 | `context.py`, `verify.py` | TEST-TRC-02 graph property matrix | EVID-TRC-02 verifier report | PROPOSED |
| REQ-TRC-03 | `schema.py` ID validators | TEST-TRC-03 malformed-ID matrix | EVID-TRC-03 pytest output | PROPOSED |
| REQ-TRC-04 | `recorder.py:SpanHandle` | TEST-TRC-04 exception/double-close | EVID-TRC-04 reopened trace | PROPOSED |
| REQ-TRC-05 | `recorder.py`, JSONL exporter | TEST-TRC-05 crash/sidecar matrix | EVID-TRC-05 crash report | PROPOSED |
| REQ-TRC-06 | `agent/src/agent/trace.py` | TEST-TRC-06 v1 compatibility | EVID-TRC-06 compatibility report | PROPOSED |
| REQ-TRC-07 | `redaction.py` | TEST-TRC-07 secret/CoT corpus | EVID-TRC-07 redaction report | PROPOSED |
| REQ-TRC-08 | `exporters/base.py` | TEST-TRC-08 exporter failure isolation | EVID-TRC-08 result equivalence | PROPOSED |
| REQ-TRC-09 | `schema.py:ResourceIdentity` | TEST-TRC-09 identity/reference validation | EVID-TRC-09 sample trace | PROPOSED |
| REQ-TRC-10 | `verify.py` | TEST-TRC-10 adversarial graph matrix | EVID-TRC-10 verification report | PROPOSED |

| INV-TRC-01, INV-TRC-02, INV-TRC-03, INV-TRC-04, INV-TRC-05, INV-TRC-06, INV-TRC-07, INV-TRC-08, INV-TRC-09, INV-TRC-10 | Trace invariants | TEST-TRC-01, TEST-TRC-02, TEST-TRC-03, TEST-TRC-04, TEST-TRC-05, TEST-TRC-06, TEST-TRC-07, TEST-TRC-08, TEST-TRC-09, TEST-TRC-10 | EVID-TRC-01, EVID-TRC-02, EVID-TRC-03, EVID-TRC-04, EVID-TRC-05, EVID-TRC-06, EVID-TRC-07, EVID-TRC-08, EVID-TRC-09, EVID-TRC-10 | PROPOSED |
| AC-TRC-01, AC-TRC-02, AC-TRC-03, AC-TRC-04, AC-TRC-05, AC-TRC-06, AC-TRC-07, AC-TRC-08, AC-TRC-09, AC-TRC-10, AC-TRC-11, AC-TRC-12 | Acceptance gates | TEST-TRC-01, TEST-TRC-02, TEST-TRC-03, TEST-TRC-04, TEST-TRC-05, TEST-TRC-06, TEST-TRC-07, TEST-TRC-08, TEST-TRC-09, TEST-TRC-10 | EVID-TRC-01, EVID-TRC-02, EVID-TRC-03, EVID-TRC-04, EVID-TRC-05, EVID-TRC-06, EVID-TRC-07, EVID-TRC-08, EVID-TRC-09, EVID-TRC-10 | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/observability -q --tb=short
    python -m pytest agent/tests/test_trace.py agent/tests/test_agent_loop.py -q --tb=short
    python -m pytest agent/tests/security -q --tb=short
    python scripts/verify_agent_trace.py <sample-trace-dir> --format json
    python -m compileall -q agent/src/observability agent/src/agent
    git diff --check

Then run the applicable backend regression command from `AGENTS.md`. Do not invent fixed pass counts before execution.

## Idempotence, Rollback, and Recovery

v2 traces write under a new schema-specific filename/directory and never overwrite v1. Same record IDs cannot be appended twice. Interrupted traces remain readable as incomplete and fail complete-verification.

Rollback disables v2 integration and returns callers to the v1 facade; v2 artifacts remain readable by versioned tooling. Optional exporters are removable without changing the base recorder.

## Observability and Evidence Artifacts

- `agent/research_evidence/agent_trace_v2/contract.json`
- `agent/research_evidence/agent_trace_v2/sample/<trace_id>/trace.v2.jsonl`
- `agent/research_evidence/agent_trace_v2/sample/<trace_id>/verification.json`
- `agent/research_evidence/agent_trace_v2/compatibility_report.json`

All are bound to schema hash and final commit. Raw sensitive test inputs remain generated fixtures and are not committed.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-TRC-01 — Telemetry becomes a second research truth | Medium | Critical | Source-ownership review/tests | References only; research ledger remains authority |
| RISK-TRC-02 — Sensitive content leaks | Medium | Critical | Corpus scan and exporter capture | Central pre-export redaction and prohibited fields |
| RISK-TRC-03 — Context leaks across concurrent runs | Medium | High | Thread/concurrency tests | `contextvars`, explicit propagation, teardown |
| RISK-TRC-04 — Compatibility breaks current UI/CLI | Medium | High | v1 fixture/call-site tests | Facade and additive v2 path |
| RISK-TRC-05 — Trace overhead affects behavior | Medium | Medium | Stage 02 benchmark later; focused microbench now | Bounded buffering, no-op exporter, fail-soft |
| RISK-TRC-06 — Optional dependency becomes mandatory | Low | High | import-without-extra test | Lazy optional import |

## Progress

- [ ] Record exact Stage 00 accepted base SHA and trace call-site inventory.
- [ ] Freeze contract, ownership, redaction, and compatibility decisions.
- [ ] Implement schema/context/recorder/exporter/verifier.
- [ ] Add v1 compatibility facade.
- [ ] Complete adversarial and concurrency tests.
- [ ] Perform independent design/security review.
- [ ] Run final regression gates and bind evidence.
- [ ] Update all acceptance states and retrospective.

## Surprises & Discoveries

- Observation: Current traces already implement useful crash-safe JSONL and safe sidecar resolution, so v2 should reuse proven behavior rather than replace it wholesale.
  Evidence: `agent/src/agent/trace.py` inspection.
- Observation: Current AgentLoop writes a `thinking` record, which conflicts with the v2 privacy boundary and requires an explicit compatibility decision.
  Evidence: `agent/src/agent/loop.py` inspection.

## Decision Log

- Decision: Introduce v2 additively and preserve v1 reads.
  Alternatives: Rewrite historical traces; break current readers.
  Rationale: Schema evolution must not falsify historical telemetry or create a risky big-bang migration.
  Date/Author: 2026-08-24 / plan author.
- Decision: Keep the base recorder vendor-neutral and make OTLP optional.
  Alternatives: Depend on one hosted tracing product; use only local JSONL forever.
  Rationale: The project needs portable evidence and optional industrial integration without vendor authority.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, measure compatibility, verification coverage, redaction findings, record overhead, and remaining instrumentation gaps.

## Plan Revision Log

- 2026-08-24: Initial plan created.
