# Milestone 5 — Production Activation Candidate Factory V1

## Outcome

`ProductionActivationCandidateFactoryV1` is a narrow adapter over existing
production capabilities. It does not implement a parser, scorecard, execution
engine, Decision runner, ledger, or artifact repository.

The shared call boundary is:

```text
AlphaFoundrySearch
  -> FactorIdentityService
  -> ProductionCandidateDAGEvaluatorFactoryV1
       -> ProductionCandidateEvaluatorFactoryV1
  -> QualityDecisionV3Service
  -> existing TrialTerminated / TrialTerminalDossierRecorded events
```

Both arms use the same class and production component instances. Frozen
contract, snapshot, grammar, candidate budget, compute budget and evaluator
policy are reachable only through one
`ProductionActivationRunInputBundleV1Registered` reference. The arm request
does not expose independent overrides. The retriever policy hash is the only
scientific policy difference.

## Public result boundary

The public arm result contains only:

- arm-started event hash;
- retriever authority event refs;
- trial-terminal event refs;
- QualityDecision v3 event refs;
- resource artifact ref;
- arm-completed event hash.

It has no IC, score, yield, decision, metric, or success-count field. Formula,
callable, import-path, report JSON, worker summary and caller decision truth are
rejected by the closed request schema.

## Fail-closed compatibility audit

The binding event currently records two repository facts:

- `IDENTITY_TERMINAL_DOSSIER_PRODUCER_UNAVAILABLE`: the canonical identity
  service closes invalid/duplicate trials, while the existing production
  dossier builder is entered through the evaluator and cannot dossier a trial
  that has no valid factor definition.
- `QUALITY_DECISION_V3_EVIDENCE_REF_BRIDGE_NOT_PRODUCER_BOUND`: the current
  production evaluator's narrow decision/dossier path emits Decision v4, while
  Formal Activation Run Source v3 requires a separately producer-bound
  QualityDecision v3 event. The factory accepts only typed v3 evidence refs and
  does not derive them from reports or remap v4 truth.

Therefore the factory boundary is bound and tested, but formal pilot outcome
access remains blocked. This is additive and preserves the prior shadow mode.

## Production-boundary tests

`agent/tests/alpha_foundry/test_activation_candidate_factory_v1.py` proves:

- construction reaches the existing serial evaluator through the existing DAG
  evaluator factory;
- canonical identity is recorded by `FactorIdentityService`;
- QualityDecision v3 is called through `QualityDecisionV3Service`;
- terminal/dossier authority remains with the production evaluator;
- both arms share generator/evaluator factories and one input bundle;
- factory outputs are refs-only;
- no Activation-specific scorecard, execution engine, Decision runner, second
  ledger, or second artifact repository exists.
