# Harden, benchmark, document, and package the flagship Agent Reliability release

**Plan-ID:** AGS-AR-07  
**Status:** PROPOSED  
**Stage:** 07  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** Accepted AGS-AR-06 head  
**Base-SHA:** `TO_BE_RECORDED_AFTER_AGS-AR-06`  
**Depends-On:** AGS-AR-00 through AGS-AR-06 `COMPLETE`  
**Supersedes:** Unverified or hand-authored flagship metrics and release claims  
**Target-Outcome:** Vibe-Trading AGS ships a reproducible, security-reviewed, benchmarked Agent Reliability release whose public claims are generated from accepted evidence.

This ExecPlan is governed by root `AGENTS.md` and is a living document.

## Purpose / Big Picture

After this stage, a reviewer can clone the repository, execute a bounded flagship demonstration, inspect the trace/eval/failure loop, verify the release manifest, and reproduce all public benchmark claims from source evidence. The release demonstrates not merely “an Agent that runs,” but an Agent system that is observable, evaluated, failure-aware, recoverable, regression-gated, and continuously improved.

The shortest demonstration:

1. runs a representative task;
2. displays verified run/turn/model/tool spans;
3. injects a controlled failure;
4. shows bounded recovery or safe escalation;
5. runs the corresponding eval case;
6. promotes and replays a regression case;
7. verifies a release evidence manifest and generated README metrics.

## Implementation Scope Contract

### Required behavior

- `REQ-RLS-01`: Define a release profile with complete benchmark, fault, security, performance, context, memory, and failure-loop evidence and explicit claim boundaries.
- `REQ-RLS-02`: Expand the reviewed benchmark to at least 100 high-quality unique cases across declared capability/risk categories, including at least 20 sanitized production-derived or realistic incident-derived cases when available; insufficient coverage narrows claims rather than fabricating cases.
- `REQ-RLS-03`: Run controlled ablations for context-compaction strategies, memory on/off or retrieval policies, and any multi-agent architecture proposed for release.
- `REQ-RLS-04`: Multi-agent or additional architectural complexity is enabled in the flagship path only when paired evidence demonstrates a practically meaningful benefit under cost/latency/safety constraints.
- `REQ-RLS-05`: Execute prompt-injection, tool-output-injection, path/traversal, secret/redaction, unsafe action, authorization, artifact tamper, and evaluator-manipulation red-team suites.
- `REQ-RLS-06`: Execute concurrency, crash/restart, resource exhaustion, long-context, malformed-provider/tool, and repeated-run stability tests.
- `REQ-RLS-07`: Measure task/evidence-valid success, tool/argument/trajectory correctness, recovery/incorrect recovery, loop rate, trace completeness, latency, token/tool cost, stability, and failure-loop metrics with full denominators and uncertainty.
- `REQ-RLS-08`: A `ReleaseEvidenceManifestV1` reopens and verifies every accepted source artifact, binds final commit, dependencies, datasets, graders, policies, baseline, models/providers, prompts/tools/skills, and all public claims.
- `REQ-RLS-09`: README/wiki/demo metrics and badges are generated from the accepted release manifest; hand-edited metric values are rejected.
- `REQ-RLS-10`: Public wording distinguishes software capability, deterministic fixture evidence, live/stochastic evidence, research-only status, and known limitations.
- `REQ-RLS-11`: A reproducible three-minute flagship demo is deterministic by default, uses sanitized/public data, requires no live broker, and includes exact commands and expected observable checkpoints.
- `REQ-RLS-12`: Packaging/install/container/developer setup is tested from a clean environment and no optional observability/eval dependency breaks the base product.
- `REQ-RLS-13`: Release artifacts include SBOM/dependency audit, static analysis, type checks, secret scanning, license/provenance inventory, and rollback/runbook.
- `REQ-RLS-14`: Repository hygiene is finalized: stable branch strategy recommendation, accurate description/topics, CODEOWNERS/required-check recommendations, issue templates, and roadmap; repository-setting mutations remain explicitly authorized operations.
- `REQ-RLS-15`: The release state is `PASS`, `FAIL`, `BLOCKED`, or `INCONCLUSIVE`; any unresolved hard gate prevents a “production-ready” claim.
- `REQ-RLS-16`: Known limitations and unsupported cases are generated or cross-checked against plan outcomes and acceptance evidence.

### Compatibility boundary

Preserve research-only/no-live-authorization boundary. Maintain released public APIs and durable schemas according to the recorded compatibility contract. Unreleased internal interfaces may be simplified before release rather than carrying unnecessary compatibility branches.

Base installation must remain functional without optional OTLP/live-eval dependencies. Existing Vibe-Trading CLI/Web/MCP/finance behavior remains unless separately in scope and tested.

### Intentionally unsupported cases and failure behavior

- No profitability or live-trading claim.
- No universal Agent benchmark claim beyond declared datasets/capabilities.
- No production SLO claim without production data and operating environment.
- No cross-tenant enterprise control claim.
- No automated self-modifying Agent claim.
- If production-derived case target is unavailable, release may proceed with narrower “evaluated on synthetic/public incidents” wording only if hard gates pass.
- External hosted observability providers remain optional.

### In-scope files

- release profile/policy/manifests under Agent eval/evidence paths
- `scripts/build_agent_release_evidence.py`
- `scripts/verify_agent_release_evidence.py`
- `scripts/run_agent_flagship_demo.py`
- `scripts/render_agent_metrics.py`
- benchmark/red-team/ablation datasets
- clean-install/container/workflow configuration
- README/README_zh/wiki/docs relevant to released behavior
- issue/PR templates and optional CODEOWNERS recommendations
- security/release tests
- package metadata and optional extras where required

### Out-of-scope files and behavior

No live order placement, production deployment, package publication, GitHub release creation, branch/settings mutation, or external marketing claims without separate authorization. Do not rewrite unrelated upstream Vibe-Trading functionality.

## Current System Evidence

The current repository already presents a strong evidence-governed quantitative research system with extensive tests and documented v3.1/v3.2 phases. Earlier plans add Agent runtime trace, evaluation, recovery, CI gates, and failure loop. This stage integrates and hardens them into one honest flagship release rather than adding arbitrary features.

Current repository metadata, default branch, workflow triggers, documentation, and package setup must be re-inspected at activation because they are mutable.

## Terminology

- **Flagship:** The primary public demonstration and evidence bundle.
- **Release evidence manifest:** Canonical index that reopens every source supporting release claims.
- **Ablation:** Controlled comparison with one architecture feature removed or changed.
- **Red team:** Adversarial testing intended to violate safety/security/quality boundaries.
- **Production-derived:** Sanitized case originating in an observed real or production-like failure.
- **Claim scope:** Exact population/environment/capability to which a metric applies.
- **Clean-room install:** Testing from a fresh environment without developer caches or untracked state.

## Dependencies and Prerequisite Gate

- All prior plans are `COMPLETE`.
- Release candidate SHA is frozen.
- All Stage 05 required gates are configured/run or repository-setting limitations are explicitly `BLOCKED`.
- Dataset/provenance/license reviews are complete.
- Public claim wording and metric list are preregistered before final run.
- Security/release tooling availability is recorded.
- A release rollback owner/path is identified.
- No unresolved hard-gate finding remains.

## Invariants

- `INV-RLS-01`: Research-only and no-live-authorization boundaries remain.
- `INV-RLS-02`: Every public metric is generated from verified source evidence.
- `INV-RLS-03`: Hard failures cannot be hidden by aggregate averages.
- `INV-RLS-04`: Benchmark/result artifacts are immutable and commit-bound.
- `INV-RLS-05`: Optional extras do not break base installation/import.
- `INV-RLS-06`: Demo uses no real credentials/private data/external writes.
- `INV-RLS-07`: README/wiki claims do not exceed measured scope.
- `INV-RLS-08`: Multi-agent complexity requires measured benefit.
- `INV-RLS-09`: Failed, blocked, and inconclusive tests remain visible.
- `INV-RLS-10`: Security scans and dependency evidence are from the final candidate.
- `INV-RLS-11`: Clean install does not depend on developer-local files.
- `INV-RLS-12`: Release manifest verification reopens artifacts rather than trusting summaries.
- `INV-RLS-13`: Release docs identify known limitations and rollback.
- `INV-RLS-14`: Repository setting actions are not performed without authorization.
- `INV-RLS-15`: Final release decision is source-computed and non-caller-overridable.
- `INV-RLS-16`: Generated metric files are reproducible byte-for-byte from the manifest.

## Deliverables

- `DEL-RLS-01`: Frozen release profile and claim registry.
- `DEL-RLS-02`: Expanded reviewed benchmark and coverage/provenance manifest.
- `DEL-RLS-03`: Context, memory, retrieval, and architecture ablation reports.
- `DEL-RLS-04`: Agent security/red-team report.
- `DEL-RLS-05`: Reliability/concurrency/performance/stability report.
- `DEL-RLS-06`: Release evidence manifest builder/verifier.
- `DEL-RLS-07`: Generated metrics/badges and truthful README/wiki updates.
- `DEL-RLS-08`: Deterministic flagship demo and recorded walkthrough.
- `DEL-RLS-09`: Clean-install/package/container evidence.
- `DEL-RLS-10`: SBOM, dependency/license/security evidence.
- `DEL-RLS-11`: Operator/developer runbook and rollback.
- `DEL-RLS-12`: Repository-hygiene checklist and authorized settings handoff.
- `DEL-RLS-13`: Final release decision/report.
- `DEL-RLS-14`: Known limitations and future roadmap.

## Non-Goals and Prohibited Changes

- `NC-RLS-01`: Do not claim profitable alpha or authorize live trading.
- `NC-RLS-02`: Do not hand-edit metrics/badges.
- `NC-RLS-03`: Do not add cases merely to reach a count when they lack defect-detection value.
- `NC-RLS-04`: Do not enable multi-agent architecture without ablation evidence.
- `NC-RLS-05`: Do not omit failed/blocked/inconclusive evidence.
- `NC-RLS-06`: Do not lower release gates.
- `NC-RLS-07`: Do not publish private traces, secrets, or chain-of-thought.
- `NC-RLS-08`: Do not require a hosted vendor for the default demo.
- `NC-RLS-09`: Do not label fixture-only evidence as production.
- `NC-RLS-10`: Do not publish package/release/deploy or change settings without authorization.
- `NC-RLS-11`: Do not broaden APIs/schema solely for demo appearance.
- `NC-RLS-12`: Do not generate a polished dashboard that cannot trace numbers to source.
- `NC-RLS-13`: Do not declare “production-ready” with any unresolved hard gate.
- `NC-RLS-14`: Do not rewrite historical evidence to fit release schema.

## Architecture and Data Flow

    Final candidate SHA
       │
       ├─ software/security/build gates
       ├─ complete Agent eval/fault/regression profiles
       ├─ context/memory/architecture ablations
       ├─ failure-loop evidence
       └─ clean-install/demo artifacts
                    │
                    ▼
    ReleaseEvidenceManifestBuilder
      ├─ reopens source manifests/artifacts
      ├─ verifies hashes/relationships/final SHA
      ├─ computes approved public metrics
      ├─ evaluates hard/soft release policy
      └─ emits ReleaseEvidenceManifestV1
                    │
          ┌─────────┴─────────┐
          ▼                   ▼
    render metrics/docs   release verifier
          │                   │
          └──── identical source truth ────┘

Public docs never become the source of metrics.

## Interfaces and Schemas

`ReleaseClaimV1` includes claim ID, text template, scope, metric/formula, source refs, required gate, precision/rounding, caveats, and allowed publication surfaces.

`ReleaseEvidenceManifestV1` includes:

- final repository SHA/tree state;
- package/dependency/lock/SBOM hashes;
- release profile/policy;
- datasets/graders/baselines/models/providers/prompts/tools/skills;
- experiment and gate refs;
- security/build/install/demo refs;
- computed metrics with numerator/denominator/uncertainty;
- claims and caveats;
- known limitations;
- final state and reasons;
- generator/verifier versions.

Renderer accepts only a verified manifest and produces generated Markdown/JSON fragments. A check mode fails when committed fragments differ.

## Milestones

### Milestone 1 — Freeze claims, coverage, and release profile

Inventory every intended public statement. Map it to source evidence or remove/narrow it. Complete dataset coverage/provenance review and preregister final metrics/gates.

### Milestone 2 — Execute ablations, red team, and hardening

Run context/memory/retrieval/architecture ablations, security adversarial suites, concurrency/crash/resource/stability tests, and address findings without weakening gates.

### Milestone 3 — Build and verify release evidence

Implement manifest builder/verifier and generated metric renderer. Run all final-candidate gates and produce one source-complete manifest.

### Milestone 4 — Deliver reproducible demo, docs, install, and handoff

Run clean install/container/demo, generate public docs, complete runbooks/rollback/repository checklist, and issue final release decision. Publication remains separate authorization.

## Detailed Tasks

1. Inventory public README/wiki claims and map each to evidence.
2. Define benchmark category taxonomy and inspect every case for uniqueness, realism, provenance, anti-cheat, and defect value.
3. Add cases only through Stage 06 promotion or reviewed public/synthetic authoring.
4. Freeze final release profile and claim registry before running candidate.
5. Design context strategy ablation using the existing microcompact/collapse/summary modes.
6. Design memory/retrieval ablation with equal case schedules/budgets.
7. Compare single-agent baseline to any multi-agent candidate before enabling it.
8. Add prompt/tool-output injection, evaluator manipulation, path, redaction, unsafe-action, and authorization suites.
9. Run concurrency/race/crash/restart/resource/long-context/repetition tests.
10. Measure metrics with raw samples and confidence intervals.
11. Implement manifest builder that reopens all source evidence.
12. Implement independent verifier and tamper tests.
13. Implement generated README/wiki metric fragments and check mode.
14. Build deterministic three-minute demo with checkpoints and fallback offline fixtures.
15. Test package/base install plus optional extras in clean environments.
16. Generate SBOM, audit dependencies/licenses/secrets/static/type checks.
17. Prepare operator/developer runbook and rollback.
18. Re-inspect repository metadata/branch/workflow/settings needs and create an explicit authorized-action checklist.
19. Perform independent final review of code, tests, claims, statistics, security, and docs.
20. Run final release gate and update this plan.

## Failure Semantics

- `RELEASE_SOURCE_INCOMPLETE`
- `RELEASE_HARD_GATE_FAILED`
- `RELEASE_GATE_INCONCLUSIVE`
- `RELEASE_CLAIM_UNSUPPORTED`
- `RELEASE_CLAIM_SCOPE_MISMATCH`
- `RELEASE_METRIC_RENDER_DRIFT`
- `RELEASE_EVIDENCE_TAMPERED`
- `RELEASE_FINAL_SHA_MISMATCH`
- `RELEASE_DATASET_COVERAGE_INSUFFICIENT`
- `RELEASE_SECURITY_FINDING`
- `RELEASE_INSTALL_FAILED`
- `RELEASE_DEMO_FAILED`
- `RELEASE_OPTIONAL_DEPENDENCY_LEAK`
- `RELEASE_LICENSE_PROVENANCE_BLOCKED`
- `RELEASE_PUBLICATION_NOT_AUTHORIZED`

Any hard failure blocks a production-ready claim. Insufficient coverage narrows the claim or results in `INCONCLUSIVE`.

## Security, Privacy, and Threat Model

Threats include public leakage, supply-chain vulnerabilities, malicious fixtures, generated-doc injection, metric laundering, demo credential use, unsafe tool calls, action compromise, and repository-setting overreach.

Controls:

- source-bound manifest;
- final-SHA evidence;
- secret scans/redaction;
- SBOM/dependency/license review;
- safe synthetic/public demo;
- capability allowlists/no broker writes;
- generated-doc escaping and check mode;
- least-privilege workflows;
- immutable baseline/results;
- explicit publication/settings authorization.

## Test Strategy

- Complete Stage 05 release profile and Stage 04 chaos suite.
- Dataset coverage/provenance validation.
- Context/memory/retrieval/single-vs-multi ablations.
- Prompt/tool/evaluator injection red team.
- Path/symlink/artifact tamper/redaction tests.
- Concurrency, repeated race, crash/restart, resource exhaustion.
- Long-context retention and compaction-loss cases.
- Clean-room install on supported Python versions/platforms where CI permits.
- Base install without optional extras; each optional extra separately.
- Container build/run and deterministic demo.
- Manifest builder/verifier tamper/missing/stale-SHA tests.
- Generated metric drift test.
- Documentation link/command validation.
- Final full backend/frontend/security/type/static/dependency test stack.

## Validation and Acceptance

- `AC-RLS-01`: Every public claim maps to verified release evidence or is removed/narrowed.
- `AC-RLS-02`: Reviewed benchmark meets declared coverage target or release claim is explicitly narrower.
- `AC-RLS-03`: Context/memory/retrieval ablations are paired and source-complete.
- `AC-RLS-04`: Multi-agent is absent or proves predeclared practical benefit without hard-gate/cost violations.
- `AC-RLS-05`: Red-team suite has zero unresolved critical/high findings and all findings remain documented.
- `AC-RLS-06`: Concurrency/crash/resource/long-context/stability acceptance passes or is honestly inconclusive.
- `AC-RLS-07`: Final metrics retain denominators, uncertainty, failure states, and source refs.
- `AC-RLS-08`: Release manifest verifier reopens every source and rejects tamper/missing/stale evidence.
- `AC-RLS-09`: README/wiki metrics are generated and drift check passes.
- `AC-RLS-10`: Three-minute demo completes offline from a clean checkout with no credentials/external writes.
- `AC-RLS-11`: Base install works without optional observability/eval dependencies.
- `AC-RLS-12`: Clean install/container/full tests run on final candidate SHA.
- `AC-RLS-13`: SBOM/audit/license/secret/type/static evidence is complete.
- `AC-RLS-14`: Research-only/no-live boundary remains explicit.
- `AC-RLS-15`: Rollback/runbook and repository-settings handoff are complete.
- `AC-RLS-16`: Final release decision is source-computed; no unresolved hard criterion is non-pass.
- `AC-RLS-17`: Independent review signs off on claims, statistics, security, architecture, tests, and docs.
- `AC-RLS-18`: No publication/settings action is performed without explicit authorization.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-RLS-01 | release profile/claim registry | TEST-RLS-01 profile validation | EVID-RLS-01 release profile | PROPOSED |
| REQ-RLS-02 | benchmark datasets | TEST-RLS-02 coverage/provenance | EVID-RLS-02 coverage manifest | PROPOSED |
| REQ-RLS-03 | ablation configs/runner | TEST-RLS-03 paired ablations | EVID-RLS-03 ablation reports | PROPOSED |
| REQ-RLS-04 | architecture policy | TEST-RLS-04 single-vs-multi gate | EVID-RLS-04 comparison | PROPOSED |
| REQ-RLS-05 | red-team suites | TEST-RLS-05 adversarial matrix | EVID-RLS-05 security report | PROPOSED |
| REQ-RLS-06 | hardening suites | TEST-RLS-06 concurrency/crash/resource | EVID-RLS-06 reliability report | PROPOSED |
| REQ-RLS-07 | metric computation | TEST-RLS-07 arithmetic/source checks | EVID-RLS-07 metrics bundle | PROPOSED |
| REQ-RLS-08 | manifest builder/verifier | TEST-RLS-08 tamper/missing/stale | EVID-RLS-08 release manifest | PROPOSED |
| REQ-RLS-09 | metric renderer | TEST-RLS-09 generated drift | EVID-RLS-09 generated fragments | PROPOSED |
| REQ-RLS-10 | documentation claim checks | TEST-RLS-10 scope/wording review | EVID-RLS-10 claim matrix | PROPOSED |
| REQ-RLS-11 | flagship demo | TEST-RLS-11 clean offline run | EVID-RLS-11 demo manifest | PROPOSED |
| REQ-RLS-12 | packaging/install | TEST-RLS-12 clean matrix | EVID-RLS-12 install report | PROPOSED |
| REQ-RLS-13 | security/release tooling | TEST-RLS-13 scans/audits | EVID-RLS-13 security bundle | PROPOSED |
| REQ-RLS-14 | repo hygiene checklist | TEST-RLS-14 metadata/settings audit | EVID-RLS-14 handoff checklist | PROPOSED |
| REQ-RLS-15 | release decision | TEST-RLS-15 four-state/hard gate | EVID-RLS-15 final decision | PROPOSED |
| REQ-RLS-16 | limitations generator/check | TEST-RLS-16 evidence cross-check | EVID-RLS-16 limitations report | PROPOSED |

Supplemental linked identifiers: INV-RLS-01, INV-RLS-02, INV-RLS-03, INV-RLS-04, INV-RLS-05, INV-RLS-06, INV-RLS-07, INV-RLS-08, INV-RLS-09, INV-RLS-10, INV-RLS-11, INV-RLS-12, INV-RLS-13, INV-RLS-14, INV-RLS-15, INV-RLS-16, AC-RLS-01, AC-RLS-02, AC-RLS-03, AC-RLS-04, AC-RLS-05, AC-RLS-06, AC-RLS-07, AC-RLS-08, AC-RLS-09, AC-RLS-10, AC-RLS-11, AC-RLS-12, AC-RLS-13, AC-RLS-14, AC-RLS-15, AC-RLS-16, AC-RLS-17, AC-RLS-18.

## Concrete Execution Commands

From repository root, exact commands are finalized with the release profile. At minimum:

    python -m pytest agent/tests -q --tb=short
    cd frontend && npm ci && npx vitest run --reporter=verbose && npm run build
    python scripts/run_agent_eval_gate.py --profile release --candidate-sha <sha>
    python scripts/build_agent_release_evidence.py --candidate-sha <sha> --output <manifest>
    python scripts/verify_agent_release_evidence.py <manifest>
    python scripts/render_agent_metrics.py <manifest> --check
    python scripts/run_agent_flagship_demo.py --offline --output <demo-dir>
    python -m compileall -q agent/src agent/cli
    git diff --check

Also run the repository’s approved security/type/static/dependency/SBOM commands and clean-install/container matrix. Record exact versions/results; do not prewrite pass counts.

## Idempotence, Rollback, and Recovery

Release evidence is content-addressed and never overwritten. Rendering from the same verified manifest is deterministic. Demo output uses unique run IDs. Interrupted release runs remain incomplete and cannot yield an accepted manifest.

Rollback reverts release-facing code/docs and disables optional workflows/features while preserving evidence. Publication/tag/package rollback requires a separate authorized operational plan.

## Observability and Evidence Artifacts

- frozen release profile/policy/claims;
- dataset coverage/provenance;
- complete experiment/gate bundles;
- ablation/red-team/reliability/performance reports;
- install/container/demo manifests;
- SBOM/audit/license/secret evidence;
- `ReleaseEvidenceManifestV1`;
- generated metrics/docs fragments;
- final decision and rollback runbook.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-RLS-01 — Public claims exceed evidence | Medium | Critical | claim matrix/verifier | Source-bound generated claims |
| RISK-RLS-02 — Benchmark overfits | High | High | held-out/production-derived review | Diverse frozen cases and later additions |
| RISK-RLS-03 — Metric laundering | Medium | Critical | generated drift/source verification | Manifest-only renderer |
| RISK-RLS-04 — Optional deps break base | Medium | High | clean install matrix | Extras/lazy imports |
| RISK-RLS-05 — Demo bypasses real path | Medium | High | trace/path assertions | Use same production runtime/harness |
| RISK-RLS-06 — Security finding rushed past release | Medium | Critical | hard gate | Zero unresolved high/critical |
| RISK-RLS-07 — Multi-agent feature theater | Medium | Medium | ablation gate | Require measured benefit |
| RISK-RLS-08 — Publication/settings overreach | Low | Critical | authorization check | Separate explicit operational action |

## Progress

- [ ] Record Stage 06 accepted SHA and freeze release claims/profile.
- [ ] Complete benchmark coverage and provenance review.
- [ ] Run ablations, red team, and hardening suites.
- [ ] Build/verify release evidence and generated metrics.
- [ ] Complete clean install/container/demo.
- [ ] Complete security/license/SBOM and documentation.
- [ ] Perform independent final release review.
- [ ] Issue source-computed final decision and authorized handoff.

## Surprises & Discoveries

- Observation: The project’s strongest differentiation is the combination of quantitative evidence governance and Agent reliability; release claims should show both without conflating them.
  Evidence: current README architecture plus planned stages.
- Observation: Test counts are useful supporting evidence but not the primary flagship metric.
  Evidence: Stage 03–05 outcome/trajectory/recovery gate design.

## Decision Log

- Decision: Generate public metrics from a release evidence manifest.
  Alternatives: Manually update README; copy CI summary.
  Rationale: Public numbers must remain reproducible and source-bound.
  Date/Author: 2026-08-24 / plan author.
- Decision: Gate multi-agent complexity through ablation.
  Alternatives: Add multi-agent because it appears advanced.
  Rationale: Architecture is valuable only when measured outcomes justify cost/risk.
  Date/Author: 2026-08-24 / plan author.

## Outcomes & Retrospective

Not started. At completion, compare measured flagship outcomes with original goals, list claim limitations, summarize security/reliability findings, and identify the next evidence-driven roadmap.

## Plan Revision Log

- 2026-08-24: Initial plan created.
