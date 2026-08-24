# Close the loop from observed runtime failure to permanent regression protection

**Plan-ID:** AGS-AR-06  
**Status:** PROPOSED  
**Stage:** 06  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-05 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-05`  
**Depends-On:** AGS-AR-00 through AGS-AR-05 `COMPLETE`  
**Supersedes:** Manual, non-durable failure notes that are disconnected from evaluation datasets and recurrence detection  
**Target-Outcome:** A sanitized observed failure becomes a source-bound failure case, receives triage/root-cause evidence, enters a regression dataset, verifies a fix, closes, and automatically reopens on recurrence.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a production-like or evaluation trace with a real failure is not merely inspected and forgotten. It enters a controlled lifecycle:

    observed trace
      -> failure detection/classification
      -> cluster and triage
      -> sanitized FailureCase
      -> reviewed regression promotion
      -> candidate fix evaluation
      -> verified closure
      -> recurrence monitor/reopen

The shortest demonstration begins with one deterministic injected failure, promotes it to a sanitized regression case, proves the candidate fix passes while the base fails, closes the case, then replays the failure signature and observes `REOPENED`.

## Implementation Scope Contract

### Required behavior

- `REQ-CFL-01`: Define immutable schemas for `FailureObservation`, `FailureSignature`, `FailureCluster`, `FailureCase`, `TriageRecord`, `RegressionPromotion`, `FixVerification`, and `RecurrenceRecord`.
- `REQ-CFL-02`: Ingestion accepts only verified Stage 02 traces and referenced Stage 03/04 evidence; unverified caller summaries cannot create a trusted failure.
- `REQ-CFL-03`: Detection uses typed failure/recovery/eval states and explicit rules; LLM-assisted classification may suggest labels but cannot mint authoritative failure state.
- `REQ-CFL-04`: Failure signatures are stable, privacy-preserving fingerprints over typed semantics and bounded normalized metadata, not raw prompt content.
- `REQ-CFL-05`: Clustering preserves every observation and supports split/merge with an auditable decision log.
- `REQ-CFL-06`: A `FailureCase` lifecycle is closed: `NEW`, `TRIAGED`, `FIX_IN_PROGRESS`, `VERIFIED`, `CLOSED`, `REOPENED`, or `REJECTED`.
- `REQ-CFL-07`: Promotion to a regression dataset requires source completeness, sanitization, provenance/license, reproducibility, anti-cheat separation, acceptance oracle, and explicit human or policy approval.
- `REQ-CFL-08`: Sensitive/private observations cannot be promoted until a sanitized minimal reproducer is created and verified to preserve the failure.
- `REQ-CFL-09`: Fix verification proves the pre-fix/base reproduces the defect and the candidate passes the same frozen case without causing gate regressions.
- `REQ-CFL-10`: Closure requires evidence bound to candidate SHA, dataset/case/grader/policy hashes, reviewer, and date; a comment or merged commit alone is insufficient.
- `REQ-CFL-11`: Recurrence detection reopens a closed case when a verified new observation matches the frozen signature or accepted semantic matcher.
- `REQ-CFL-12`: Read-only APIs/CLI expose clusters, cases, evidence, lifecycle, and metrics without exposing secrets or enabling runtime actions.
- `REQ-CFL-13`: The system reports detection-to-triage, triage-to-case, case-to-fix, fix-to-close, recurrence, false-cluster, and regression-coverage metrics with complete denominators.
- `REQ-CFL-14`: Failure records and regression cases remain separate from mutable dashboards; persisted source records are append-only or versioned.
- `REQ-CFL-15`: The loop cannot automatically modify code, prompts, baselines, or release policy.
- `REQ-CFL-16`: Retention/deletion/export rules distinguish raw observations, sanitized cases, evidence, and aggregate metrics.

### Compatibility boundary

Existing traces, eval results, recovery results, and research evidence remain authoritative in their own planes. This stage stores references and derived failure-management records. It does not rewrite source traces or experiments.

Existing regression datasets are extended through versioned case addition, not in-place hidden mutation. Read-only API routes follow current GET-only research-delivery posture unless explicitly approved.

### Intentionally unsupported cases and failure behavior

- Automatic root-cause proof from an LLM is unsupported; suggestions remain non-authoritative.
- Fully automated regression promotion is unsupported by default.
- Raw production/private traces are not committed.
- Cross-organization multi-tenant access control is not claimed.
- Automated GitHub issue/PR creation is outside this stage unless separately authorized.
- A non-reproducible failure remains `TRIAGED`/`INCONCLUSIVE`, not closed.

### In-scope files

- `agent/src/failure_loop/__init__.py`
- `agent/src/failure_loop/schema.py`
- `agent/src/failure_loop/store.py`
- `agent/src/failure_loop/detect.py`
- `agent/src/failure_loop/signature.py`
- `agent/src/failure_loop/cluster.py`
- `agent/src/failure_loop/sanitize.py`
- `agent/src/failure_loop/promote.py`
- `agent/src/failure_loop/verify.py`
- `agent/src/failure_loop/recurrence.py`
- `agent/src/failure_loop/report.py`
- read-only API/CLI routes
- `agent/tests/failure_loop/`
- `agent/evals/datasets/production_regression/`
- evidence/fixture directories

### Out-of-scope files and behavior

No code/prompt auto-fix, write API, live deployment, issue/PR mutation, baseline update, broker action, or external incident-management integration.

## Current System Evidence

Prior stages provide verified traces, typed failures/recovery, evaluation cases, and protected gates. The repository already has append-only event/evidence patterns and content-addressed artifacts that can inform storage design. There is no unified Agent failure lifecycle from observation to regression case and recurrence reopening.

The system must not place failure-management records into the research event spine merely for convenience unless ownership and semantics are explicitly approved; a separate failure store can reference research evidence.

## Terminology

- **Observation:** One verified occurrence of a failure.
- **Signature:** Privacy-preserving reproducible identifier/matcher for semantically similar failures.
- **Cluster:** A reviewed grouping of observations.
- **Failure case:** A durable, actionable, sanitized unit with reproducer and acceptance.
- **Promotion:** Adding a reviewed case to a versioned regression dataset.
- **Fix verification:** Paired proof that base fails and candidate passes the same case/gates.
- **Recurrence:** A new verified observation matching a closed case.
- **Minimal reproducer:** Smallest sanitized setup that still triggers the same accepted failure semantics.

## Dependencies and Prerequisite Gate

- Stage 05 gates and baseline comparison operate.
- Stage 04 typed failures/fault evidence are stable.
- Privacy/redaction and data-retention policy is accepted.
- Failure-store ownership relative to research ledger is decided.
- At least one controlled end-to-end failure is available.
- Promotion/reviewer roles and lifecycle transitions are frozen.

## Invariants

- `INV-CFL-01`: Every failure record references verified source evidence.
- `INV-CFL-02`: Raw observations are never silently rewritten into sanitized cases.
- `INV-CFL-03`: Sanitization preserves the failure or promotion is rejected.
- `INV-CFL-04`: A case cannot close unless base-fail/candidate-pass is proven.
- `INV-CFL-05`: Closed cases reopen on verified recurrence.
- `INV-CFL-06`: LLM suggestions cannot set authoritative lifecycle state.
- `INV-CFL-07`: Every observation remains queryable after cluster split/merge.
- `INV-CFL-08`: Failure fingerprints do not include raw secrets/private content.
- `INV-CFL-09`: Promotion changes dataset version/hash.
- `INV-CFL-10`: Write actions require trusted local/maintainer path; public API remains read-only.
- `INV-CFL-11`: Failure loop cannot modify code, baselines, or gate policy.
- `INV-CFL-12`: Retention/deletion preserves required audit references or records a tombstone/legal deletion state.
- `INV-CFL-13`: Aggregate metrics retain denominators and lifecycle states.
- `INV-CFL-14`: Duplicate ingestion is idempotent.

## Deliverables

- `DEL-CFL-01`: Failure lifecycle schemas and append-only/versioned store.
- `DEL-CFL-02`: Verified ingestion/detection pipeline.
- `DEL-CFL-03`: Privacy-preserving signatures and cluster engine.
- `DEL-CFL-04`: Triage and decision records.
- `DEL-CFL-05`: Sanitization/minimal-reproducer pipeline.
- `DEL-CFL-06`: Controlled regression-promotion workflow.
- `DEL-CFL-07`: Base-fail/candidate-pass fix verifier.
- `DEL-CFL-08`: Recurrence monitor/reopen logic.
- `DEL-CFL-09`: Read-only API/CLI and dashboard-ready report.
- `DEL-CFL-10`: Retention/deletion/export implementation.
- `DEL-CFL-11`: End-to-end closed-loop demonstration/evidence.
- `DEL-CFL-12`: Operational runbook.

## Non-Goals and Prohibited Changes

- `NC-CFL-01`: Do not ingest unverified free-form reports as trusted failures.
- `NC-CFL-02`: Do not commit raw private traces/prompts.
- `NC-CFL-03`: Do not auto-generate and merge code fixes.
- `NC-CFL-04`: Do not auto-promote unsanitized cases.
- `NC-CFL-05`: Do not close from “issue resolved” text or a green unrelated test.
- `NC-CFL-06`: Do not lose observations during cluster merges/splits.
- `NC-CFL-07`: Do not use raw text embeddings alone as authoritative recurrence proof.
- `NC-CFL-08`: Do not hide rejected/non-reproducible cases.
- `NC-CFL-09`: Do not expose lifecycle writes through unauthenticated/public GET routes.
- `NC-CFL-10`: Do not let the failure loop alter accepted baselines/gates.
- `NC-CFL-11`: Do not claim production incident management or multi-tenant security.
- `NC-CFL-12`: Do not retain sensitive data without a declared retention purpose/limit.

## Architecture and Data Flow

    verified Trace / Eval / Recovery result
                    │
                    ▼
    FailureDetector (typed rules)
                    │
                    ▼
    FailureObservation (immutable)
                    │
          signature + safe features
                    ▼
    FailureCluster
        ├─ triage decision
        ├─ root-cause evidence/uncertainty
        └─ sanitization request
                    ▼
    Sanitized Minimal Reproducer
                    │ verify same failure
                    ▼
    FailureCase + approval
                    │
                    ▼
    versioned production_regression dataset
                    │
         base vs candidate gate run
                    ▼
    FixVerification
        ├─ candidate fails ─► remains open
        └─ base fails/candidate passes/no regressions
                    ▼
                  CLOSED
                    │
          new verified matching observation
                    ▼
                 REOPENED

## Interfaces and Schemas

`FailureObservationV1` includes observation ID, source refs/hashes, failure taxonomy, recovery state, safe normalized features, timestamps, code/model/tool/skill versions, privacy class, and ingestion hash.

`FailureCaseV1` includes lifecycle state, cluster/signature, reproducer refs, expected failure, acceptance oracle, sanitization verification, provenance, owner/reviewer, promotion ref, fix-verification refs, and revision history.

Store API examples:

    append_observation(observation) -> ObservationRef
    create_or_match_cluster(observation_ref) -> ClusterDecision
    record_triage(cluster_id, record) -> TriageRef
    promote_case(case_id, approval) -> RegressionPromotion
    verify_fix(case_id, base_ref, candidate_ref) -> FixVerification
    evaluate_recurrence(observation_ref) -> tuple[RecurrenceRecord, ...]

Lifecycle transitions are validated by a closed transition table and optimistic concurrency/version checks.

## Milestones

### Milestone 1 — Freeze lifecycle, store, and privacy boundaries

Define schemas, state transitions, source ownership, signatures, retention, and access. Build invalid-transition, duplicate-ingestion, and unverified-source tests.

### Milestone 2 — Ingest, detect, sign, and cluster failures

Implement verified ingestion and deterministic signature features. Support reviewed split/merge without losing observations. Evaluate clustering on labeled fixtures.

### Milestone 3 — Sanitize, promote, and verify fixes

Build minimal-reproducer verification and human/policy approval. Add promoted cases to a versioned dataset. Require paired base-fail/candidate-pass plus Stage 05 gates.

### Milestone 4 — Reopen recurrence and expose read-only operations

Implement recurrence matching, reopening, metrics, read-only routes/CLI, retention/deletion, and the complete demonstration.

## Detailed Tasks

1. Decide whether to reuse generic content-addressed artifact writer/store primitives while keeping failure semantics separate from research truth.
2. Define strict schemas and transition table.
3. Implement source verifier adapters for trace/eval/recovery evidence.
4. Implement idempotent ingestion keyed by source observation hash.
5. Define signature feature allowlist: taxonomy, operation/tool identity, normalized error code, safe stack/module fingerprint, lifecycle context, version hashes.
6. Exclude prompt/user/tool raw content and secrets.
7. Implement exact/semantic matcher layers with confidence and review states.
8. Implement append-only cluster membership decisions and split/merge records.
9. Implement triage records with root-cause evidence and uncertainty.
10. Implement sanitization workflow and equivalence verification against accepted failure semantics.
11. Implement promotion approval and versioned dataset writer.
12. Implement fix verifier that checks base failure, candidate pass, and no gate regression.
13. Implement closure/reopen transition.
14. Implement read-only API/CLI and escaped reports.
15. Implement retention classes, delete/tombstone behavior, and export.
16. Add end-to-end controlled failure demonstration.
17. Complete privacy, lifecycle, and independent review.

## Failure Semantics

- `FAILURE_SOURCE_UNVERIFIED`
- `FAILURE_DUPLICATE_OBSERVATION`
- `FAILURE_SIGNATURE_INVALID`
- `FAILURE_CLUSTER_AMBIGUOUS`
- `FAILURE_TRANSITION_INVALID`
- `FAILURE_SANITIZATION_LOST_REPRO`
- `FAILURE_PROMOTION_UNAPPROVED`
- `FAILURE_CASE_CONTAMINATED`
- `FAILURE_BASE_NOT_REPRODUCED`
- `FAILURE_CANDIDATE_NOT_FIXED`
- `FAILURE_GATE_REGRESSION`
- `FAILURE_RECURRENCE_MATCHED`
- `FAILURE_RETENTION_BLOCKED`
- `FAILURE_PRIVATE_DATA_REJECTED`

Ambiguous cluster match remains queued for review. A failed sanitization keeps the raw observation under its retention policy but does not create a regression case.

## Security, Privacy, and Threat Model

Threats include raw production data leakage, re-identification through signatures, malicious traces, root-cause hallucination, dataset poisoning, lifecycle tampering, unauthorized write APIs, and deletion-policy violations.

Controls:

- verify source artifacts/hashes;
- allowlisted safe signature fields;
- explicit privacy classes and retention;
- sanitize/minimize before promotion;
- human/policy approval;
- append-only/versioned state transitions;
- read-only public delivery;
- authorization on local/admin mutation paths;
- escaped rendering;
- no raw stack/environment secrets;
- audit deletion/tombstones;
- reject prompt content that tries to set state.

## Test Strategy

- Schema/state-transition matrix.
- Duplicate/idempotent ingestion.
- Unverified/tampered source rejection.
- Signature stability across redacted-equivalent observations.
- Signature non-leakage corpus.
- Labeled clustering precision/recall and ambiguity handling.
- Split/merge preservation.
- Sanitization equivalence and loss-of-reproducer rejection.
- Promotion approval/contamination/provenance tests.
- Base-fail/candidate-pass and false-fix tests.
- Closure and recurrence/reopen tests.
- Retention/deletion/export tests.
- Read-only API security/schema tests.
- End-to-end failure-to-regression-to-reopen demonstration.
- Mutation test that bypasses base reproduction or approval and must fail.

## Validation and Acceptance

- `AC-CFL-01`: Unverified/tampered source cannot create a trusted observation.
- `AC-CFL-02`: Duplicate ingestion creates one observation and idempotent ref.
- `AC-CFL-03`: Signatures are stable for semantically identical redacted failures and contain no seeded secret/raw prompt.
- `AC-CFL-04`: Cluster split/merge preserves every observation and decision.
- `AC-CFL-05`: Invalid lifecycle transitions are rejected.
- `AC-CFL-06`: Sanitized reproducer triggers the same accepted failure; otherwise promotion fails.
- `AC-CFL-07`: Promotion without approval/provenance/oracle/source completeness fails.
- `AC-CFL-08`: Closing requires base-fail/candidate-pass plus no Stage 05 regression.
- `AC-CFL-09`: A deliberately false fix remains open.
- `AC-CFL-10`: Verified recurrence reopens a closed case.
- `AC-CFL-11`: Public routes/CLI are read-only and redact private fields.
- `AC-CFL-12`: Retention/deletion/export behavior passes policy tests.
- `AC-CFL-13`: End-to-end controlled demonstration completes with immutable evidence.
- `AC-CFL-14`: Metrics preserve complete denominators.
- `AC-CFL-15`: Applicable broad gates pass and independent privacy/design review is clean.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-CFL-01 | `failure_loop/schema.py` | TEST-CFL-01 schema/transition matrix | EVID-CFL-01 contract report | PROPOSED |
| REQ-CFL-02 | `detect.py`, source adapters | TEST-CFL-02 source tamper matrix | EVID-CFL-02 ingestion report | PROPOSED |
| REQ-CFL-03 | typed detector | TEST-CFL-03 caller/LLM override rejection | EVID-CFL-03 detection report | PROPOSED |
| REQ-CFL-04 | `signature.py` | TEST-CFL-04 stability/privacy | EVID-CFL-04 signature report | PROPOSED |
| REQ-CFL-05 | `cluster.py` | TEST-CFL-05 split/merge/labeled set | EVID-CFL-05 cluster report | PROPOSED |
| REQ-CFL-06 | lifecycle/store | TEST-CFL-06 transition matrix | EVID-CFL-06 lifecycle history | PROPOSED |
| REQ-CFL-07 | `promote.py` | TEST-CFL-07 approval/provenance | EVID-CFL-07 promotion record | PROPOSED |
| REQ-CFL-08 | `sanitize.py` | TEST-CFL-08 repro equivalence | EVID-CFL-08 sanitization report | PROPOSED |
| REQ-CFL-09 | `verify.py` | TEST-CFL-09 base/candidate pairing | EVID-CFL-09 fix verification | PROPOSED |
| REQ-CFL-10 | closure logic | TEST-CFL-10 evidence-bound close | EVID-CFL-10 closure record | PROPOSED |
| REQ-CFL-11 | `recurrence.py` | TEST-CFL-11 recurrence/reopen | EVID-CFL-11 recurrence record | PROPOSED |
| REQ-CFL-12 | API/CLI/report | TEST-CFL-12 read-only/redaction | EVID-CFL-12 API report | PROPOSED |
| REQ-CFL-13 | metrics/report | TEST-CFL-13 arithmetic | EVID-CFL-13 metrics bundle | PROPOSED |
| REQ-CFL-14 | store | TEST-CFL-14 immutability/version | EVID-CFL-14 store verification | PROPOSED |
| REQ-CFL-15 | authority boundaries | TEST-CFL-15 no-auto-modification | EVID-CFL-15 scope review | PROPOSED |
| REQ-CFL-16 | retention module | TEST-CFL-16 retention/export matrix | EVID-CFL-16 policy report | PROPOSED |

| INV-CFL-01, INV-CFL-02, INV-CFL-03, INV-CFL-04, INV-CFL-05, INV-CFL-06, INV-CFL-07, INV-CFL-08, INV-CFL-09, INV-CFL-10, INV-CFL-11, INV-CFL-12, INV-CFL-13, INV-CFL-14 | Failure-loop invariants | TEST-CFL-01, TEST-CFL-02, TEST-CFL-03, TEST-CFL-04, TEST-CFL-05, TEST-CFL-06, TEST-CFL-07, TEST-CFL-08, TEST-CFL-09, TEST-CFL-10, TEST-CFL-11, TEST-CFL-12, TEST-CFL-13, TEST-CFL-14 | EVID-CFL-01, EVID-CFL-02, EVID-CFL-03, EVID-CFL-04, EVID-CFL-05, EVID-CFL-06, EVID-CFL-07, EVID-CFL-08, EVID-CFL-09, EVID-CFL-10, EVID-CFL-11, EVID-CFL-12, EVID-CFL-13, EVID-CFL-14 | PROPOSED |
| AC-CFL-01, AC-CFL-02, AC-CFL-03, AC-CFL-04, AC-CFL-05, AC-CFL-06, AC-CFL-07, AC-CFL-08, AC-CFL-09, AC-CFL-10, AC-CFL-11, AC-CFL-12, AC-CFL-13, AC-CFL-14, AC-CFL-15 | Acceptance gates | TEST-CFL-01, TEST-CFL-02, TEST-CFL-03, TEST-CFL-04, TEST-CFL-05, TEST-CFL-06, TEST-CFL-07, TEST-CFL-08, TEST-CFL-09, TEST-CFL-10, TEST-CFL-11, TEST-CFL-12, TEST-CFL-13, TEST-CFL-14, TEST-CFL-15 | EVID-CFL-01, EVID-CFL-02, EVID-CFL-03, EVID-CFL-04, EVID-CFL-05, EVID-CFL-06, EVID-CFL-07, EVID-CFL-08, EVID-CFL-09, EVID-CFL-10, EVID-CFL-11, EVID-CFL-12, EVID-CFL-13, EVID-CFL-14, EVID-CFL-15 | PROPOSED |

## Concrete Execution Commands

From repository root:

    python -m pytest agent/tests/failure_loop -q --tb=short
    python scripts/demo_agent_failure_loop.py \
      --scenario deterministic-timeout \
      --output <evidence-dir>
    python scripts/verify_failure_case.py <case-manifest>
    python -m src.evals.cli run \
      --dataset agent/evals/datasets/production_regression \
      --deterministic \
      --output <experiment-dir>
    python -m compileall -q agent/src/failure_loop
    git diff --check

Then run the applicable eval gate, security, API contract, backend, and frontend checks.

## Idempotence, Rollback, and Recovery

Observation ingestion and promotion are content-addressed/idempotent. Lifecycle writes use expected-version concurrency. Interrupted sanitization/promotion leaves no promoted case until atomic completion.

Rollback disables ingestion/routes and preserves source records. Promoted dataset versions remain readable. A mistaken cluster/triage decision is superseded by an auditable new decision, not deleted.

## Observability and Evidence Artifacts

- failure observations/signatures/clusters/cases as versioned records;
- sanitized reproducer bundle;
- promotion record and dataset version;
- base/candidate experiments and gate decisions;
- closure/recurrence records;
- end-to-end demo manifest;
- aggregate lifecycle metrics.

Raw private observation content is stored only under approved retention outside tracked repository artifacts.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-CFL-01 — Private data enters regression set | Medium | Critical | sanitization/privacy tests | Minimize, verify, approve |
| RISK-CFL-02 — False clustering | High | Medium | labeled set/review | Ambiguous state and split/merge |
| RISK-CFL-03 — False closure | Medium | Critical | base-fail/candidate-pass gate | Evidence-bound closure |
| RISK-CFL-04 — Recurrence missed | Medium | High | replay/labeled cases | Exact + reviewed semantic matcher |
| RISK-CFL-05 — Dataset poisoning | Low | Critical | provenance/approval | Controlled promotion |
| RISK-CFL-06 — Failure loop becomes auto-coder | Medium | High | scope/API review | No code/prompt/baseline writes |
| RISK-CFL-07 — Store duplicates research truth | Medium | High | ownership review | References only; separate semantics |

## Progress

- [ ] Record Stage 05 accepted SHA and source ownership decision.
- [ ] Freeze schemas, lifecycle, signatures, privacy, and retention.
- [ ] Implement verified ingestion/signature/clustering.
- [ ] Implement sanitization/promotion/fix verification.
- [ ] Implement recurrence/reopen and read-only delivery.
- [ ] Complete end-to-end demonstration.
- [ ] Run privacy/design review and broad gates.
- [ ] Bind evidence and update acceptance states.

## Surprises & Discoveries

- Observation: AGS already uses append-only and content-addressed patterns that can support failure-loop auditability, but failure records should not be mislabeled as research truth.
  Evidence: research-ledger/event/artifact architecture.
- Observation: A production-derived case is valuable only after sanitization preserves the failure and hides the oracle.
  Evidence: Stage 03 anti-contamination requirements.

## Decision Log

- Decision: Require human/policy approval for regression promotion.
  Alternatives: Auto-promote every failure.
  Rationale: Private/noisy/non-reproducible observations can poison the benchmark.
  Date/Author: 2026-08-24 / plan author.
- Decision: Require base-fail/candidate-pass for closure.
  Alternatives: Candidate pass only.
  Rationale: Otherwise the case may never have represented the claimed defect.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, report promoted-case yield, sanitization rejection, cluster quality, median lifecycle times, closure validity, recurrence behavior, and remaining manual burden.

## Plan Revision Log

- 2026-08-24: Initial plan created.
