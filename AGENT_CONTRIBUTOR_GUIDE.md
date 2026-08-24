# Agent Contributor Guide

`AGENTS.md` is the repository’s sole normative agent-governance source. Read it
before planning or editing. Stage-specific requirements live only in the active
`.agent/execplans/<stage>/ExecPlan.md`; the template is an authoring aid, not
another authority.

## Orientation

- Backend and package code: `agent/`; frontend: `frontend/`; public wiki: `wiki/`.
- MCP and CLI entry points are `agent/mcp_server.py` and `agent/cli/`.
- Broker connector, mandate, order-gate, halt, and audit-ledger areas are safety
  critical. Preserve their fail-closed behavior.
- `CONTRIBUTING.md`, `SECURITY.md`, and the DCO remain applicable repository
  policies; community commits need the required sign-off.

## Local workflow

Start with `git status --short --branch` and `git diff --check`. Keep a narrow
scope, write defect-detecting tests before declaring success, and record exact
commands and results. Do not commit secrets, token caches, `.env` files, private
trading exports, generated local runs, or unsanitized market data.

Useful checks (when their dependencies are available):

```bash
python -m compileall -q agent/cli
python -m py_compile agent/api_server.py agent/mcp_server.py
pytest --ignore=agent/tests/e2e_backtest --ignore=agent/tests/test_e2e_harness_v2.py --tb=short -q
pytest agent/tests/test_sdk_order_gate.py agent/tests/test_mandate_enforcement.py -q
cd frontend && npm ci && npm run build
```

Do not place orders, invoke broker writes, authorize external accounts, start
externally reachable services, deploy, publish, change CI secrets, or rewrite
shared history without explicit authorization. For anything involving live/order
behavior, follow the stricter safety, evidence, and rollback rules in
`AGENTS.md`.
