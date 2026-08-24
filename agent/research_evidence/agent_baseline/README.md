# Agent baseline evidence

This directory stores reproducible local baseline manifests captured by
`scripts/capture_agent_baseline.py`. Each accepted capture is placed under a
directory named by the checkout SHA and includes a JSON manifest plus a concise
summary. Command logs may remain local or be attached to CI when they are too
large to track.

The manifest binds the commit, non-evidence working-tree state, selected
dependency-file hashes, command exit codes, and SHA-256 hashes of its referenced
logs. Reopen it with `scripts/verify_agent_baseline.py`; a changed commit, tree,
or referenced artifact is a failure, never a passing baseline.

Captured artifacts are evidence, not a source of secrets. The capture utility
redacts repository paths and secret-like values from command output, but callers
must still choose commands that do not print credentials.
