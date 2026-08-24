# Agent baseline evidence

This directory stores reproducible local baseline manifests captured by
`scripts/capture_agent_baseline.py`. Each accepted capture is placed under a
directory named by the checkout SHA and includes a JSON manifest plus a concise
summary. Command logs may remain local or be attached to CI when they are too
large to track.

The manifest binds the commit, the exact non-evidence working-tree file list and
content fingerprint, selected dependency-file hashes, Python/Node/npm versions,
command exit codes and typed blocked reasons, aggregate state counts, and
SHA-256 hashes of its referenced logs and generated `baseline_summary.md`.
Reopen it with `scripts/verify_agent_baseline.py`; a changed commit, dirty-file
content, dependency, aggregate state, or referenced artifact is a failure,
never a passing baseline. Captures require at least one explicit `--command`.
The full controlled baseline-evidence root is excluded from the working-tree
fingerprint so sibling capture attempts do not invalidate one another. Command
timeouts default to `FAIL`; callers may select `--timeout-state BLOCKED` only
when the timeout is known to be an environmental prerequisite failure and both
`--blocker-reason` and `--blocker-owner` name that external condition.

Captured artifacts are evidence, not a source of secrets. The capture utility
redacts repository paths and secret-like values from command output, but callers
must still choose commands that do not print credentials.
