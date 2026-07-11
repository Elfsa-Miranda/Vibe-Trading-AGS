# Alpha Genesis Known Limitations

- The canonical release manifest reports `empirical_status=not_established`.
  The tracked Activation plan/result/decision are preflight-only, invalidated,
  not bound to a tracked source-event ledger, and use a superseded Retriever
  treatment policy hash.
- Historical test commands in the acceptance document are not closed,
  event-bound test records and therefore cannot be replayed by the current
  release index as authoritative verification evidence.
- The A-share demo data is deterministic fixture data, not a live market data feed.
- PBO/DSR support is proxy-only in this phase and is reported as advisory.
- The forward tracking store is append-only for observations, but external backup and retention are operator concerns.
- Candidate promotion stops at research/paper-style states; production readiness is intentionally unreachable.
- Financial-quality PIT data adapters are not completed in this package.
- The read-only API exposes stored artifacts only; it does not start mining jobs.
- `mutmut` is not a native-Windows/local required gate. Run mutation testing separately from WSL/Linux if a CI mutation score gate is required.
