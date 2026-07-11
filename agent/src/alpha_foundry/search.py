from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from src.alpha_foundry.candidate_pool import CandidateExpression
from src.alpha_foundry.mutators import SeedMutator
from src.alpha_foundry.search_lifecycle import (
    EventSourcedSearchLifecycle,
    SearchAttemptResult,
)
from src.alpha_foundry.seed_bank import SeedBank
from src.research_ledger.hash_utils import canonical_json_hash
from src.research_ledger.trial_ledger import TrialLedger, TrialLedgerEntry


@dataclass(frozen=True)
class AlphaFoundrySearchResult:
    candidates: list[CandidateExpression]
    n_candidates_seen: int
    trial_budget_exhausted: bool
    attempts: tuple[SearchAttemptResult, ...] = ()
    terminal_status_counts: tuple[tuple[str, int], ...] = ()


class AlphaFoundrySearch:
    def __init__(
        self,
        *,
        seed_bank: SeedBank,
        ledger: TrialLedger | None = None,
        mutator: SeedMutator | None = None,
        max_candidates: int = 2000,
        trial_budget: int = 5000,
        now: Callable[[], datetime] | None = None,
        lifecycle: EventSourcedSearchLifecycle | None = None,
        run_id: str | None = None,
    ) -> None:
        self.seed_bank = seed_bank
        self.ledger = ledger
        self.mutator = mutator or SeedMutator()
        self.max_candidates = max(0, max_candidates)
        self.trial_budget = max(0, trial_budget)
        self.now = now or (lambda: datetime.now(timezone.utc))
        if (lifecycle is None) != (run_id is None):
            raise ValueError("typed search lifecycle and run_id must be supplied together")
        self.lifecycle = lifecycle
        self.run_id = run_id

    def generate(self) -> AlphaFoundrySearchResult:
        candidates: list[CandidateExpression] = []
        attempts: list[SearchAttemptResult] = []
        seen = 0
        for seed in self.seed_bank.list():
            for candidate in self.mutator.mutate(seed):
                if seen >= self.max_candidates or seen >= self.trial_budget:
                    return AlphaFoundrySearchResult(
                        candidates=candidates,
                        n_candidates_seen=seen,
                        trial_budget_exhausted=seen >= self.trial_budget,
                        attempts=tuple(attempts),
                        terminal_status_counts=self._terminal_counts(attempts),
                    )
                seen += 1
                candidates.append(candidate)
                attempt: SearchAttemptResult | None = None
                if self.lifecycle is not None and self.run_id is not None:
                    attempt = self.lifecycle.evaluate_candidate(
                        candidate,
                        run_id=self.run_id,
                        attempt_index=seen,
                    )
                    attempts.append(attempt)
                if self.ledger is not None:
                    self.ledger.append(self._trial_record(candidate, seen, attempt))
        return AlphaFoundrySearchResult(
            candidates=candidates,
            n_candidates_seen=seen,
            trial_budget_exhausted=seen >= self.trial_budget,
            attempts=tuple(attempts),
            terminal_status_counts=self._terminal_counts(attempts),
        )

    def _trial_record(
        self,
        candidate: CandidateExpression,
        count: int,
        attempt: SearchAttemptResult | None = None,
    ) -> TrialLedgerEntry:
        created = self.now().astimezone(timezone.utc).isoformat()
        status = "success" if attempt is None else (
            attempt.status
            if attempt.status in {"success", "reject", "skip", "error"}
            else "error"
        )
        decision = "none" if attempt is None else attempt.decision
        return TrialLedgerEntry(
            trial_id=(
                f"trial-{candidate.candidate_id}-{count}"
                if attempt is None else attempt.trial_id
            ),
            trial_group_id="alpha_foundry_search",
            parent_trial_id=None,
            candidate_id=candidate.candidate_id,
            parent_seed_id=candidate.parent_seed_id,
            formula=candidate.formula,
            formula_hash=candidate.formula_hash,
            data_snapshot_hash=(
                "sha256:unavailable"
                if attempt is None else attempt.data_snapshot_hash
            ),
            universe_hash="sha256:unavailable",
            split_id="train",
            data_scope="train",
            search_space_hash=canonical_json_hash(
                {"max_candidates": self.max_candidates, "trial_budget": self.trial_budget}
            ),
            objective="candidate_generation",
            random_seed=None,
            n_candidates_seen_so_far=count,
            status=status,
            decision=decision,
            reason_codes=([] if attempt is None else list(attempt.reason_codes)),
            parameter_variant=candidate.metadata,
            metrics_summary=(
                {}
                if attempt is None
                else {
                    "typed_terminal_event_hash": attempt.terminal_event_hash,
                    "typed_evaluation_event_hash": attempt.evaluation_event_hash,
                    "typed_terminal_status": attempt.status,
                }
            ),
            previous_entry_hash=None,
            entry_hash="",
            created_at=created,
        )

    @staticmethod
    def _terminal_counts(
        attempts: list[SearchAttemptResult],
    ) -> tuple[tuple[str, int], ...]:
        return tuple(sorted(Counter(attempt.status for attempt in attempts).items()))
