# Establish enforceable Agent engineering governance and a reproducible baseline

**Plan-ID:** AGS-AR-00  
**Status:** ACTIVE
**Stage:** 00  
**Owner:** Repository maintainer / implementing agent  
**Created:** 2026-08-24  
**Last-Updated:** 2026-08-24  
**Base-Ref:** `codex/agent-reliability-main`
**Base-SHA:** `561fb5ddc9d778021da18083462b8583e60ee848`
**Depends-On:** NONE  
**Supersedes:** The agent-workflow portions of `AGENT_CONTRIBUTOR_GUIDE.md` only where this plan explicitly migrates them into root `AGENTS.md`; the guide otherwise remains valid  
**Target-Outcome:** Every substantial change is governed by a machine-validated, commit-bound ExecPlan and begins from a truthful, reproducible repository baseline.

This ExecPlan is a self-contained living specification governed by the repository-root `AGENTS.md`. `Progress`, `Surprises & Discoveries`, `Decision Log`, `Outcomes & Retrospective`, and `Plan Revision Log` must remain current.

## Purpose / Big Picture

After this stage, a new contributor or coding agent can enter the repository, discover one authoritative engineering contract, locate the active stage plan, reproduce the accepted baseline, and prove that a plan satisfies the repository’s required structure before runtime implementation begins.

The shortest observable demonstration is:

    python scripts/validate_execplans.py
    python scripts/capture_agent_baseline.py --output agent/research_evidence/agent_baseline/<sha>/baseline_manifest.json
    python scripts/verify_agent_baseline.py agent/research_evidence/agent_baseline/<sha>/baseline_manifest.json

All commands must exit `0`; the manifest must name the actual commit, commands, environment, results, and hashes rather than relying on prose.

## Implementation Scope Contract

### Required behavior

- `REQ-GOV-01`: A repository-root `AGENTS.md` is the single normative agent engineering contract and contains the complete ExecPlan protocol, safety boundaries, anti-shortcut rules, review gates, and completion protocol.
- `REQ-GOV-02`: Every planned Agent Reliability stage has a self-contained `.agent/execplans/<stage>/ExecPlan.md` with stable requirement and acceptance identifiers.
- `REQ-GOV-03`: `scripts/validate_execplans.py` deterministically rejects missing mandatory sections, invalid lifecycle states, duplicate identifiers, incomplete traceability rows, unresolved template placeholders in an `ACTIVE` or `COMPLETE` plan, and contradictory completion claims.
- `REQ-GOV-04`: A baseline capture tool records the exact Git SHA, dirty-tree state, Python/Node/platform versions, dependency-lock hashes, selected test commands, exit codes, durations, summary counts, and evidence-file hashes.
- `REQ-GOV-05`: Baseline verification reopens the manifest and artifacts, verifies hashes and schema, and refuses a manifest whose commit or declared working-tree state does not match the checkout unless an explicit read-only comparison mode is selected.
- `REQ-GOV-06`: CI runs plan validation and baseline-schema tests without invoking live providers, brokers, external writes, or private data.
- `REQ-GOV-07`: Existing contributor instructions are reconciled so that duplicated mandatory rules do not drift into two conflicting sources of truth.
- `REQ-GOV-08`: The stage creates a roadmap index that identifies exactly one next implementable plan and every dependency edge.

### Compatibility boundary

This stage must not alter runtime Agent, factor-research, evidence, API, frontend, broker, or evaluation behavior. Existing command entry points and persisted data formats remain unchanged. Existing `AGENT_CONTRIBUTOR_GUIDE.md` may be reduced to repo-orientation material and a pointer to `AGENTS.md`, but user-facing contributor policy in `CONTRIBUTING.md` remains intact.

### Intentionally unsupported cases and failure behavior

- Nested path-specific `AGENTS.md` files are not introduced in this stage; the root contract documents how they may later add stricter rules.
- The validator does not infer semantic correctness of a design. It enforces document structure, identifiers, state transitions, traceability completeness, and evidence declarations.
- A baseline test that cannot run because a dependency is absent is recorded as `BLOCKED`, never rewritten as `PASS`.
- A dirty tree is permitted only when explicitly recorded with the exact diff hash and file list; it cannot be called the accepted release baseline.

### In-scope files

- `AGENTS.md`
- `AGENT_CONTRIBUTOR_GUIDE.md`
- `.agent/README.md`
- `.agent/templates/ExecPlan.template.md`
- `.agent/execplans/**/ExecPlan.md`
- `scripts/validate_execplans.py`
- `scripts/capture_agent_baseline.py`
- `scripts/verify_agent_baseline.py`
- `agent/tests/governance/`
- `.github/workflows/agent-governance.yml`
- `agent/research_evidence/agent_baseline/README.md`
- `.gitignore` only for governance-generated scratch outputs

### Out-of-scope files and behavior

No file under `agent/src/`, `agent/api_server.py`, `agent/mcp_server.py`, `frontend/src/`, broker/order code, research-event payloads, factor logic, or current evaluation logic may change. CI repository settings, branch protection, required-check settings, and CODEOWNERS enforcement are documented recommendations only unless separately authorized.

## Current System Evidence

At plan-authoring time, the repository has `AGENT_CONTRIBUTOR_GUIDE.md` with safety instructions, test hints, high-risk surfaces, and rollback guidance, but no root `AGENTS.md`. The current CI workflow runs backend tests and frontend build/tests, while the repository default branch and workflow branch filters require explicit reconciliation during activation. The current Python project declares unit/integration markers and `coverage.fail_under = 0`, so this stage must record—not conceal—the actual baseline before later gates are tightened.

Before changing files, replace `Base-SHA` with:

    git rev-parse HEAD

Record:

    git status --short --branch
    git diff --check
    python --version
    node --version
    npm --version

The base evidence belongs in the active plan and generated manifest.

## Terminology

- **Normative:** Mandatory and authoritative for repository work.
- **ExecPlan:** A self-contained living specification for one substantial implementation stage.
- **Traceability:** An explicit mapping from a requirement to implementation, test, and evidence.
- **Baseline:** The observed state of the exact recorded checkout before a candidate change.
- **Evidence artifact:** A file whose content and hash support an acceptance conclusion.
- **Dirty tree:** A checkout with tracked or untracked modifications relative to `HEAD`.

## Dependencies and Prerequisite Gate

There is no prior stage. Activation requires:

1. Exact base SHA recorded.
2. Current tree state recorded.
3. Python 3.11+ available.
4. The repository’s existing test dependencies install successfully, or missing prerequisites are explicitly recorded as `BLOCKED`.
5. The maintainer confirms the root `AGENTS.md` filename and `.agent/execplans/` location.

The stage may not become `ACTIVE` with unresolved `TO_BE_CAPTURED` metadata.

## Invariants

- `INV-GOV-01`: Runtime behavior and runtime source files are byte-identical before and after this stage.
- `INV-GOV-02`: No live trading, external write, deployment, release, credential, or account action occurs.
- `INV-GOV-03`: A plan cannot be marked `COMPLETE` when a required acceptance row lacks passing evidence.
- `INV-GOV-04`: The baseline manifest cannot claim an unexecuted command.
- `INV-GOV-05`: Evidence hashes are computed from reopened bytes, not caller-supplied values.
- `INV-GOV-06`: The validator itself has positive and negative tests; it is not accepted merely because it parses the provided plans.
- `INV-GOV-07`: There is exactly one normative definition of ExecPlan requirements.

## Deliverables

### Runtime-neutral governance deliverables

- `DEL-GOV-01`: Root `AGENTS.md`.
- `DEL-GOV-02`: Eight stage-specific ExecPlans and one non-normative template.
- `DEL-GOV-03`: `.agent/README.md` roadmap and active-plan selection rules.

### Tooling deliverables

- `DEL-GOV-04`: Strict plan validator with a versioned validation schema.
- `DEL-GOV-05`: Baseline capture and verification tools.
- `DEL-GOV-06`: Governance CI workflow.

### Test and evidence deliverables

- `DEL-GOV-07`: Validator fixture matrix covering valid, missing-section, duplicate-ID, incomplete-traceability, false-complete, unresolved-placeholder, and malformed-state plans.
- `DEL-GOV-08`: Baseline tamper, wrong-commit, dirty-tree, missing-artifact, command-failure, and canonical-serialization tests.
- `DEL-GOV-09`: Commit-bound baseline manifest and concise Markdown summary.

## Non-Goals and Prohibited Changes

- `NC-GOV-01`: Do not instrument Agent runtime.
- `NC-GOV-02`: Do not add an evaluation harness, recovery engine, or fault injector.
- `NC-GOV-03`: Do not change thresholds to manufacture a clean baseline.
- `NC-GOV-04`: Do not edit existing test expectations or skip failing tests to make the baseline green.
- `NC-GOV-05`: Do not duplicate the normative ExecPlan specification into `PLANS.md`, the template, and `AGENT_CONTRIBUTOR_GUIDE.md`.
- `NC-GOV-06`: Do not write a hand-maintained “all tests pass” claim.
- `NC-GOV-07`: Do not configure repository settings or required checks without explicit authorization.

Accidental runtime changes are detected by a path allowlist and a base-to-head diff test. Duplicated normative text is detected by review and a documentation ownership test that requires the template to identify itself as non-normative.

## Architecture and Data Flow

    AGENTS.md
       │ defines required plan schema and completion semantics
       ▼
    .agent/execplans/*/ExecPlan.md
       │ parsed by
       ▼
    validate_execplans.py ──► machine-readable validation report
       │
       └── gates pull requests in agent-governance.yml

    checkout + environment + commands
       │
       ▼
    capture_agent_baseline.py
       │ writes canonical manifest and command artifacts
       ▼
    verify_agent_baseline.py
       │ reopens and hashes evidence
       └──► PASS / FAIL / BLOCKED / INCONCLUSIVE summary

The manifest is an evidence index, not a source of runtime truth. It records what was observed.

## Interfaces and Schemas

`validate_execplans.py` must expose:

    validate_plan(path: Path) -> PlanValidationResult
    validate_repository(root: Path) -> RepositoryPlanValidationResult
    main(argv: Sequence[str] | None = None) -> int

`PlanValidationResult` contains `path`, `plan_id`, `status`, `errors`, and `warnings`. Errors are stable typed codes such as `MISSING_SECTION`, `DUPLICATE_ID`, `MISSING_TRACEABILITY`, `FALSE_COMPLETE`, and `UNRESOLVED_PLACEHOLDER`.

The baseline manifest is versioned as `ags.agent-baseline.v1` and includes:

- repository and commit identity;
- tree status and diff hash;
- environment;
- dependency/lockfile hashes;
- commands with timestamps, exit codes, duration, stdout/stderr artifact references;
- result state;
- artifact content hashes;
- generator version.

The verifier never accepts caller-provided hashes without reopening the referenced files.

## Milestones

### Milestone 1 — Install the governance constitution

Add root `AGENTS.md`, the `.agent` directory structure, stage plans, and a concise roadmap. Reconcile `AGENT_CONTRIBUTOR_GUIDE.md` so it does not compete with the root contract. Proof is a path-scoped diff showing no runtime-source changes and a manual review against every mandatory section.

### Milestone 2 — Enforce plan structure

Implement the strict validator and adversarial fixtures. First demonstrate that malformed fixtures fail for the intended reason. Then validate every real stage plan. Proof is a JSON validation report containing zero errors for repository plans and expected error codes for every negative fixture.

### Milestone 3 — Capture a truthful baseline

Implement canonical baseline capture and verification. Execute focused and broad repository checks available in the environment. Preserve failures and blocked commands exactly. Proof is a tamper-evident manifest bound to `Base-SHA`, plus verifier tests showing modified evidence and wrong-commit use are rejected.

### Milestone 4 — Gate governance in CI

Add a deterministic, offline workflow that validates plans, runs governance tests, and uploads the validation report and baseline-schema test outputs. It must not require secrets. Proof is a successful workflow run on the final commit and an intentionally invalid fixture rejected by tests.

## Detailed Tasks

1. Add root `AGENTS.md`; ensure permanent safety rules and ExecPlan requirements are complete within the Codex instruction-size budget.
2. Add `.agent/README.md` with plan lifecycle, stage order, and “one active implementation plan per worktree.”
3. Add the eight plans and template.
4. Replace duplicated mandatory workflow prose in `AGENT_CONTRIBUTOR_GUIDE.md` with a short pointer while preserving repo shape and user-facing safety context.
5. Implement a Markdown parser that uses heading names and explicit metadata rather than brittle line numbers.
6. Validate identifier uniqueness both within and across plans.
7. Parse the traceability table and verify each required `REQ`, `INV`, and `AC` is referenced.
8. Enforce status-specific rules: placeholders allowed in `PROPOSED`, forbidden in `ACTIVE`/`COMPLETE`; all required acceptance states `PASS` for `COMPLETE`.
9. Implement canonical JSON output for validation and baseline manifests.
10. Capture stdout/stderr to separate artifacts; never infer success from text when exit code is nonzero.
11. Add tests using temporary repositories and tampered files.
12. Add CI with least permissions (`contents: read`) and artifact upload.
13. Run `git diff --check`, focused governance tests, compile checks, then the applicable existing backend/frontend baseline commands.
14. Update this plan with exact results and evidence hashes.

## Failure Semantics

- Parser or schema errors return nonzero with a typed list; they do not silently skip a plan.
- A command timeout is `BLOCKED` or `FAIL` according to whether the environment or repository behavior caused it; record the distinction.
- A missing tool is `BLOCKED`.
- A test failure is `FAIL`; it remains part of the baseline.
- A changed evidence file yields `EVIDENCE_HASH_MISMATCH`.
- A wrong checkout yields `BASE_SHA_MISMATCH`.
- Partial manifests are written only to a temporary path and never promoted as accepted evidence.
- Interrupted capture may be rerun; content-addressed command artifacts make retries idempotent.

## Security, Privacy, and Threat Model

Assets include repository instructions, evidence manifests, command logs, and local environment metadata. Threats include fabricated results, path traversal in artifact references, secrets in environment dumps, symlink escape, malicious plan Markdown, and CI artifact poisoning.

Controls:

- Collect an environment allowlist, not all environment variables.
- Redact tokens and credentials from command output before persistence while retaining a redaction count.
- Resolve every artifact path inside the configured evidence root.
- Reject symlinks that escape the root.
- Parse Markdown as data; never execute code blocks.
- Use read-only CI permissions.
- Do not upload the developer’s local baseline automatically.

## Test Strategy

- Unit tests for metadata, headings, identifiers, traceability, state transitions, and canonical serialization.
- Property tests for identifier order and whitespace invariance.
- Adversarial tests for duplicate headings, fake checkbox completion, malformed tables, Unicode lookalike identifiers, traversal paths, and hash replacement.
- Integration tests using a temporary Git repository for clean/dirty/wrong-commit cases.
- CI workflow lint and a local runner test where feasible.
- Regression assertion that no files under prohibited runtime paths changed.

Each negative test must assert the precise error code, proving the validator is not merely returning a generic failure.

## Validation and Acceptance

- `AC-GOV-01`: `python scripts/validate_execplans.py --format json` exits `0`; report lists all eight plans with zero errors. Evidence: `agent/research_evidence/governance/execplan_validation.json`.
- `AC-GOV-02`: Every invalid fixture fails with its expected typed code. Evidence: focused pytest output.
- `AC-GOV-03`: Baseline capture and verification succeed on the final checkout and the manifest names the exact SHA. Evidence: baseline manifest and verifier transcript.
- `AC-GOV-04`: Mutating one referenced artifact makes verification fail with `EVIDENCE_HASH_MISMATCH`. Evidence: adversarial pytest.
- `AC-GOV-05`: The base-to-head diff contains no prohibited runtime path. Evidence: `governance_diff_scope.txt`.
- `AC-GOV-06`: Governance CI passes without secrets or network-dependent tests. Evidence: workflow URL/run ID recorded in this plan.
- `AC-GOV-07`: Existing baseline failures, skips, warnings, and blocked checks are reported without being relabeled. Evidence: baseline summary.
- `AC-GOV-08`: Independent review finds no conflicting normative ExecPlan source. Evidence: review record.
- `AC-GOV-09`: All required repository commands that are runnable in the declared environment have final-commit results. Evidence: command artifacts.

## Requirement Traceability Matrix

| Requirement | Implementation | Test | Evidence | State |
|---|---|---|---|---|
| REQ-GOV-01 | `AGENTS.md` | TEST-GOV-01 contract-section test | EVID-GOV-01 root contract review | PROPOSED |
| REQ-GOV-02 | `.agent/execplans/**/ExecPlan.md` | TEST-GOV-02 repository-plan discovery | EVID-GOV-02 validation report | PROPOSED |
| REQ-GOV-03 | `scripts/validate_execplans.py` | TEST-GOV-03 adversarial fixture matrix | EVID-GOV-03 pytest transcript | PROPOSED |
| REQ-GOV-04 | `scripts/capture_agent_baseline.py` | TEST-GOV-04 temp-repo capture matrix | EVID-GOV-04 baseline manifest | PROPOSED |
| REQ-GOV-05 | `scripts/verify_agent_baseline.py` | TEST-GOV-05 tamper/wrong-SHA matrix | EVID-GOV-05 verifier transcript | PROPOSED |
| REQ-GOV-06 | `.github/workflows/agent-governance.yml` | TEST-GOV-06 workflow execution | EVID-GOV-06 workflow run | PROPOSED |
| REQ-GOV-07 | `AGENT_CONTRIBUTOR_GUIDE.md` | TEST-GOV-07 authority ownership check | EVID-GOV-07 documentation review | PROPOSED |
| REQ-GOV-08 | `.agent/README.md` | TEST-GOV-08 dependency-DAG validation | EVID-GOV-08 roadmap report | PROPOSED |

## Concrete Execution Commands

From repository root:

    git rev-parse HEAD
    git status --short --branch
    git diff --check
    python -m pytest agent/tests/governance -q --tb=short
    python scripts/validate_execplans.py --format json \
      --output agent/research_evidence/governance/execplan_validation.json
    python scripts/capture_agent_baseline.py \
      --output agent/research_evidence/agent_baseline/<sha>/baseline_manifest.json
    python scripts/verify_agent_baseline.py \
      agent/research_evidence/agent_baseline/<sha>/baseline_manifest.json
    python -m compileall -q agent/src agent/cli
    python -m py_compile agent/api_server.py agent/mcp_server.py

Run the existing backend and frontend commands from `AGENTS.md` when dependencies are available. Record exact results rather than embedding expected pass counts in advance.

## Idempotence, Rollback, and Recovery

Validation is read-only and rerunnable. Baseline capture writes to a temporary directory and atomically promotes only a complete manifest. Same-content reruns are idempotent; conflicting content for the same SHA is rejected unless written under a new attempt ID.

Rollback consists of reverting governance files and CI workflow. No runtime schema rollback is needed. Preserve accepted baseline artifacts for auditability; do not overwrite them during rollback.

## Observability and Evidence Artifacts

Expected tracked evidence:

- `agent/research_evidence/governance/execplan_validation.json`
- `agent/research_evidence/agent_baseline/<sha>/baseline_manifest.json`
- `agent/research_evidence/agent_baseline/<sha>/baseline_summary.md`

Large raw command logs may be CI artifacts rather than committed files. Every manifest reference includes SHA-256, size, media type, and relative path.

## Risks and Mitigations

| Risk | Likelihood | Impact | Detection | Mitigation |
|---|---:|---:|---|---|
| RISK-GOV-01 — Governance becomes prose-only ceremony | Medium | High | Plans validate but claims lack evidence | Make validation, traceability, and evidence gates executable |
| RISK-GOV-02 — Three sources of truth drift | Medium | High | Conflicting rule review | Keep `AGENTS.md` normative; template explicitly non-normative |
| RISK-GOV-03 — Baseline captures secrets | Low | High | Redaction and fixture tests | Environment allowlist, output redaction, local-only raw logs |
| RISK-GOV-04 — Existing failures are hidden | Medium | High | Compare raw exit codes and summaries | Preserve four-state results and raw evidence |
| RISK-GOV-05 — Validator is brittle to Markdown edits | Medium | Medium | Property/format tests | Parse headings/IDs semantically and version schema |
| RISK-GOV-06 — CI branch filters do not cover default branch | Medium | High | Workflow trigger audit | Record mismatch and explicitly align in authorized CI change |

## Progress

- [x] (2026-08-24) Replaced `Base-SHA` with `561fb5ddc9d778021da18083462b8583e60ee848`; Stage 00 worktree started clean on `codex/agent-reliability-00-governance`.
- [x] Governance constitution and all eight stage plans were installed by bootstrap commit `561fb5ddc9d778021da18083462b8583e60ee848`.
- [ ] Implement validator and adversarial tests.
- [ ] Implement baseline capture/verification and tamper tests.
- [ ] Add offline governance CI.
- [ ] Perform independent full-diff review.
- [ ] Run final commands and bind evidence.
- [ ] Mark every acceptance criterion with its real state.

## Surprises & Discoveries

- Observation: At plan-authoring time, the repository has an agent contributor guide but no root `AGENTS.md`.
  Evidence: Repository path inspection.
- Observation: The current workflow is filtered to `main` while the public default branch was observed as `codex/ags-v32-review-hardening`.
  Evidence: Repository metadata and `.github/workflows/test.yml`; verify again at activation because this is mutable.
- Observation: The user selected the current GitHub default commit `60c27c2d817523fa3f909a43f721a6b78fec1c59` as the program source base. Bootstrap is the single local commit `561fb5ddc9d778021da18083462b8583e60ee848` on top of that source.
  Evidence: local `git fetch personal codex/ags-v32-review-hardening`; integration branch history.
- Observation: The supplied ZIP's manifest had a stale entry for `BUNDLE_VALIDATION.json`. Its actual ZIP bytes were `2456` and SHA-256 `aa34088a874cb4b16a498dd7242b6def02102a279e235511649be4fe8298d082`, rather than the declared `2351` bytes and SHA-256 `1ec5c8057d82cd7013e71ce2cd42267a4bc3aa1fbaf7230364d3d70a0a68339f`.
  Evidence: reopened ZIP and post-extraction hash verification. The bootstrap manifest was repaired only for this entry; all fourteen entries, all eight plan hashes, and the validation report now verify.
- Observation: the Windows `python` app-execution alias is unavailable in this environment. The repository-supported interpreter must be discovered and recorded; no package installation is authorized merely to satisfy a baseline command.
  Evidence: `python --version` exited nonzero before implementation.

## Decision Log

- Decision: Use root `AGENTS.md` as the only normative ExecPlan specification.
  Alternatives: Add a separate normative `PLANS.md`; duplicate full rules in every plan.
  Rationale: The user requested a two-layer model, and one authority prevents drift. Stage plans remain self-contained while inheriting non-waivable rules.
  Date/Author: 2026-08-24 / plan author.
- Decision: Preserve non-passing baseline results rather than requiring a green baseline to start.
  Alternatives: Fix all failures first; omit failed checks.
  Rationale: A truthful baseline can contain failures; hiding them destroys comparison validity.
  Date/Author: 2026-08-24 / plan author.
- Decision: Repair the supplied bundle manifest's one stale validation-report size/hash entry locally before bootstrap.
  Alternatives: retain a knowingly invalid manifest; wait for a replacement ZIP.
  Rationale: the user instructed continuous execution after the discrepancy was reported. The repair is minimal, uses reopened ZIP bytes, leaves the validation report and every plan unchanged, and is fully recorded here.
  Date/Author: 2026-08-24 / implementing agent.

## Outcomes & Retrospective

Not started. At completion, compare actual governance coverage, validator defects found, baseline reproducibility, and remaining manual controls with the original purpose.

## Plan Revision Log

- 2026-08-24: Initial high-assurance plan created from repository inspection and official ExecPlan/agent-instruction guidance.
- 2026-08-24: Activated on bootstrap commit `561fb5ddc9d778021da18083462b8583e60ee848`; recorded GitHub source-base selection, bundle-manifest repair, and Python launcher discovery.
