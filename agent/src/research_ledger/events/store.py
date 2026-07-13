"""SQLite WAL-backed append-only typed research-event store."""

from __future__ import annotations

import json
import math
import random
import sqlite3
import time
from collections import Counter
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Mapping, cast
from uuid import UUID, uuid4

from src.alpha_quality.flags import ResolvedAGSFlags, is_valid_ags_flag_snapshot
from src.research_ledger.events.artifacts import (
    hash_artifact,
    validate_artifact_references,
)
from src.research_ledger.events.model import (
    EventDraft,
    EventIdempotencyConflict,
    EventMutationError,
    EventTransitionError,
    EventValidationError,
    LifecycleSummary,
    ReplayState,
    ResearchEventAppendError,
    ResearchEventEnvelope,
    VerifiedEventSubsequence,
    _issue_verified_event_subsequence,
)
from src.research_ledger.events.payloads import (
    envelope_diagnostics,
    validate_and_redact_payload,
)
from src.research_ledger.events.replay import build_replay_state
from src.research_ledger.hash_utils import (
    canonical_json,
    canonical_json_hash,
    redact_secrets,
    utc_now_iso,
)


DurabilityProfile = Literal["authoritative", "balanced"]


_EVENT_CAPABILITY_REQUIREMENTS: Mapping[str, tuple[str, ...]] = {
    "RegistryBootstrapRecordedV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ProcessActionFrozenV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
    ),
    "ProcessOutcomeRecordedV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
    ),
    "RetrieverDecisionV2Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverActionTemplateFrozen": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "TrainValidDataSnapshotFrozen": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "EvaluationPolicyRegistered": ("VIBE_TRADING_ALPHA_SCORECARD",),
    "ResolvedEvaluationContractRegistered": (
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "ApplicabilityAssessmentRecorded": (
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "AsharePITAdapterRegistered": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "AsharePITSnapshotRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "FactorOutputRecordedV3": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ObservedPanelPredictiveEvidenceRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "PITPredictiveEvidenceRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ScorecardDecisionEvidenceV4Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ExecutionEvidenceRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ComparisonPoolFrozen": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "SecondaryEvidenceRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "SelectionAssessmentRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_DECISION_V2",
    ),
    "ClaimMatrixRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_DECISION_V2",
    ),
    "QualityDecisionV4Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_DECISION_V2",
    ),
    "ResearchDossierRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_ALPHA_REPORT_API",
    ),
    "ExperimentRunReportRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_ALPHA_REPORT_API",
    ),
    "ResearchReleaseManifestRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_ALPHA_REPORT_API",
    ),
    "ResearchDossierMaterializationFailed": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_ALPHA_REPORT_API",
    ),
    "ProductionEvaluationNodeRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "TrialTerminalDossierRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "ReportMaterializationFailed": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
    ),
    "FinalRawProviderV2Registered": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalEvaluationEligibilityRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_ALPHA_SCORECARD",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalTestCapabilityV2Issued": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalOutcomeAccessV2Recorded": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalContaminationV2Recorded": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalTestArtifactV2Recorded": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalTestFailedV2Recorded": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "FinalSelectionAssessmentV2Recorded": (
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_DECISION_V2",
    ),
    "RetrieverFeatureSourceRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV3Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV4Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV5Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV6Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV7Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "OfficialSearchControlRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "PreArmFlatScheduleFrozen": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationPairExecutionScheduled": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationPairExecutionClaimed": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationPlanRegistered": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationRunRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationRunSourceAudited": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationResourceMeasured": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationResourceMeasuredV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationGenerationConsumptionRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationGenerationConsumptionV2Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationGenerationConsumptionV3Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationGenerationConsumptionV4Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_RESEARCH_EVENTS",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationResultRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverActivationDecisionRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY",
        "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
        "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "FalsificationContractRegistered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "SequentialProtocolRegistered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "SequentialLookRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "OutcomeDataAccessed": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationResultRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "MechanismEvidenceIndexRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationCatalogV2Registered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationContractV2Registered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationOutcomeAccessV2Recorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationSourceArtifactV2Recorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationResultV2Recorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "ComplementEvidenceRecorded": ("VIBE_TRADING_COMPLEMENT_V2",),
    "QualityDecisionRecorded": ("VIBE_TRADING_ADMISSION_GATE",),
    "DecisionEvidenceV3Recorded": ("VIBE_TRADING_DECISION_V2",),
    "ScorecardDecisionEvidenceV3Recorded": ("VIBE_TRADING_DECISION_V2",),
    "SnapshotDecisionEvidenceV3Recorded": ("VIBE_TRADING_DECISION_V2",),
    "QualityDecisionV2Recorded": ("VIBE_TRADING_DECISION_V2",),
    "QualityDecisionV3Recorded": ("VIBE_TRADING_DECISION_V2",),
    "FinalCandidateFrozen": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestCapabilityIssued": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestAccessRecorded": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestArtifactRecorded": ("VIBE_TRADING_DECISION_V2",),
    "ForwardPlanV2Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardObservationV2Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardMonitoringProviderV3Registered": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardPlanV3Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardSourceArtifactV3Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardObservationV3Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "DataRevisionRecorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardPlanRecorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardObservationRecorded": ("VIBE_TRADING_FORWARD_TRACKING",),
}

_PRODUCER_SCOPED_EVENT_TYPES = frozenset(
    {
        "DecisionEvidenceV3Recorded",
        "EvaluationPolicyRegistered",
        "ResolvedEvaluationContractRegistered",
        "ApplicabilityAssessmentRecorded",
        "ScorecardDecisionEvidenceV3Recorded",
        "SnapshotDecisionEvidenceV3Recorded",
        "AsharePITAdapterRegistered",
        "AsharePITSnapshotRecorded",
        "FactorOutputRecordedV3",
        "ObservedPanelPredictiveEvidenceRecorded",
        "PITPredictiveEvidenceRecorded",
        "ScorecardDecisionEvidenceV4Recorded",
        "ExecutionEvidenceRecorded",
        "ComparisonPoolFrozen",
        "SecondaryEvidenceRecorded",
        "SelectionAssessmentRecorded",
        "ClaimMatrixRecorded",
        "QualityDecisionV4Recorded",
        "ResearchDossierRecorded",
        "ExperimentRunReportRecorded",
        "ResearchReleaseManifestRecorded",
        "ResearchDossierMaterializationFailed",
        "ProductionEvaluationNodeRecorded",
        "TrialTerminalDossierRecorded",
        "ReportMaterializationFailed",
        "FinalRawProviderV2Registered",
        "FinalEvaluationEligibilityRecorded",
        "FinalTestCapabilityV2Issued",
        "FinalOutcomeAccessV2Recorded",
        "FinalContaminationV2Recorded",
        "FinalTestArtifactV2Recorded",
        "FinalTestFailedV2Recorded",
        "FinalSelectionAssessmentV2Recorded",
        "FalsificationCatalogV2Registered",
        "FalsificationContractV2Registered",
        "FalsificationOutcomeAccessV2Recorded",
        "FalsificationSourceArtifactV2Recorded",
        "FalsificationResultV2Recorded",
        "ForwardMonitoringProviderV3Registered",
        "ForwardPlanV3Recorded",
        "ForwardSourceArtifactV3Recorded",
        "ForwardObservationV3Recorded",
        "DataRevisionRecorded",
    }
)


class ResearchEventStore:
    """Additive event table sharing the existing research-ledger database."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        artifact_root: str | Path,
        flags: ResolvedAGSFlags,
        code_version: str,
        durability_profile: DurabilityProfile = "authoritative",
        busy_timeout_ms: int = 30_000,
        max_retries: int = 12,
    ) -> None:
        if not flags.enabled("VIBE_TRADING_RESEARCH_EVENTS"):
            raise RuntimeError("research events capability is disabled")
        if durability_profile not in {"authoritative", "balanced"}:
            raise ValueError("unknown research-event durability profile")
        if not code_version.strip():
            raise ValueError("code_version is required")
        self._reject_secret_or_path(code_version, "code_version")
        if busy_timeout_ms <= 0 or max_retries <= 0:
            raise ValueError("busy timeout and retry count must be positive")

        self.db_path = Path(db_path)
        self.artifact_root = Path(artifact_root)
        self.flags = flags
        self.code_version = code_version
        self.durability_profile = durability_profile
        self.busy_timeout_ms = int(busy_timeout_ms)
        self.max_retries = int(max_retries)
        self.synchronous_mode = "FULL" if durability_profile == "authoritative" else "NORMAL"
        self.decision_cap = None if durability_profile == "authoritative" else "research_only"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.artifact_root = self.artifact_root.resolve(strict=True)
        self._initialize_schema()

    @staticmethod
    def hash_artifact(path: str | Path) -> str:
        return hash_artifact(path)

    def durability_diagnostics(self) -> dict[str, str | int]:
        with self._connect() as conn:
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).upper()
            synchronous_value = int(conn.execute("PRAGMA synchronous").fetchone()[0])
            busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        synchronous = {0: "OFF", 1: "NORMAL", 2: "FULL", 3: "EXTRA"}.get(
            synchronous_value,
            f"UNKNOWN:{synchronous_value}",
        )
        return {
            "journal_mode": journal_mode,
            "synchronous": synchronous,
            "busy_timeout_ms": busy_timeout,
        }

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=self.busy_timeout_ms / 1000.0,
            isolation_level=None,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA synchronous={self.synchronous_mode}")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _initialize_schema(self) -> None:
        last_error: sqlite3.OperationalError | None = None
        for attempt in range(self.max_retries):
            try:
                self._initialize_schema_once()
                return
            except sqlite3.OperationalError as exc:
                last_error = exc
                if not self._is_retryable_lock(exc) or attempt + 1 >= self.max_retries:
                    raise ResearchEventAppendError(f"research-event schema initialization failed: {exc}") from exc
                self._retry_delay(attempt)
        raise ResearchEventAppendError(f"research-event schema initialization failed after retries: {last_error}")

    def _initialize_schema_once(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS research_events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    schema_version TEXT NOT NULL,
                    event_id TEXT NOT NULL UNIQUE,
                    event_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    payload_schema_version TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    idempotency_key TEXT,
                    previous_event_hash TEXT,
                    event_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    code_version TEXT NOT NULL,
                    feature_flags TEXT NOT NULL,
                    warnings TEXT NOT NULL,
                    hard_failures TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_research_events_idempotency
                    ON research_events(idempotency_key)
                    WHERE idempotency_key IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_research_events_entity
                    ON research_events(entity_id, seq);
                CREATE INDEX IF NOT EXISTS idx_research_events_type
                    ON research_events(event_type, seq);
                CREATE TRIGGER IF NOT EXISTS research_events_no_update
                BEFORE UPDATE ON research_events
                BEGIN
                    SELECT RAISE(ABORT, 'research_events is append-only');
                END;
                CREATE TRIGGER IF NOT EXISTS research_events_no_delete
                BEFORE DELETE ON research_events
                BEGIN
                    SELECT RAISE(ABORT, 'research_events is append-only');
                END;
                """
            )
            columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(research_events)").fetchall()}
            expected = {
                "seq",
                "schema_version",
                "event_id",
                "event_type",
                "entity_id",
                "run_id",
                "payload_schema_version",
                "payload",
                "payload_hash",
                "idempotency_key",
                "previous_event_hash",
                "event_hash",
                "created_at",
                "code_version",
                "feature_flags",
                "warnings",
                "hard_failures",
            }
            if columns != expected:
                raise ResearchEventAppendError("incompatible research_events schema; additive migration required")

    def append_event(self, draft: EventDraft) -> ResearchEventEnvelope:
        self._validate_event_capability(draft.event_type)
        if draft.event_type in _PRODUCER_SCOPED_EVENT_TYPES:
            raise EventValidationError(f"{draft.event_type} can only be minted by its deterministic producer")
        return self._append_event(draft)

    def _append_producer_event(self, draft: EventDraft) -> ResearchEventEnvelope:
        if draft.event_type not in _PRODUCER_SCOPED_EVENT_TYPES:
            raise EventValidationError("event type is not producer scoped")
        return self._append_event(draft)

    def _append_event(self, draft: EventDraft) -> ResearchEventEnvelope:
        self._validate_event_capability(draft.event_type)
        self._validate_draft_identity(draft)
        payload = validate_and_redact_payload(
            draft.event_type,
            draft.payload_schema_version,
            draft.payload,
        )
        self._validate_payload_entity(draft, payload)
        self._validate_artifacts(payload)
        self._validate_external_falsification_v2(draft.event_type, draft.run_id, payload)
        self._validate_external_forward_v3(draft.event_type, draft.run_id, payload)
        self._validate_external_process_evidence(draft.event_type, payload)
        self._validate_external_retriever_action_template(draft.event_type, payload)
        self._validate_external_train_valid_snapshot(draft.event_type, payload)
        self._validate_external_retriever_feature_source(draft.event_type, payload)
        self._validate_external_retriever_evidence(draft.event_type, payload)
        self._validate_external_retriever_v4_evidence(draft.event_type, payload)
        self._validate_external_retriever_v5_evidence(draft.event_type, payload)
        self._validate_external_retriever_v6_evidence(draft.event_type, payload)
        self._validate_external_retriever_v7_evidence(draft.event_type, payload)
        self._validate_external_quality_decision_evidence(draft.event_type, payload)
        self._validate_external_decision_evidence_v3(draft.event_type, payload)
        self._validate_external_evaluation_policy(draft.event_type, payload)
        self._validate_external_resolved_evaluation_contract(draft.event_type, payload)
        self._validate_external_applicability_assessment(draft.event_type, payload)
        self._validate_external_scorecard_evidence_v3(draft.event_type, payload)
        self._validate_external_snapshot_evidence_v3(draft.event_type, payload)
        self._validate_external_pit_adapter_registration(draft.event_type, payload)
        self._validate_external_pit_snapshot(draft.event_type, payload)
        self._validate_external_predictive_v4(draft.event_type, payload)
        self._validate_external_execution_evidence(draft.event_type, payload)
        self._validate_external_secondary_evidence(draft.event_type, payload)
        self._validate_external_claim_decision(draft.event_type, payload)
        self._validate_external_research_dossier(draft.event_type, payload)
        self._validate_external_production_evaluator(draft.event_type, payload)
        self._validate_external_activation_source_audit(draft.event_type, payload)
        self._validate_external_official_control_evidence(draft.event_type, payload)
        self._validate_external_prearm_flat_schedule(draft.event_type, payload)
        self._validate_external_pair_execution_schedule(draft.event_type, payload)
        self._validate_external_activation_resource(draft.event_type, payload)
        self._validate_external_activation_resource_v2(draft.event_type, payload)
        self._validate_external_generation_consumption(draft.event_type, payload)
        self._validate_external_generation_consumption_v2(draft.event_type, payload)
        self._validate_external_generation_consumption_v3(draft.event_type, payload)
        self._validate_external_generation_consumption_v4(draft.event_type, payload)
        self._validate_factor_definition_identity(draft.event_type, payload)
        self._validate_registry_bootstrap_identity(draft.event_type, payload)
        payload_hash = canonical_json_hash(payload)
        last_error: Exception | None = None

        for attempt in range(self.max_retries):
            conn: sqlite3.Connection | None = None
            try:
                conn = self._connect()
                conn.execute("BEGIN IMMEDIATE")
                existing = self._idempotent_event(conn, draft, payload, payload_hash)
                if existing is not None:
                    conn.execute("COMMIT")
                    return existing
                self._validate_transition(conn, draft, payload)
                previous_event_hash = self._tail_hash(conn)
                event = self._build_event(
                    draft,
                    payload,
                    payload_hash=payload_hash,
                    previous_event_hash=previous_event_hash,
                )
                self._insert_event(conn, event, canonical_json(payload))
                conn.execute("COMMIT")
                return event
            except sqlite3.OperationalError as exc:
                if conn is not None:
                    self._rollback_quietly(conn)
                last_error = exc
                if not self._is_retryable_lock(exc) or attempt + 1 >= self.max_retries:
                    raise ResearchEventAppendError(str(exc)) from exc
                self._retry_delay(attempt)
            except (EventIdempotencyConflict, EventTransitionError, EventValidationError):
                if conn is not None:
                    self._rollback_quietly(conn)
                raise
            except sqlite3.IntegrityError as exc:
                if conn is not None:
                    self._rollback_quietly(conn)
                raise ResearchEventAppendError(str(exc)) from exc
            except Exception as exc:
                if conn is not None:
                    self._rollback_quietly(conn)
                raise ResearchEventAppendError(str(exc)) from exc
            finally:
                if conn is not None:
                    conn.close()
        raise ResearchEventAppendError(f"append failed after retries: {last_error}")

    def _validate_event_capability(self, event_type: str) -> None:
        missing = [name for name in _EVENT_CAPABILITY_REQUIREMENTS.get(event_type, ()) if not self.flags.enabled(name)]
        if missing:
            raise EventValidationError(f"event capability is disabled for {event_type}: {sorted(missing)}")

    def _build_event(
        self,
        draft: EventDraft,
        payload: Mapping[str, Any],
        *,
        payload_hash: str,
        previous_event_hash: str | None,
    ) -> ResearchEventEnvelope:
        warnings, hard_failures = envelope_diagnostics(
            draft.event_type,
            payload,
            reduced_durability=self.durability_profile == "balanced",
        )
        without_hash = {
            "schema_version": "research_event.v1",
            "event_id": str(uuid4()),
            "event_type": draft.event_type,
            "entity_id": draft.entity_id,
            "run_id": draft.run_id,
            "payload_schema_version": draft.payload_schema_version,
            "payload": dict(payload),
            "payload_hash": payload_hash,
            "idempotency_key": draft.idempotency_key,
            "previous_event_hash": previous_event_hash,
            "created_at": utc_now_iso(),
            "code_version": self.code_version,
            "feature_flags": self.flags.as_dict(),
            "warnings": list(warnings),
            "hard_failures": list(hard_failures),
        }
        event_hash = canonical_json_hash(without_hash)
        return ResearchEventEnvelope.from_dict({**without_hash, "event_hash": event_hash})

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        event: ResearchEventEnvelope,
        payload_json: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO research_events (
                schema_version, event_id, event_type, entity_id, run_id,
                payload_schema_version, payload, payload_hash,
                idempotency_key, previous_event_hash, event_hash,
                created_at, code_version, feature_flags, warnings, hard_failures
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.schema_version,
                event.event_id,
                event.event_type,
                event.entity_id,
                event.run_id,
                event.payload_schema_version,
                payload_json,
                event.payload_hash,
                event.idempotency_key,
                event.previous_event_hash,
                event.event_hash,
                event.created_at,
                event.code_version,
                canonical_json(dict(event.feature_flags)),
                canonical_json(list(event.warnings)),
                canonical_json(list(event.hard_failures)),
            ),
        )

    def _idempotent_event(
        self,
        conn: sqlite3.Connection,
        draft: EventDraft,
        payload: Mapping[str, Any],
        payload_hash: str,
    ) -> ResearchEventEnvelope | None:
        if draft.idempotency_key is None:
            return None
        row = conn.execute(
            "SELECT * FROM research_events WHERE idempotency_key = ?",
            (draft.idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        existing = self._row_to_event(row)
        requested_warnings, requested_hard_failures = envelope_diagnostics(
            draft.event_type,
            payload,
            reduced_durability=self.durability_profile == "balanced",
        )
        business_identity = (
            existing.event_type,
            existing.entity_id,
            existing.run_id,
            existing.payload_schema_version,
            existing.payload_hash,
            existing.code_version,
            dict(existing.feature_flags),
            existing.warnings,
            existing.hard_failures,
        )
        requested_identity = (
            draft.event_type,
            draft.entity_id,
            draft.run_id,
            draft.payload_schema_version,
            payload_hash,
            self.code_version,
            self.flags.as_dict(),
            requested_warnings,
            requested_hard_failures,
        )
        if business_identity != requested_identity:
            raise EventIdempotencyConflict(f"idempotency key conflicts with existing event: {draft.idempotency_key}")
        return existing

    def _validate_draft_identity(self, draft: EventDraft) -> None:
        for name, value in (("entity_id", draft.entity_id), ("run_id", draft.run_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise EventValidationError(f"{name} must be a non-empty bounded string")
            if any(ord(char) < 32 for char in value):
                raise EventValidationError(f"{name} contains control characters")
            self._reject_secret_or_path(value, name)
        if draft.idempotency_key is not None:
            if not draft.idempotency_key.strip() or len(draft.idempotency_key) > 512:
                raise EventValidationError("idempotency_key must be a bounded non-empty string")
            self._reject_secret_or_path(draft.idempotency_key, "idempotency_key")

    @staticmethod
    def _reject_secret_or_path(value: str, name: str) -> None:
        if redact_secrets(value) != value or PurePosixPath(value).is_absolute():
            raise EventValidationError(f"{name} contains secret or local path")

    @staticmethod
    def _validate_payload_entity(draft: EventDraft, payload: Mapping[str, Any]) -> None:
        identity_fields = {
            "TrialStarted": "trial_id",
            "FactorDefinitionRecorded": "factor_spec_id",
            "RegistryBootstrapRecorded": "snapshot_id",
            "RegistryBootstrapRecordedV2": "snapshot_id",
            "DerivationRecorded": "child_factor_spec_id",
            "ProcessActionFrozen": "action_id",
            "ProcessOutcomeRecorded": "outcome_id",
            "ProcessActionFrozenV2": "action_id",
            "ProcessOutcomeRecordedV2": "outcome_id",
            "GenerationFailureRecorded": "trial_id",
            "EvaluationRecorded": "evaluation_id",
            "TrialTerminated": "trial_id",
            "RetrieverDecisionRecorded": "decision_id",
            "RetrieverActionTemplateFrozen": "action_id",
            "TrainValidDataSnapshotFrozen": "snapshot_id",
            "EvaluationPolicyRegistered": "registration_id",
            "ResolvedEvaluationContractRegistered": "contract_id",
            "ApplicabilityAssessmentRecorded": "assessment_id",
            "AsharePITAdapterRegistered": "registration_id",
            "AsharePITSnapshotRecorded": "snapshot_id",
            "FactorOutputRecordedV3": "factor_output_id",
            "ObservedPanelPredictiveEvidenceRecorded": "evidence_id",
            "PITPredictiveEvidenceRecorded": "evidence_id",
            "ScorecardDecisionEvidenceV4Recorded": "evidence_id",
            "ExecutionEvidenceRecorded": "evidence_id",
            "ComparisonPoolFrozen": "pool_id",
            "SecondaryEvidenceRecorded": "evidence_id",
            "SelectionAssessmentRecorded": "assessment_id",
            "ClaimMatrixRecorded": "matrix_id",
            "QualityDecisionV4Recorded": "decision_id",
            "ResearchDossierRecorded": "dossier_id",
            "ExperimentRunReportRecorded": "report_id",
            "ResearchReleaseManifestRecorded": "manifest_id",
            "ResearchDossierMaterializationFailed": "failure_id",
            "ProductionEvaluationNodeRecorded": "node_id",
            "TrialTerminalDossierRecorded": "dossier_id",
            "ReportMaterializationFailed": "failure_id",
            "FinalRawProviderV2Registered": "registration_id",
            "FinalEvaluationEligibilityRecorded": "eligibility_id",
            "FinalTestCapabilityV2Issued": "capability_id",
            "FinalOutcomeAccessV2Recorded": "access_id",
            "FinalContaminationV2Recorded": "taint_id",
            "FinalTestArtifactV2Recorded": "artifact_id",
            "FinalTestFailedV2Recorded": "failure_id",
            "FinalSelectionAssessmentV2Recorded": "assessment_id",
            "FalsificationCatalogV2Registered": "catalog_hash",
            "FalsificationContractV2Registered": "contract_id",
            "FalsificationOutcomeAccessV2Recorded": "factor_spec_id",
            "FalsificationSourceArtifactV2Recorded": "source_id",
            "FalsificationResultV2Recorded": "result_id",
            "RetrieverFeatureSourceRecorded": "feature_source_id",
            "RetrieverDecisionV2Recorded": "decision_id",
            "RetrieverDecisionV3Recorded": "decision_id",
            "RetrieverDecisionV4Recorded": "decision_id",
            "RetrieverDecisionV5Recorded": "decision_id",
            "RetrieverDecisionV6Recorded": "decision_id",
            "RetrieverDecisionV7Recorded": "decision_id",
            "OfficialSearchControlRecorded": "control_id",
            "PreArmFlatScheduleFrozen": "schedule_id",
            "ActivationPairExecutionScheduled": "schedule_id",
            "ActivationPairExecutionClaimed": "claim_id",
            "ActivationPlanRegistered": "experiment_id",
            "ActivationRunRecorded": "manifest_id",
            "ActivationRunSourceAudited": "audit_id",
            "ActivationResourceMeasured": "resource_id",
            "ActivationResourceMeasuredV2": "resource_id",
            "ActivationGenerationConsumptionRecorded": "generation_id",
            "ActivationGenerationConsumptionV2Recorded": "generation_id",
            "ActivationGenerationConsumptionV3Recorded": "generation_id",
            "ActivationGenerationConsumptionV4Recorded": "generation_id",
            "ActivationResultRecorded": "result_id",
            "RetrieverActivationDecisionRecorded": "activation_decision_id",
            "FalsificationContractRegistered": "contract_id",
            "SequentialProtocolRegistered": "protocol_id",
            "SequentialLookRecorded": "look_id",
            "OutcomeDataAccessed": "access_id",
            "FalsificationResultRecorded": "result_id",
            "MechanismEvidenceIndexRecorded": "mei_id",
            "ComplementEvidenceRecorded": "complement_id",
            "QualityDecisionRecorded": "decision_id",
            "DecisionEvidenceV3Recorded": "evidence_id",
            "ScorecardDecisionEvidenceV3Recorded": "evidence_id",
            "SnapshotDecisionEvidenceV3Recorded": "evidence_id",
            "QualityDecisionV2Recorded": "decision_id",
            "QualityDecisionV3Recorded": "decision_id",
            "FinalCandidateFrozen": "freeze_id",
            "FinalTestCapabilityIssued": "capability_id",
            "FinalTestAccessRecorded": "access_id",
            "FinalTestArtifactRecorded": "artifact_id",
            "ForwardPlanV2Recorded": "plan_id",
            "ForwardObservationV2Recorded": "observation_id",
            "ForwardMonitoringProviderV3Registered": "registration_id",
            "ForwardPlanV3Recorded": "plan_id",
            "ForwardSourceArtifactV3Recorded": "source_id",
            "ForwardObservationV3Recorded": "observation_id",
            "DataRevisionRecorded": "revision_id",
            "ForwardPlanRecorded": "plan_id",
            "ForwardObservationRecorded": "observation_id",
        }
        field = identity_fields[draft.event_type]
        if draft.entity_id != payload[field]:
            raise EventValidationError(f"entity_id must equal payload {field} for {draft.event_type}")

    def _validate_artifacts(self, payload: dict[str, Any]) -> None:
        for field in ("artifact_refs", "raw_partition_refs"):
            references = payload.get(field)
            if references is None:
                continue
            payload[field] = validate_artifact_references(
                self.artifact_root,
                references,
            )

    def _validate_external_falsification_v2(
        self,
        event_type: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "FalsificationResultV2Recorded":
            return
        from src.alpha_quality.falsification.authority_v2 import FalsificationExecutorV2

        rebuilt = FalsificationExecutorV2(store=self).recompute(
            str(payload["contract_event_hash"]),
            str(payload["source_event_hash"]),
            run_id=run_id,
        )
        if (
            canonical_json_hash(rebuilt) != payload["result_hash"]
            or rebuilt["test_results"] != payload["test_results"]
            or rebuilt["family_result"] != payload["family_result"]
        ):
            raise EventValidationError("falsification v2 result is not producer-recomputable")

    def _validate_external_forward_v3(
        self,
        event_type: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ForwardObservationV3Recorded":
            return
        from src.alpha_quality.forward.authority_v3 import (
            ForwardMonitoringProducerV3,
            ForwardMonitoringProviderRegistryV3,
            ForwardPlanAuthorityV3,
            MonitoringProviderDescriptorV3,
        )

        plan_matches = [
            event
            for event in self.query_events(event_type="ForwardPlanV3Recorded")
            if event.event_hash == payload["plan_event_hash"] and event.run_id == run_id
        ]
        source_matches = [
            event
            for event in self.query_events(event_type="ForwardSourceArtifactV3Recorded")
            if event.event_hash == payload["source_event_hash"] and event.run_id == run_id
        ]
        if len(plan_matches) != 1 or len(source_matches) != 1:
            raise EventValidationError("forward v3 observation lacks exact producer sources")
        descriptor_payload = plan_matches[0].payload["config"]
        registration = [
            event
            for event in self.query_events(event_type="ForwardMonitoringProviderV3Registered")
            if event.event_hash == plan_matches[0].payload["provider_registration_event_hash"]
        ]
        if len(registration) != 1:
            raise EventValidationError("forward v3 provider registration is missing")
        descriptor = registration[0].payload["descriptor"]
        registry = ForwardMonitoringProviderRegistryV3(
            (
                MonitoringProviderDescriptorV3(
                    provider_id=str(descriptor["provider_id"]),
                    provider_version=str(descriptor["provider_version"]),
                    calendar_id=str(descriptor["calendar_id"]),
                    vintage_policy="as_observed_append_only",
                    maximum_availability_delay_days=int(descriptor["maximum_availability_delay_days"]),
                    provider_policy_hash=str(descriptor["provider_policy_hash"]),
                ),
            )
        )
        if (
            descriptor_payload["provider_id"] != descriptor["provider_id"]
            or descriptor_payload["provider_version"] != descriptor["provider_version"]
        ):
            raise EventValidationError("forward v3 provider scope differs")
        authority = ForwardPlanAuthorityV3(store=self, registry=registry)
        rebuilt = ForwardMonitoringProducerV3(store=self, authority=authority).recompute(
            plan_matches[0], source_matches[0]
        )
        if (
            canonical_json_hash(rebuilt) != payload["observation_hash"]
            or rebuilt["metrics"] != payload["metrics"]
            or rebuilt["status"] != payload["status"]
            or rebuilt["kill_reasons"] != payload["kill_reasons"]
        ):
            raise EventValidationError("forward v3 observation is not producer-recomputable")

    def _validate_external_process_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild utility before the write transaction; never do file IO under lock."""
        if event_type != "ProcessOutcomeRecordedV2":
            return
        actions = self.query_events(event_type="ProcessActionFrozenV2", entity_id=str(payload["action_id"]))
        evaluations = [
            event
            for event in self.query_events(event_type="EvaluationRecorded")
            if event.event_hash == payload["evaluation_event_hash"]
        ]
        if len(actions) != 1 or len(evaluations) != 1:
            raise EventTransitionError("process outcome v2 lacks frozen external evidence")
        action = actions[0]
        evaluation = evaluations[0]
        references = [
            ref
            for ref in evaluation.payload["artifact_refs"]
            if ref["artifact_hash"] == evaluation.payload["scorecard_hash"]
            and ref["media_type"] == "application/vnd.vibe.alpha-quality-scorecard+json"
        ]
        if len(references) != 1:
            raise EventValidationError("process outcome v2 requires one scorecard artifact")
        reference = validate_artifact_references(self.artifact_root, references)[0]
        path = self.artifact_root.joinpath(*PurePosixPath(reference["relative_path"]).parts)
        try:
            from src.alpha_foundry.memory.utility import mean_valid_rank_icir_utility

            utility = mean_valid_rank_icir_utility(
                path,
                expected_factor_spec_id=str(payload["child_factor_spec_id"]),
                expected_data_snapshot_hash=str(action.payload["data_snapshot_hash"]),
            )
        except ValueError as exc:
            raise EventValidationError("process outcome v2 scorecard evidence is invalid") from exc
        if utility != float(payload["observed_validation_utility"]):
            raise EventValidationError("process outcome v2 utility was not deterministically rebuilt")

    def _validate_external_retriever_action_template(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild the proposed edit from the authoritative parent definition."""
        if event_type != "RetrieverActionTemplateFrozen":
            return
        definitions = [
            event
            for event in self.query_events(
                event_type="FactorDefinitionRecorded",
                entity_id=str(payload["parent_factor_spec_id"]),
            )
            if event.event_hash == payload["parent_definition_event_hash"]
        ]
        if len(definitions) != 1:
            raise EventValidationError("Retriever action lacks its authoritative parent definition")
        try:
            from src.alpha_foundry.retrieval.action_template_v1 import (
                FrozenRetrieverActionTemplateV1,
            )

            rebuilt = FrozenRetrieverActionTemplateV1.build(
                execution_run_id=str(payload["execution_run_id"]),
                parent_definition=definitions[0].payload,
                parent_definition_event_hash=str(payload["parent_definition_event_hash"]),
                eligible_event_watermark=str(payload["eligible_event_watermark"]),
                data_snapshot_hash=str(payload["data_snapshot_hash"]),
                retrieval_policy_hash=str(payload["retrieval_policy_hash"]),
                template_id=str(payload["template_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError("Retriever action template cannot be deterministically rebuilt") from exc
        if rebuilt.to_dict() != dict(payload):
            raise EventValidationError("Retriever action differs from its deterministic template rebuild")

    def _validate_external_train_valid_snapshot(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "TrainValidDataSnapshotFrozen":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.frozen-train-valid-snapshot-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("train/valid snapshot requires one source artifact")
        try:
            from src.alpha_foundry.retrieval.feature_source_v1 import (
                FrozenTrainValidSnapshotArtifactStoreV1,
            )

            snapshot = FrozenTrainValidSnapshotArtifactStoreV1(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["snapshot_hash"]),
            )
            contract = snapshot.snapshot_contract
            base = contract["base_manifest_content"]
            if not isinstance(base, Mapping):
                raise ValueError("train/valid snapshot base contract is invalid")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("train/valid snapshot source cannot be rebuilt") from exc
        expected = {
            "snapshot_hash": snapshot.snapshot_hash,
            "data_scope": snapshot.data_scope,
            "panel_content_hash": contract["panel_content_hash"],
            "frame_content_hashes": contract["frame_content_hashes"],
            "frame_names": list(snapshot.frames),
            "source_config_hash": base["source_config_hash"],
            "pit_contract_present": base["pit_contract_present"],
            "survivorship_bias": base["survivorship_bias"],
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("train/valid snapshot event differs from source artifact")

    def _validate_external_retriever_feature_source(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "RetrieverFeatureSourceRecorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-feature-source-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Retriever feature source requires one artifact")
        try:
            from src.alpha_foundry.retrieval.evidence_v3 import (
                _candidate_to_dict,
            )
            from src.alpha_foundry.retrieval.feature_producer_v1 import (
                RetrieverFeaturePolicyV1,
                RetrieverFeatureSourceArtifactStoreV1,
                RetrieverFeatureSourceServiceV1,
            )
            from src.alpha_foundry.retrieval.policy import (
                ActivationRetrieverPolicy,
            )

            source = RetrieverFeatureSourceArtifactStoreV1(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["source_hash"]),
            )
            rebuilt = RetrieverFeatureSourceServiceV1(
                self,
                flags=self.flags,
                feature_policy=RetrieverFeaturePolicyV1(**dict(source.feature_policy)),
            ).rebuild(
                execution_run_id=source.execution_run_id,
                snapshot_event_hash=source.snapshot_event_hash,
                action_event_hashes=source.action_event_hashes,
                eligible_event_watermark=source.eligible_event_watermark,
                retrieval_policy=ActivationRetrieverPolicy(**dict(source.retrieval_policy)),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Retriever feature source cannot be independently rebuilt") from exc
        if rebuilt != source:
            raise EventValidationError("Retriever feature source differs from deterministic rebuild")
        expected = {
            "source_hash": source.source_hash,
            "execution_run_id": source.execution_run_id,
            "snapshot_event_hash": source.snapshot_event_hash,
            "snapshot_hash": source.snapshot_hash,
            "eligible_event_watermark": source.eligible_event_watermark,
            "retrieval_policy_hash": source.retrieval_policy_hash,
            "feature_policy_hash": source.feature_policy_hash,
            "action_event_hashes": list(source.action_event_hashes),
            "scorecard_event_hashes": list(source.scorecard_event_hashes),
            "candidate_count": len(source.candidates),
            "candidate_hashes": [canonical_json_hash(_candidate_to_dict(candidate)) for candidate in source.candidates],
            "semantic_state": "unavailable",
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Retriever feature event differs from source artifact")

    def _validate_external_retriever_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild v3 components, propensities and selection before locking."""
        if event_type != "RetrieverDecisionV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-input-bundle-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("retriever v3 requires one source input bundle")
        reference = references[0]
        try:
            from src.alpha_foundry.dag import FactorDAGQuery
            from src.alpha_foundry.retrieval.evidence_v3 import (
                RetrieverInputArtifactStoreV3,
            )
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
            from src.alpha_foundry.retrieval.shadow import ShadowRetriever
            from src.alpha_quality.scope import DiscoveryEvidenceProjector

            bundle = RetrieverInputArtifactStoreV3(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_bundle_hash=str(payload["input_bundle_hash"]),
            )
            if (
                bundle.eligible_event_watermark != payload["eligible_event_watermark"]
                or bundle.data_snapshot_hash != payload["data_snapshot_hash"]
                or bundle.seed != payload["seed"]
                or bundle.candidate_budget != payload["candidate_budget"]
                or dict(bundle.policy_config) != dict(payload["policy_config"])
            ):
                raise ValueError("retriever v3 bundle and event inputs differ")
            evidence = DiscoveryEvidenceProjector(flags=self.flags).project_at_watermark(
                self,
                data_snapshot_hash=bundle.data_snapshot_hash,
                watermark_event_hash=bundle.eligible_event_watermark,
            )
            decision = ShadowRetriever(
                flags=self.flags,
                policy=ActivationRetrieverPolicy(**dict(bundle.policy_config)),
            ).decide(
                official_candidate_ids=bundle.official_candidate_ids,
                evidence=evidence,
                query=FactorDAGQuery(evidence.factual.dag),
                candidates=bundle.candidates,
                seed=bundle.seed,
                candidate_budget=bundle.candidate_budget,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("retriever v3 source evidence cannot rebuild the decision") from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "selected_factor_spec_ids": list(decision.selected_factor_spec_ids),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [component.to_dict() for component in decision.components],
            "shadow_only": decision.shadow_only,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v3 event differs from its deterministically rebuilt decision")

    def _validate_external_retriever_v4_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "RetrieverDecisionV4Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-input-bundle-v4+json"
        ]
        if len(references) != 1:
            raise EventValidationError("retriever v4 requires one source input bundle")
        try:
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )
            from src.alpha_foundry.dag import FactorDAGQuery
            from src.alpha_foundry.retrieval.evidence_v4 import (
                RetrieverInputArtifactStoreV4,
            )
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
            from src.alpha_foundry.retrieval.shadow import ShadowRetriever
            from src.alpha_quality.scope import DiscoveryEvidenceProjector

            bundle = RetrieverInputArtifactStoreV4(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["input_bundle_hash"]),
            )
            source = bundle.retriever_input
            controls = [
                event
                for event in self.query_events(event_type="OfficialSearchControlRecorded")
                if event.event_hash == bundle.control_evidence_event_hash
            ]
            if len(controls) != 1:
                raise ValueError("retriever v4 control event is missing")
            control_event = controls[0]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(control_refs) != 1:
                raise ValueError("retriever v4 control artifact is missing")
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                bundle.control_evidence_hash,
            )
            all_events = self.query_events()
            event_order = {event.event_hash: index for index, event in enumerate(all_events)}
            watermark_order = event_order.get(source.eligible_event_watermark, -1)
            if (
                watermark_order < 0
                or event_order.get(control_event.event_hash, -1) <= watermark_order
                or any(
                    event_order.get(event_hash, -1) <= watermark_order for event_hash in control.terminal_event_hashes
                )
            ):
                raise ValueError("retriever v4 discovery watermark includes control evidence")
            official_ids = tuple(str(item["candidate_id"]) for item in control.candidates)
            if (
                official_ids != source.official_candidate_ids
                or control_event.payload["evidence_hash"] != bundle.control_evidence_hash
                or control_event.payload["policy_hash"] != control.policy.policy_hash
                or control_event.payload["output_hash"] != control.output_hash
                or control.output_hash != payload["official_output_hash"]
                or control.policy.policy_hash != payload["control_policy_hash"]
                or control.data_snapshot_hash != source.data_snapshot_hash
                or control_event.run_id != control.run_id
            ):
                raise ValueError("retriever v4 control binding differs")
            discovery = DiscoveryEvidenceProjector(flags=self.flags).project_at_watermark(
                self,
                data_snapshot_hash=source.data_snapshot_hash,
                watermark_event_hash=source.eligible_event_watermark,
            )
            decision = ShadowRetriever(
                flags=self.flags,
                policy=ActivationRetrieverPolicy(**dict(source.policy_config)),
            ).decide(
                official_candidate_ids=official_ids,
                evidence=discovery,
                query=FactorDAGQuery(discovery.factual.dag),
                candidates=source.candidates,
                seed=source.seed,
                candidate_budget=source.candidate_budget,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("retriever v4 source evidence cannot rebuild the decision") from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "input_bundle_hash": bundle.bundle_hash,
            "control_evidence_event_hash": bundle.control_evidence_event_hash,
            "control_evidence_hash": bundle.control_evidence_hash,
            "control_policy_hash": control.policy.policy_hash,
            "selected_factor_spec_ids": list(decision.selected_factor_spec_ids),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [component.to_dict() for component in decision.components],
            "shadow_only": decision.shadow_only,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v4 differs from deterministic rebuild")

    def _validate_external_retriever_v5_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Reopen control, action events, discovery inputs and replay action selection."""
        if event_type != "RetrieverDecisionV5Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-input-bundle-v5+json"
        ]
        if len(references) != 1:
            raise EventValidationError("retriever v5 requires one source input bundle")
        try:
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )
            from src.alpha_foundry.dag import FactorDAGQuery
            from src.alpha_foundry.retrieval.action_decision_v5 import (
                ActionShadowRetrieverV5,
            )
            from src.alpha_foundry.retrieval.action_template_v1 import (
                FrozenRetrieverActionTemplateV1,
            )
            from src.alpha_foundry.retrieval.evidence_v5 import (
                RetrieverInputArtifactStoreV5,
            )
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
            from src.alpha_quality.scope import DiscoveryEvidenceProjector

            bundle = RetrieverInputArtifactStoreV5(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["input_bundle_hash"]),
            )
            source = bundle.retriever_input
            controls = [
                event
                for event in self.query_events(event_type="OfficialSearchControlRecorded")
                if event.event_hash == bundle.control_evidence_event_hash
            ]
            if len(controls) != 1:
                raise ValueError("retriever v5 control event is missing")
            control_event = controls[0]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(control_refs) != 1:
                raise ValueError("retriever v5 control artifact is missing")
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                bundle.control_evidence_hash,
            )
            all_events = self.query_events()
            by_hash = {event.event_hash: event for event in all_events}
            order = {event.event_hash: index for index, event in enumerate(all_events)}
            watermark_order = order.get(source.eligible_event_watermark, -1)
            control_order = order.get(control_event.event_hash, -1)
            action_events = [by_hash.get(event_hash) for event_hash in bundle.action_template_event_hashes]
            if (
                watermark_order < 0
                or control_order <= watermark_order
                or any(event is None for event in action_events)
                or any(
                    event is None
                    or event.event_type != "RetrieverActionTemplateFrozen"
                    or not watermark_order < order[event.event_hash] < control_order
                    for event in action_events
                )
                or any(order.get(event_hash, -1) <= watermark_order for event_hash in control.terminal_event_hashes)
            ):
                raise ValueError("retriever v5 source ordering is invalid")
            actions = tuple(
                FrozenRetrieverActionTemplateV1.from_dict(event.payload) for event in action_events if event is not None
            )
            policy = ActivationRetrieverPolicy(**dict(source.policy_config))
            if any(
                action.eligible_event_watermark != source.eligible_event_watermark
                or action.data_snapshot_hash != source.data_snapshot_hash
                or action.retrieval_policy_hash != policy.policy_hash
                for action in actions
            ):
                raise ValueError("retriever v5 action scope or policy differs")
            official_ids = tuple(str(item["candidate_id"]) for item in control.candidates)
            if (
                official_ids != source.official_candidate_ids
                or control_event.payload["evidence_hash"] != bundle.control_evidence_hash
                or control_event.payload["policy_hash"] != control.policy.policy_hash
                or control_event.payload["output_hash"] != control.output_hash
                or control.output_hash != payload["official_output_hash"]
                or control.policy.policy_hash != payload["control_policy_hash"]
                or control.data_snapshot_hash != source.data_snapshot_hash
                or control_event.run_id != control.run_id
            ):
                raise ValueError("retriever v5 control binding differs")
            discovery = DiscoveryEvidenceProjector(flags=self.flags).project_at_watermark(
                self,
                data_snapshot_hash=source.data_snapshot_hash,
                watermark_event_hash=source.eligible_event_watermark,
            )
            decision = ActionShadowRetrieverV5(
                flags=self.flags,
                policy=policy,
            ).decide(
                official_candidate_ids=official_ids,
                evidence=discovery,
                query=FactorDAGQuery(discovery.factual.dag),
                candidates=source.candidates,
                actions=actions,
                action_template_event_hashes=bundle.action_template_event_hashes,
                seed=source.seed,
                candidate_budget=source.candidate_budget,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("retriever v5 source evidence cannot rebuild the action decision") from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "input_bundle_hash": bundle.bundle_hash,
            "control_evidence_event_hash": bundle.control_evidence_event_hash,
            "control_evidence_hash": bundle.control_evidence_hash,
            "control_policy_hash": control.policy.policy_hash,
            "selected_action_ids": list(decision.selected_action_ids),
            "selected_parent_factor_spec_ids": list(decision.selected_parent_factor_spec_ids),
            "action_template_event_hashes": list(decision.action_template_event_hashes),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [component.to_dict() for component in decision.components],
            "shadow_only": decision.shadow_only,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v5 differs from deterministic rebuild")

    def _validate_external_retriever_v6_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Reopen and independently rebuild the feature-source-bound decision."""
        if event_type != "RetrieverDecisionV6Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-decision-input-v6+json"
        ]
        if len(references) != 1:
            raise EventValidationError("retriever v6 requires one minimal input bundle")
        try:
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )
            from src.alpha_foundry.dag import FactorDAGQuery
            from src.alpha_foundry.retrieval.action_decision_v5 import (
                ActionShadowRetrieverV5,
            )
            from src.alpha_foundry.retrieval.action_template_v1 import (
                FrozenRetrieverActionTemplateV1,
            )
            from src.alpha_foundry.retrieval.evidence_v6 import (
                RetrieverDecisionInputArtifactStoreV6,
            )
            from src.alpha_foundry.retrieval.feature_producer_v1 import (
                RetrieverFeatureSourceArtifactStoreV1,
            )
            from src.alpha_foundry.retrieval.policy import ActivationRetrieverPolicy
            from src.alpha_quality.scope import DiscoveryEvidenceProjector

            bundle = RetrieverDecisionInputArtifactStoreV6(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["input_bundle_hash"]),
            )
            all_events = self.query_events()
            by_hash = {event.event_hash: event for event in all_events}
            order = {event.event_hash: index for index, event in enumerate(all_events)}
            source_event = by_hash.get(bundle.feature_source_event_hash)
            control_event = by_hash.get(bundle.control_evidence_event_hash)
            if (
                source_event is None
                or source_event.event_type != "RetrieverFeatureSourceRecorded"
                or control_event is None
                or control_event.event_type != "OfficialSearchControlRecorded"
                or order[source_event.event_hash] >= order[control_event.event_hash]
            ):
                raise ValueError("retriever v6 feature/control ordering is invalid")
            self._validate_external_retriever_feature_source(source_event.event_type, source_event.to_dict()["payload"])
            source_refs = [
                reference
                for reference in source_event.payload["artifact_refs"]
                if reference["media_type"] == RetrieverFeatureSourceArtifactStoreV1.media_type
            ]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(source_refs) != 1 or len(control_refs) != 1:
                raise ValueError("retriever v6 authoritative source artifact is missing")
            source = RetrieverFeatureSourceArtifactStoreV1(self.artifact_root).read(
                str(source_refs[0]["relative_path"]), bundle.feature_source_hash
            )
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]), bundle.control_evidence_hash
            )
            action_events = tuple(by_hash.get(event_hash) for event_hash in source.action_event_hashes)
            if any(
                event is None
                or event.event_type != "RetrieverActionTemplateFrozen"
                or event.run_id != source.execution_run_id
                or order[event.event_hash] >= order[source_event.event_hash]
                for event in action_events
            ):
                raise ValueError("retriever v6 frozen action source is invalid")
            actions = tuple(
                FrozenRetrieverActionTemplateV1.from_dict(event.payload) for event in action_events if event is not None
            )
            policy = ActivationRetrieverPolicy(**dict(source.retrieval_policy))
            official_ids = tuple(str(item["candidate_id"]) for item in control.candidates)
            watermark_order = order.get(source.eligible_event_watermark, -1)
            if (
                watermark_order < 0
                or any(order.get(event_hash, -1) <= watermark_order for event_hash in control.terminal_event_hashes)
                or source_event.payload["source_hash"] != source.source_hash
                or source_event.run_id != source.execution_run_id
                or source.snapshot_hash != control.data_snapshot_hash
                or control_event.payload["evidence_hash"] != control.evidence_hash
                or control_event.payload["policy_hash"] != control.policy.policy_hash
                or control_event.payload["output_hash"] != control.output_hash
                or control_event.run_id != control.run_id
                or source.action_event_hashes != tuple(payload["action_template_event_hashes"])
            ):
                raise ValueError("retriever v6 authoritative source binding differs")
            discovery = DiscoveryEvidenceProjector(flags=self.flags).project_at_watermark(
                self,
                data_snapshot_hash=source.snapshot_hash,
                watermark_event_hash=source.eligible_event_watermark,
            )
            decision = ActionShadowRetrieverV5(
                flags=self.flags,
                policy=policy,
            ).decide(
                official_candidate_ids=official_ids,
                evidence=discovery,
                query=FactorDAGQuery(discovery.factual.dag),
                candidates=source.candidates,
                actions=actions,
                action_template_event_hashes=source.action_event_hashes,
                seed=bundle.seed,
                candidate_budget=bundle.candidate_budget,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("retriever v6 evidence cannot rebuild the source-bound action decision") from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "input_bundle_hash": bundle.bundle_hash,
            "control_evidence_event_hash": bundle.control_evidence_event_hash,
            "control_evidence_hash": bundle.control_evidence_hash,
            "control_policy_hash": control.policy.policy_hash,
            "feature_source_event_hash": bundle.feature_source_event_hash,
            "feature_source_hash": bundle.feature_source_hash,
            "selected_action_ids": list(decision.selected_action_ids),
            "selected_parent_factor_spec_ids": list(decision.selected_parent_factor_spec_ids),
            "action_template_event_hashes": list(decision.action_template_event_hashes),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [component.to_dict() for component in decision.components],
            "shadow_only": decision.shadow_only,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v6 differs from deterministic rebuild")

    def _validate_external_retriever_v7_evidence(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type != "RetrieverDecisionV7Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.retriever-decision-input-v7+json"
        ]
        if len(references) != 1:
            raise EventValidationError("retriever v7 requires one minimal input bundle")
        try:
            from src.alpha_foundry.retrieval.evidence_v7 import (
                RetrieverDecisionInputArtifactStoreV7,
            )
            from src.alpha_foundry.retrieval.service_v7 import (
                RetrieverDecisionV7Service,
            )

            bundle = RetrieverDecisionInputArtifactStoreV7(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["input_bundle_hash"]),
            )
            by_hash = {event.event_hash: event for event in self.query_events()}
            schedule_event = by_hash.get(bundle.schedule_event_hash)
            source_event = by_hash.get(bundle.feature_source_event_hash)
            if schedule_event is None or source_event is None:
                raise ValueError("retriever v7 upstream event is missing")
            self._validate_external_prearm_flat_schedule(schedule_event.event_type, schedule_event.to_dict()["payload"])
            self._validate_external_retriever_feature_source(source_event.event_type, source_event.to_dict()["payload"])
            historical = [
                event
                for event in self.query_events(event_type="RetrieverDecisionV7Recorded")
                if event.entity_id == payload["decision_id"]
                and event.payload["decision_hash"] == payload["decision_hash"]
            ]
            if len(historical) > 1:
                raise ValueError("retriever v7 historical identity is ambiguous")
            decision, rebuilt_bundle, schedule, source, _ = RetrieverDecisionV7Service(self).rebuild(
                schedule_event_hash=bundle.schedule_event_hash,
                feature_source_event_hash=bundle.feature_source_event_hash,
                decision_event_hash=(None if not historical else historical[0].event_hash),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("retriever v7 evidence cannot rebuild the pre-arm decision") from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "input_bundle_hash": rebuilt_bundle.bundle_hash,
            "plan_hash": schedule.plan_hash,
            "pair_id": schedule.pair_id,
            "schedule_event_hash": bundle.schedule_event_hash,
            "schedule_hash": schedule.schedule_hash,
            "feature_source_event_hash": bundle.feature_source_event_hash,
            "feature_source_hash": source.source_hash,
            "selected_action_ids": list(decision.selected_action_ids),
            "selected_parent_factor_spec_ids": list(decision.selected_parent_factor_spec_ids),
            "action_template_event_hashes": list(decision.action_template_event_hashes),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [component.to_dict() for component in decision.components],
            "shadow_only": decision.shadow_only,
        }
        if bundle != rebuilt_bundle or any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v7 differs from deterministic rebuild")

    def _validate_external_decision_evidence_v3(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "DecisionEvidenceV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.decision-evidence-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Decision evidence v3 requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.decision_v2.evidence_v3 import (
                DecisionEvidenceArtifactStoreV3,
                DecisionLedgerEvidenceServiceV3,
            )

            record = DecisionEvidenceArtifactStoreV3(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_evidence_hash=str(payload["evidence_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt = DecisionLedgerEvidenceServiceV3.rebuild_record(
                self,
                factor_spec_id=record.factor_spec_id,
                expected_evaluation_event_hash=str(record.evidence_payload["evaluation_event_hash"]),
                expected_terminal_event_hash=str(record.evidence_payload["terminal_event_hash"]),
                run_id=record.evidence_run_id,
                ledger_watermark_event_hash=str(record.evidence_payload["ledger_watermark_event_hash"]),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Decision evidence v3 artifact or source replay is invalid") from exc
        if rebuilt != record:
            raise EventValidationError("Decision evidence v3 differs from deterministic source replay")
        digest = record.evidence_hash.removeprefix("sha256:")
        expected = DecisionLedgerEvidenceServiceV3.event_payload(
            "decision-evidence-v3-" + digest[:24],
            record,
            reference,
        )
        if dict(payload) != expected:
            raise EventValidationError("Decision evidence v3 event differs from its producer artifact")

    def _validate_external_evaluation_policy(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "EvaluationPolicyRegistered":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.registered-evaluation-policy-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("evaluation policy registration requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.evaluation_registry_v1 import (
                EvaluationPolicyArtifactStoreV1,
                EvaluationPolicyRegistryServiceV1,
            )

            bundle = EvaluationPolicyArtifactStoreV1(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_bundle_hash=str(payload["bundle_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = EvaluationPolicyRegistryServiceV1.event_payload(
                str(payload["registration_id"]),
                bundle,
                reference,
                (None if payload["preregistration_watermark"] is None else str(payload["preregistration_watermark"])),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("evaluation policy artifact cannot be independently rebuilt") from exc
        if dict(payload) != expected:
            raise EventValidationError("evaluation policy event differs from its producer artifact")

    def _validate_external_resolved_evaluation_contract(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ResolvedEvaluationContractRegistered":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.resolved-evaluation-contract-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("resolved contract requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.evaluation_contract.contract import (
                ResolvedEvaluationContractArtifactStoreV1,
                ResolvedEvaluationContractServiceV1,
            )

            contract = ResolvedEvaluationContractArtifactStoreV1(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_contract_hash=str(payload["contract_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = ResolvedEvaluationContractServiceV1.event_payload(contract, reference)
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("resolved contract artifact cannot be independently rebuilt") from exc
        if dict(payload) != expected:
            raise EventValidationError("resolved contract event differs from its producer artifact")

    def _validate_external_applicability_assessment(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ApplicabilityAssessmentRecorded":
            return
        try:
            from src.alpha_quality.evaluation_contract.applicability import (
                _contract_from_event,
                build_assessment,
            )

            by_hash = {event.event_hash: event for event in self.query_events()}
            source_hashes = set(str(item) for item in payload["source_event_hashes"])
            factor_event = by_hash.get(str(payload["factor_definition_event_hash"]))
            contract_events = [
                event
                for event in by_hash.values()
                if event.event_type == "ResolvedEvaluationContractRegistered"
                and event.event_hash in source_hashes
                and event.payload["contract_hash"] == payload["resolved_contract_hash"]
            ]
            if factor_event is None or len(contract_events) != 1:
                raise ValueError("applicability source events are incomplete")
            contract_event = contract_events[0]
            contract = _contract_from_event(self, contract_event)
            rebuilt = build_assessment(
                contract_event=contract_event,
                contract=contract,
                factor_event=factor_event,
                claim_type=str(payload["claim_type"]),
            )
            identifier = "applicability-" + rebuilt.assessment_hash.removeprefix("sha256:")[:24]
            expected = {"assessment_id": identifier, **rebuilt.to_dict()}
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("applicability assessment cannot be independently rebuilt") from exc
        if dict(payload) != expected:
            raise EventValidationError("applicability assessment differs from deterministic source replay")

    def _validate_external_scorecard_evidence_v3(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ScorecardDecisionEvidenceV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.decision-evidence-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("scorecard evidence requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.decision_v2.evidence_v3 import (
                DecisionEvidenceArtifactStoreV3,
            )
            from src.alpha_quality.decision_v2.scorecard_evidence_v3 import (
                DecisionScorecardEvidenceServiceV3,
            )

            record = DecisionEvidenceArtifactStoreV3(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_evidence_hash=str(payload["evidence_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt = DecisionScorecardEvidenceServiceV3.rebuild_record(
                self,
                factor_spec_id=record.factor_spec_id,
                evaluation_policy_event_hash=str(record.evidence_payload["evaluation_policy_event_hash"]),
                snapshot_event_hash=str(record.evidence_payload["snapshot_event_hash"]),
                trial_id=str(payload["trial_id"]),
                run_id=record.evidence_run_id,
                source_watermark_event_hash=str(record.evidence_payload["source_watermark_event_hash"]),
            ).record
            expected = DecisionScorecardEvidenceServiceV3.event_payload(
                DecisionScorecardEvidenceServiceV3.evidence_id(record.evidence_hash),
                str(payload["trial_id"]),
                record,
                reference,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("scorecard evidence artifact or source replay is invalid") from exc
        if record.evidence_kind != "scorecard" or rebuilt != record:
            raise EventValidationError("scorecard evidence differs from deterministic source replay")
        if dict(payload) != expected:
            raise EventValidationError("scorecard evidence event differs from producer artifact")

    def _validate_external_snapshot_evidence_v3(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "SnapshotDecisionEvidenceV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.decision-evidence-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("snapshot evidence requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.decision_v2.evidence_v3 import (
                DecisionEvidenceArtifactStoreV3,
            )
            from src.alpha_quality.decision_v2.snapshot_evidence_v3 import (
                DecisionSnapshotEvidenceServiceV3,
            )

            record = DecisionEvidenceArtifactStoreV3(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_evidence_hash=str(payload["evidence_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            rebuilt = DecisionSnapshotEvidenceServiceV3.rebuild_record(
                self,
                factor_spec_id=record.factor_spec_id,
                evaluation_policy_event_hash=str(record.evidence_payload["evaluation_policy_event_hash"]),
                snapshot_event_hash=str(record.evidence_payload["snapshot_event_hash"]),
                run_id=record.evidence_run_id,
                source_watermark_event_hash=str(record.evidence_payload["source_watermark_event_hash"]),
            ).record
            expected = DecisionSnapshotEvidenceServiceV3.event_payload(
                DecisionSnapshotEvidenceServiceV3.evidence_id(record.evidence_hash),
                record,
                reference,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("snapshot evidence artifact or source replay is invalid") from exc
        if record.evidence_kind != "snapshot" or rebuilt != record:
            raise EventValidationError("snapshot evidence differs from deterministic source replay")
        if dict(payload) != expected:
            raise EventValidationError("snapshot evidence event differs from producer artifact")

    def _validate_external_pit_adapter_registration(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "AsharePITAdapterRegistered":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.registered-ashare-pit-adapter-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("PIT adapter registration requires one producer artifact")
        reference = references[0]
        try:
            from src.alpha_quality.pit_service_v2 import (
                AsharePITAdapterRegistrationArtifactStoreV1,
                AsharePITAdapterRegistrationServiceV1,
            )

            record = AsharePITAdapterRegistrationArtifactStoreV1(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_artifact_hash=str(payload["registration_artifact_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = AsharePITAdapterRegistrationServiceV1.event_payload(
                AsharePITAdapterRegistrationServiceV1.registration_id(str(payload["registration_hash"])),
                record,
                (
                    None
                    if payload["source_watermark_event_hash"] is None
                    else str(payload["source_watermark_event_hash"])
                ),
                reference,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("PIT adapter registration cannot be rebuilt") from exc
        if dict(payload) != expected:
            raise EventValidationError("PIT adapter registration differs from producer artifact")

    def _validate_external_pit_snapshot(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "AsharePITSnapshotRecorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.ashare-pit-snapshot-v2+json"
        ]
        if len(references) != 1:
            raise EventValidationError("PIT snapshot requires one producer manifest")
        reference = references[0]
        try:
            from src.alpha_quality.pit_artifact_v2 import (
                FrozenAsharePITSnapshotArtifactStoreV2,
            )
            from src.alpha_quality.pit_service_v2 import AsharePITSnapshotServiceV2

            snapshot = FrozenAsharePITSnapshotArtifactStoreV2(self.artifact_root).read_manifest(
                str(reference["relative_path"]),
                expected_snapshot_hash=str(payload["snapshot_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            # The event run is the registered policy run; derive it from the source.
            policy_events = [
                event
                for event in self.query_events(event_type="EvaluationPolicyRegistered")
                if event.event_hash == payload["evaluation_policy_event_hash"]
            ]
            if len(policy_events) != 1:
                raise ValueError("PIT snapshot policy event is missing")
            rebuilt = AsharePITSnapshotServiceV2.rebuild_snapshot(
                self,
                snapshot=snapshot,
                adapter_registration_event_hash=str(payload["adapter_registration_event_hash"]),
                evaluation_policy_event_hash=str(payload["evaluation_policy_event_hash"]),
                run_id=policy_events[0].run_id,
            )
            expected = AsharePITSnapshotServiceV2.event_payload(
                AsharePITSnapshotServiceV2.snapshot_id(snapshot.snapshot_hash),
                rebuilt,
                str(payload["source_watermark_event_hash"]),
                reference,
                adapter_registration_event_hash=str(payload["adapter_registration_event_hash"]),
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("PIT snapshot manifest or source replay is invalid") from exc
        if rebuilt != snapshot or dict(payload) != expected:
            raise EventValidationError("PIT snapshot differs from deterministic source replay")

    def _validate_external_predictive_v4(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        event_types = {
            "FactorOutputRecordedV3",
            "ObservedPanelPredictiveEvidenceRecorded",
            "PITPredictiveEvidenceRecorded",
            "ScorecardDecisionEvidenceV4Recorded",
        }
        if event_type not in event_types:
            return
        try:
            from src.alpha_quality.predictive_evidence_v4 import (
                FACTOR_OUTPUT_MEDIA_TYPE,
                PREDICTIVE_EVIDENCE_MEDIA_TYPE,
                SCORECARD_V4_MEDIA_TYPE,
                FactorOutputArtifactStoreV3,
                PITPredictiveEvidenceServiceV4,
                PredictiveEvidenceV1,
                ScorecardDecisionEvidenceV4,
                _JsonEvidenceStore,
                _PREDICTIVE_KEYS,
                _SCORECARD_KEYS,
            )

            events = {event.event_hash: event for event in self.query_events()}
            if event_type == "FactorOutputRecordedV3":
                ref = next(
                    reference
                    for reference in payload["artifact_refs"]
                    if reference["media_type"] == FACTOR_OUTPUT_MEDIA_TYPE
                )
                artifact = FactorOutputArtifactStoreV3(self.artifact_root).read_manifest(
                    str(ref["relative_path"]),
                    expected_hash=str(payload["factor_output_hash"]),
                    expected_blob_hash=str(ref["artifact_hash"]),
                )
                source_events = [
                    events[event_hash] for event_hash in payload["source_event_hashes"] if event_hash in events
                ]
                contract = next(
                    event for event in source_events if event.event_type == "ResolvedEvaluationContractRegistered"
                )
                definition = next(event for event in source_events if event.event_type == "FactorDefinitionRecorded")
                snapshot = next(event for event in source_events if event.event_type == "AsharePITSnapshotRecorded")
                rebuilt, _, _ = PITPredictiveEvidenceServiceV4.rebuild(
                    self,
                    run_id=artifact.run_id,
                    contract_event_hash=contract.event_hash,
                    factor_definition_event_hash=definition.event_hash,
                    pit_snapshot_event_hash=snapshot.event_hash,
                    persist_factor=True,
                )
                expected = PITPredictiveEvidenceServiceV4.factor_event_payload(rebuilt, ref)
                if rebuilt != artifact or dict(payload) != expected:
                    raise ValueError("factor output differs from backend replay")
                return

            if event_type in {
                "ObservedPanelPredictiveEvidenceRecorded",
                "PITPredictiveEvidenceRecorded",
            }:
                ref = next(
                    reference
                    for reference in payload["artifact_refs"]
                    if reference["media_type"] == PREDICTIVE_EVIDENCE_MEDIA_TYPE
                )
                raw = _JsonEvidenceStore(
                    self.artifact_root,
                    namespace="predictive-evidence-v1",
                    media_type=PREDICTIVE_EVIDENCE_MEDIA_TYPE,
                    keys=_PREDICTIVE_KEYS,
                    semantic_field="evidence_hash",
                ).read(
                    str(ref["relative_path"]),
                    expected_hash=str(payload["evidence_hash"]),
                    expected_blob_hash=str(ref["artifact_hash"]),
                )
                predictive_artifact = PredictiveEvidenceV1.from_dict(raw)
                factor_event = events[str(payload["factor_output_event_hash"])]
                factor_ref = factor_event.payload["artifact_refs"][0]
                factor_artifact = FactorOutputArtifactStoreV3(self.artifact_root).read_manifest(
                    str(factor_ref["relative_path"]),
                    expected_hash=str(factor_event.payload["factor_output_hash"]),
                    expected_blob_hash=str(factor_ref["artifact_hash"]),
                )
                source_events = [events[item] for item in factor_artifact.source_event_hashes]
                contract = next(
                    event for event in source_events if event.event_type == "ResolvedEvaluationContractRegistered"
                )
                definition = next(event for event in source_events if event.event_type == "FactorDefinitionRecorded")
                snapshot = next(event for event in source_events if event.event_type == "AsharePITSnapshotRecorded")
                _, observed, pit = PITPredictiveEvidenceServiceV4.rebuild(
                    self,
                    run_id=factor_artifact.run_id,
                    contract_event_hash=contract.event_hash,
                    factor_definition_event_hash=definition.event_hash,
                    pit_snapshot_event_hash=snapshot.event_hash,
                    persist_factor=True,
                )
                expected_artifact = PITPredictiveEvidenceServiceV4._with_factor_event(
                    observed if event_type == "ObservedPanelPredictiveEvidenceRecorded" else pit,
                    factor_event.event_hash,
                )
                expected = PITPredictiveEvidenceServiceV4.predictive_event_payload(expected_artifact, ref)
                if expected_artifact != predictive_artifact or dict(payload) != expected:
                    raise ValueError("predictive evidence differs from source replay")
                return

            ref = next(
                reference
                for reference in payload["artifact_refs"]
                if reference["media_type"] == SCORECARD_V4_MEDIA_TYPE
            )
            raw = _JsonEvidenceStore(
                self.artifact_root,
                namespace="scorecard-evidence-v4",
                media_type=SCORECARD_V4_MEDIA_TYPE,
                keys=_SCORECARD_KEYS,
                semantic_field="scorecard_evidence_hash",
            ).read(
                str(ref["relative_path"]),
                expected_hash=str(payload["scorecard_evidence_hash"]),
                expected_blob_hash=str(ref["artifact_hash"]),
            )
            scorecard_artifact = ScorecardDecisionEvidenceV4.from_dict(raw)
            factor_event = events[str(payload["factor_output_event_hash"])]
            observed_event = events[str(payload["observed_event_hash"])]
            pit_event = events[str(payload["pit_event_hash"])]
            factor_ref = factor_event.payload["artifact_refs"][0]
            factor_artifact = FactorOutputArtifactStoreV3(self.artifact_root).read_manifest(
                str(factor_ref["relative_path"]),
                expected_hash=str(factor_event.payload["factor_output_hash"]),
                expected_blob_hash=str(factor_ref["artifact_hash"]),
            )
            service = PITPredictiveEvidenceServiceV4(self)
            observed = service._read_predictive_event(observed_event)
            pit = service._read_predictive_event(pit_event)
            rebuilt_scorecard = service._scorecard(
                factor=factor_artifact,
                observed=observed,
                pit=pit,
                factor_event_hash=factor_event.event_hash,
                observed_event_hash=observed_event.event_hash,
                pit_event_hash=pit_event.event_hash,
            )
            expected = service.scorecard_event_payload(rebuilt_scorecard, ref)
            if rebuilt_scorecard != scorecard_artifact or dict(payload) != expected:
                raise ValueError("scorecard v4 differs from source replay")
        except (KeyError, StopIteration, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("predictive v4 artifact or source replay is invalid") from exc

    def _validate_external_production_evaluator(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        event_types = {
            "ProductionEvaluationNodeRecorded",
            "TrialTerminalDossierRecorded",
            "ReportMaterializationFailed",
        }
        if event_type not in event_types:
            return
        try:
            from src.alpha_quality.production_evaluator_v1 import (
                DOSSIER_MEDIA_TYPE,
                PRODUCER_POLICY_HASH,
                PRODUCER_SCHEMA_VERSION,
                ProductionCandidateEvaluatorV1,
                ProductionEvaluationNodeV1,
                TrialTerminalDossierArtifactStoreV1,
            )

            if (
                payload["producer_schema_version"] != PRODUCER_SCHEMA_VERSION
                or payload["producer_policy_hash"] != PRODUCER_POLICY_HASH
            ):
                raise ValueError("production evaluator producer identity differs")
            if event_type == "ProductionEvaluationNodeRecorded":
                node = ProductionEvaluationNodeV1(
                    run_id=str(payload["run_id"]),
                    trial_id=str(payload["trial_id"]),
                    factor_spec_id=str(payload["factor_spec_id"]),
                    resolved_contract_hash=str(payload["resolved_contract_hash"]),
                    node_name=str(payload["node_name"]),  # type: ignore[arg-type]
                    status=str(payload["status"]),  # type: ignore[arg-type]
                    reason_codes=tuple(str(item) for item in payload["reason_codes"]),
                    source_event_hashes=tuple(str(item) for item in payload["source_event_hashes"]),
                    producer_schema_version=str(payload["producer_schema_version"]),
                    producer_policy_hash=str(payload["producer_policy_hash"]),
                    node_hash=str(payload["node_hash"]),
                )
                expected_id = f"production-node-{node.trial_id}-{node.node_name}"
                if payload["node_id"] != expected_id:
                    raise ValueError("production node identity differs")
                return
            if event_type == "ReportMaterializationFailed":
                if payload["failure_id"] != f"report-failure-{payload['trial_id']}":
                    raise ValueError("report failure identity differs")
                return
            references = [item for item in payload["artifact_refs"] if item["media_type"] == DOSSIER_MEDIA_TYPE]
            if len(references) != 1:
                raise ValueError("terminal dossier artifact is ambiguous")
            reference = references[0]
            dossier = TrialTerminalDossierArtifactStoreV1(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_hash=str(payload["terminal_dossier_hash"]),
                expected_blob_hash=str(reference["artifact_hash"]),
            )
            expected = ProductionCandidateEvaluatorV1.dossier_event_payload(
                dossier,
                reference,
            )
            by_hash = {event.event_hash: event for event in self.query_events()}
            evidence = [by_hash[item] for item in dossier.evidence_event_hashes]
            nodes = [by_hash[item] for item in dossier.node_event_hashes]
            terminal = by_hash[dossier.terminal_event_hash]
            evaluation = None if dossier.evaluation_event_hash is None else by_hash[dossier.evaluation_event_hash]
            expected_bundle_hash = canonical_json_hash(
                {
                    "schema_version": "production_evidence_bundle.v1",
                    "evidence_payload_hashes": sorted(event.payload_hash for event in evidence),
                    "node_hashes": sorted(str(event.payload["node_hash"]) for event in nodes),
                }
            )
            contract_matches = [
                event
                for event in by_hash.values()
                if event.event_type == "ResolvedEvaluationContractRegistered"
                and event.payload["contract_hash"] == dossier.resolved_contract_hash
                and event.run_id == dossier.run_id
            ]
            snapshot = by_hash[dossier.snapshot_event_hash]
            watermark = by_hash[dossier.source_watermark_event_hash]
            if (
                dict(payload) != expected
                or expected_bundle_hash != dossier.evidence_bundle_hash
                or len(contract_matches) != 1
                or snapshot.event_type != "AsharePITSnapshotRecorded"
                or snapshot.run_id != dossier.run_id
                or watermark.event_type != "FactorDefinitionRecorded"
                or watermark.entity_id != dossier.factor_spec_id
                or watermark.run_id != dossier.run_id
                or terminal.event_type != "TrialTerminated"
                or terminal.run_id != dossier.run_id
                or terminal.payload["trial_id"] != dossier.trial_id
                or (
                    evaluation is not None
                    and (
                        evaluation.event_type != "EvaluationRecorded"
                        or evaluation.run_id != dossier.run_id
                        or evaluation.payload["trial_id"] != dossier.trial_id
                        or evaluation.payload["factor_spec_id"] != dossier.factor_spec_id
                        or evaluation.payload["metadata"].get("evidence_bundle_hash") != dossier.evidence_bundle_hash
                    )
                )
            ):
                raise ValueError("terminal dossier event differs from artifact")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("production evaluator artifact or source replay is invalid") from exc

    def _validate_external_execution_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ExecutionEvidenceRecorded":
            return
        try:
            from src.alpha_quality.execution_evidence_v1 import (
                EXECUTION_ARTIFACT_MEDIA_TYPE,
                EXECUTION_DECISION_MEDIA_TYPE,
                ExecutionEvidenceArtifactStoreV1,
                ExecutionEvidenceServiceV1,
            )

            artifact_refs = [
                item for item in payload["artifact_refs"] if item["media_type"] == EXECUTION_ARTIFACT_MEDIA_TYPE
            ]
            decision_refs = [
                item for item in payload["artifact_refs"] if item["media_type"] == EXECUTION_DECISION_MEDIA_TYPE
            ]
            if len(artifact_refs) != 1 or len(decision_refs) != 1:
                raise ValueError("execution evidence artifacts are ambiguous")
            artifact_ref = artifact_refs[0]
            decision_ref = decision_refs[0]
            artifacts = ExecutionEvidenceArtifactStoreV1(self.artifact_root)
            artifact = artifacts.read_artifact(
                str(artifact_ref["relative_path"]),
                expected_hash=str(payload["execution_artifact_hash"]),
                expected_blob_hash=str(artifact_ref["artifact_hash"]),
            )
            decision = artifacts.read_decision(
                str(decision_ref["relative_path"]),
                expected_hash=str(payload["execution_decision_evidence_hash"]),
                expected_blob_hash=str(decision_ref["artifact_hash"]),
            )
            rebuilt_artifact, rebuilt_decision, _ = ExecutionEvidenceServiceV1.rebuild(
                self,
                run_id=artifact.run_id,
                factor_output_event_hash=artifact.factor_output_event_hash,
                observed_predictive_event_hash=str(payload["observed_predictive_event_hash"]),
                persist_tables=True,
            )
            expected = ExecutionEvidenceServiceV1.event_payload(
                rebuilt_artifact,
                rebuilt_decision,
                artifact_ref,
                decision_ref,
            )
            if rebuilt_artifact != artifact or rebuilt_decision != decision or dict(payload) != expected:
                raise ValueError("execution evidence differs from source replay")
        except (KeyError, StopIteration, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("execution evidence artifact or source replay is invalid") from exc

    def _validate_external_secondary_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type not in {"ComparisonPoolFrozen", "SecondaryEvidenceRecorded"}:
            return
        try:
            from src.alpha_quality.secondary_evidence_v1 import (
                PRODUCER_POLICY_HASH,
                PRODUCER_SCHEMA_VERSION,
                ComparisonPoolMemberV1,
                FrozenComparisonPoolV1,
                SecondaryEvidencePolicyV1,
            )

            if (
                payload["producer_schema_version"] != PRODUCER_SCHEMA_VERSION
                or payload["producer_policy_hash"] != PRODUCER_POLICY_HASH
            ):
                raise ValueError("secondary producer identity differs")
            if event_type == "ComparisonPoolFrozen":
                policy = SecondaryEvidencePolicyV1(**dict(payload["policy"]))
                members = tuple(ComparisonPoolMemberV1(**dict(item)) for item in payload["members"])
                rebuilt = FrozenComparisonPoolV1.build(
                    source_watermark_event_hash=str(payload["source_watermark_event_hash"]),
                    members=members,
                    policy=policy,
                    evidence_watermark=int(payload["evidence_watermark"]),
                )
                expected_id = "comparison-pool-" + rebuilt.comparison_pool_hash.removeprefix("sha256:")[:24]
                if dict(payload) != {
                    "pool_id": expected_id,
                    **rebuilt.to_dict(),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                }:
                    raise ValueError("comparison pool does not replay")
                return
            bundle = payload["secondary_evidence_bundle"]
            for name in (
                "identity",
                "residual_prediction",
                "portfolio_marginal_value",
                "mechanism",
            ):
                assessment = dict(bundle[name])
                assessment_hash = assessment.pop("assessment_hash")
                if assessment_hash != canonical_json_hash(assessment):
                    raise ValueError("secondary subassessment does not replay")
            bundle_hash = canonical_json_hash(bundle)
            expected_id = "secondary-evidence-" + bundle_hash.removeprefix("sha256:")[:24]
            if (
                payload["secondary_evidence_bundle_hash"] != bundle_hash
                or payload["evidence_id"] != expected_id
                or payload["identity_assessment_hash"] != bundle["identity"]["assessment_hash"]
                or payload["residual_assessment_hash"] != bundle["residual_prediction"]["assessment_hash"]
                or payload["portfolio_assessment_hash"] != bundle["portfolio_marginal_value"]["assessment_hash"]
                or payload["mechanism_assessment_hash"] != bundle["mechanism"]["assessment_hash"]
                or payload["novelty_claim"] != bundle["identity"]["novelty_claim"]
                or payload["replication_claim"] != bundle["identity"]["replication_claim"]
                or payload["residual_status"] != bundle["residual_prediction"]["status"]
                or payload["portfolio_status"] != bundle["portfolio_marginal_value"]["status"]
                or payload["mechanism_status"] != bundle["mechanism"]["status"]
                or payload["promotion_effect"] != "none"
            ):
                raise ValueError("secondary evidence envelope differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError("secondary evidence replay is invalid") from exc

    def _validate_external_claim_decision(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type not in {
            "SelectionAssessmentRecorded",
            "ClaimMatrixRecorded",
            "QualityDecisionV4Recorded",
        }:
            return
        try:
            from src.alpha_quality.claim_decision_v1 import (
                PRODUCER_POLICY_HASH,
                PRODUCER_SCHEMA_VERSION,
                TIER_INVARIANT_MANIFEST_HASH,
            )

            if (
                payload["producer_schema_version"] != PRODUCER_SCHEMA_VERSION
                or payload["producer_policy_hash"] != PRODUCER_POLICY_HASH
            ):
                raise ValueError("claim decision producer identity differs")
            identity = {
                "SelectionAssessmentRecorded": ("assessment_id", "selection-", "assessment_hash"),
                "ClaimMatrixRecorded": ("matrix_id", "claim-matrix-", "claim_matrix_hash"),
                "QualityDecisionV4Recorded": ("decision_id", "quality-decision-v4-", "decision_hash"),
            }[event_type]
            expected = identity[1] + str(payload[identity[2]]).removeprefix("sha256:")[:24]
            if payload[identity[0]] != expected:
                raise ValueError("claim decision event identity differs")
            if (
                event_type == "QualityDecisionV4Recorded"
                and payload["tier_invariant_manifest_hash"] != TIER_INVARIANT_MANIFEST_HASH
            ):
                raise ValueError("narrow decision invariant manifest differs")
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError("claim decision replay is invalid") from exc

    def _validate_external_research_dossier(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type not in {
            "ResearchDossierRecorded",
            "ExperimentRunReportRecorded",
            "ResearchReleaseManifestRecorded",
            "ResearchDossierMaterializationFailed",
        }:
            return
        try:
            from src.alpha_quality.research_dossier_v1 import (
                AUDIENCE_VIEW_MEDIA_TYPE,
                CANDIDATE_DOSSIER_MEDIA_TYPE,
                PRODUCER_POLICY_HASH,
                PRODUCER_SCHEMA_VERSION,
                RELEASE_MANIFEST_MEDIA_TYPE,
                RUN_REPORT_MEDIA_TYPE,
                CandidateResearchDossierV1,
                ExperimentRunReportV1,
                ResearchDossierArtifactStoreV1,
                ResearchReleaseManifestV1,
                _strict_json,
            )

            if (
                payload["producer_schema_version"] != PRODUCER_SCHEMA_VERSION
                or payload["producer_policy_hash"] != PRODUCER_POLICY_HASH
            ):
                raise ValueError("research dossier producer identity differs")
            if event_type == "ResearchDossierMaterializationFailed":
                expected_id = "research-dossier-failure-" + str(payload["trial_id"])
                if payload["failure_id"] != expected_id or payload["decision_effect"] != "none":
                    raise ValueError("research dossier failure identity differs")
                return
            artifacts = ResearchDossierArtifactStoreV1(self.artifact_root)
            if event_type == "ResearchDossierRecorded":
                candidate_refs = [
                    ref for ref in payload["artifact_refs"] if ref["media_type"] == CANDIDATE_DOSSIER_MEDIA_TYPE
                ]
                view_refs = [ref for ref in payload["artifact_refs"] if ref["media_type"] == AUDIENCE_VIEW_MEDIA_TYPE]
                if len(candidate_refs) != 1 or len(view_refs) != 5:
                    raise ValueError("research dossier artifact inventory differs")
                dossier_raw = artifacts.read(
                    candidate_refs[0],
                    semantic_hash=str(payload["dossier_hash"]),
                    namespace="candidate-research-dossier-v1",
                    media_type=CANDIDATE_DOSSIER_MEDIA_TYPE,
                    semantic_field="dossier_hash",
                )
                dossier = CandidateResearchDossierV1(dossier_raw)
                views = [
                    artifacts.read(
                        ref,
                        semantic_hash=str(
                            _strict_json(self.artifact_root.joinpath(*str(ref["relative_path"]).split("/")))[
                                "view_hash"
                            ]
                        ),
                        namespace="research-audience-view-v1",
                        media_type=AUDIENCE_VIEW_MEDIA_TYPE,
                        semantic_field="view_hash",
                    )
                    for ref in view_refs
                ]
                for view in views:
                    content = {key: value for key, value in view.items() if key != "view_hash"}
                    if canonical_json_hash(content) != view["view_hash"]:
                        raise ValueError("research audience view hash differs")
                expected_id = "research-dossier-" + dossier.dossier_hash.removeprefix("sha256:")[:24]
                if (
                    payload["dossier_id"] != expected_id
                    or payload["factor_spec_id"] != dossier.payload["factor_spec_id"]
                    or payload["source_event_hashes"] != dossier.payload["source_event_hashes"]
                    or payload["view_hashes"] != sorted(str(view["view_hash"]) for view in views)
                ):
                    raise ValueError("research dossier event differs from artifacts")
                return
            if event_type == "ExperimentRunReportRecorded":
                ref = payload["artifact_refs"][0]
                raw = artifacts.read(
                    ref,
                    semantic_hash=str(payload["run_report_hash"]),
                    namespace="experiment-run-report-v1",
                    media_type=RUN_REPORT_MEDIA_TYPE,
                    semantic_field="run_report_hash",
                )
                report = ExperimentRunReportV1(raw)
                expected_id = "run-report-" + report.report_hash.removeprefix("sha256:")[:24]
                if (
                    payload["report_id"] != expected_id
                    or payload["trial_count"] != report.payload["trial_count"]
                    or payload["candidate_dossier_hashes"] != report.payload["candidate_dossier_hashes"]
                    or payload["source_event_hashes"] != report.payload["source_event_hashes"]
                ):
                    raise ValueError("run report event differs from artifact")
                return
            ref = payload["artifact_refs"][0]
            raw = artifacts.read(
                ref,
                semantic_hash=str(payload["manifest_hash"]),
                namespace="research-release-manifest-v1",
                media_type=RELEASE_MANIFEST_MEDIA_TYPE,
                semantic_field="manifest_hash",
            )
            manifest = ResearchReleaseManifestV1(raw)
            expected_id = "research-release-" + manifest.manifest_hash.removeprefix("sha256:")[:24]
            if (
                payload["manifest_id"] != expected_id
                or payload["engineering_status"] != manifest.payload["engineering_status"]
                or payload["empirical_status"] != manifest.payload["empirical_status"]
                or payload["source_event_hashes"] != manifest.payload["source_event_hashes"]
            ):
                raise ValueError("release manifest event differs from artifact")
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("research dossier artifact replay is invalid") from exc

    def _validate_external_quality_decision_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild a source-bound quality decision before locking or replaying."""
        if event_type != "QualityDecisionV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.quality-decision-input-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Decision v3 requires one source input bundle")
        reference = references[0]
        try:
            from src.alpha_quality.decision_v2.runner import QualityDecisionV2Runner
            from src.alpha_quality.decision_v2.authority_gate_v1 import (
                QualityDecisionAuthorityGateV1,
            )
            from src.alpha_quality.decision_v2.source_v3 import (
                FrozenDecisionEvidenceRepository,
                QualityDecisionInputArtifactStoreV3,
                decision_v2_policy_from_mapping,
                source_bound_quality_decision_content,
            )

            bundle = QualityDecisionInputArtifactStoreV3(self.artifact_root).read(
                str(reference["relative_path"]),
                expected_bundle_hash=str(payload["input_bundle_hash"]),
            )
            policy = decision_v2_policy_from_mapping(bundle.policy_config)
            decision = QualityDecisionAuthorityGateV1(
                QualityDecisionV2Runner(
                    flags=self.flags,
                    policy=policy,
                    repository=FrozenDecisionEvidenceRepository(bundle.evidence_records),
                )
            ).run(bundle.evidence_refs)
            content = source_bound_quality_decision_content(
                decision,
                input_bundle_hash=bundle.bundle_hash,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Decision v3 source evidence cannot rebuild the decision") from exc
        expected = {
            "decision_hash": canonical_json_hash(content),
            **{key: value for key, value in content.items() if key != "schema_version"},
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Decision v3 differs from deterministic rebuild")

    def _validate_external_activation_source_audit(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationRunSourceAudited":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-run-source-v2+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation source audit requires one v2 artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.run_source_v2 import (
                ActivationRunSourceAuditV2,
            )

            raw = ActivationArtifactStore(self.artifact_root).get("run_source", str(payload["audit_hash"]))
            expected_relative = ActivationArtifactStore.relative_path("run_source", str(payload["audit_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("Activation source audit reference path is not canonical")
            audit = ActivationRunSourceAuditV2.from_dict(raw)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("Activation source audit artifact is invalid") from exc
        expected = {
            "plan_hash": audit.plan_hash,
            "summary_manifest_hash": audit.summary_manifest_hash,
            "source_watermark_event_hash": audit.source_watermark_event_hash,
            "audit_hash": audit.audit_hash,
            "retriever_decision_event_hashes": list(audit.retriever_decision_event_hashes),
            "terminal_event_hashes": list(audit.terminal_event_hashes),
            "evaluation_event_hashes": list(audit.evaluation_event_hashes),
            "quality_decision_event_hashes": list(audit.quality_decision_event_hashes),
            "source_failure_codes": list(audit.source_failure_codes),
            "source_complete": audit.source_complete,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation source audit event differs from artifact")
        events = self.query_events()
        indexes = {event.event_hash: index for index, event in enumerate(events)}
        watermark_index = indexes.get(audit.source_watermark_event_hash)
        source_hashes = (
            audit.retriever_decision_event_hashes
            + audit.terminal_event_hashes
            + audit.evaluation_event_hashes
            + audit.quality_decision_event_hashes
        )
        if watermark_index is None or any(
            indexes.get(event_hash, watermark_index + 1) > watermark_index for event_hash in source_hashes
        ):
            raise EventValidationError("Activation source audit cites an invalid watermark")
        summaries = [
            event
            for event in events
            if event.event_type == "ActivationRunRecorded"
            and event.payload["manifest_hash"] == audit.summary_manifest_hash
            and event.payload["plan_hash"] == audit.plan_hash
        ]
        if len(summaries) != 1:
            raise EventValidationError("Activation source audit lacks one prior run summary")

    def _validate_external_official_control_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "OfficialSearchControlRecorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.official-search-control-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("official control requires one source artifact")
        try:
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )

            artifact_store = OfficialSearchControlArtifactStoreV1(self.artifact_root)
            evidence = artifact_store.read(
                str(references[0]["relative_path"]),
                str(payload["evidence_hash"]),
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("official control source cannot be replayed") from exc
        expected = {
            "evidence_hash": evidence.evidence_hash,
            "policy_hash": evidence.policy.policy_hash,
            "output_hash": evidence.output_hash,
            "search_run_id": evidence.run_id,
            "data_snapshot_hash": evidence.data_snapshot_hash,
            "candidate_count": len(evidence.candidates),
            "terminal_event_hashes": list(evidence.terminal_event_hashes),
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("official control event differs from source artifact")
        events = self.query_events()
        indexes = {event.event_hash: index for index, event in enumerate(events)}
        terminals: list[ResearchEventEnvelope] = []
        for event_hash in evidence.terminal_event_hashes:
            matches = [
                event
                for event in events
                if event.event_hash == event_hash
                and event.event_type == "TrialTerminated"
                and event.run_id == evidence.run_id
            ]
            if len(matches) != 1:
                raise EventValidationError("official control terminal source is missing")
            terminals.append(matches[0])
        terminals.sort(key=lambda event: indexes[event.event_hash])
        starts = {
            str(event.payload["trial_id"]): event
            for event in events
            if event.event_type == "TrialStarted" and event.run_id == evidence.run_id
        }
        for attempt_index, (candidate, terminal) in enumerate(
            zip(evidence.candidates, terminals, strict=True), start=1
        ):
            expected_trial_hash = canonical_json_hash(
                {
                    "schema_version": "event_sourced_search_trial_id.v1",
                    "run_id": evidence.run_id,
                    "attempt_index": attempt_index,
                    "candidate_id": candidate["candidate_id"],
                    "formula_hash": candidate["formula_hash"],
                    "data_snapshot_hash": evidence.data_snapshot_hash,
                }
            )
            expected_trial_id = "trial-search-" + expected_trial_hash.removeprefix("sha256:")[:24]
            trial_id = str(terminal.payload["trial_id"])
            start = starts.get(trial_id)
            if (
                trial_id != expected_trial_id
                or start is None
                or start.payload["candidate_id"] != candidate["candidate_id"]
            ):
                raise EventValidationError("official control trial order or snapshot binding is invalid")

    def _validate_external_prearm_flat_schedule(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type != "PreArmFlatScheduleFrozen":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.prearm-flat-schedule-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("pre-arm flat schedule requires one artifact")
        try:
            from src.alpha_foundry.flat_schedule_v1 import (
                PreArmFlatScheduleArtifactStoreV1,
            )

            schedule = PreArmFlatScheduleArtifactStoreV1(self.artifact_root).read(
                str(references[0]["relative_path"]),
                str(payload["schedule_hash"]),
            )
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore

            plans = [
                event
                for event in self.query_events(event_type="ActivationPlanRegistered")
                if event.payload["plan_hash"] == schedule.plan_hash
            ]
            if len(plans) != 1:
                raise ValueError("pre-arm flat schedule lacks a registered plan")
            plan = ActivationArtifactStore(self.artifact_root).get("plan", schedule.plan_hash)
            design = plan.get("design")
            provenance = plan.get("provenance")
            if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
                raise ValueError("pre-arm flat schedule plan is invalid")
            parts = schedule.pair_id.split(":", 2)
            if (
                len(parts) != 3
                or parts[0] != schedule.run_group_id
                or schedule.run_group_id not in design.get("run_group_ids", [])
                or parts[1] not in design.get("mechanism_families", [])
                or parts[2] not in design.get("dag_regions", [])
                or provenance.get("train_snapshot_hash") != schedule.data_snapshot_hash
                or provenance.get("control_policy_hash") != schedule.policy.policy_hash
                or design.get("candidate_budget") != schedule.policy.max_candidates
                or design.get("compute_budget") != schedule.policy.trial_budget
            ):
                raise ValueError("pre-arm flat schedule differs from registered plan")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("pre-arm flat schedule cannot replay") from exc
        expected = {
            "plan_hash": schedule.plan_hash,
            "pair_id": schedule.pair_id,
            "run_group_id": schedule.run_group_id,
            "data_snapshot_hash": schedule.data_snapshot_hash,
            "policy_hash": schedule.policy.policy_hash,
            "candidate_count": len(schedule.candidates),
            "output_hash": schedule.output_hash,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("pre-arm flat schedule event differs from artifact")

    def _validate_external_pair_execution_schedule(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type != "ActivationPairExecutionScheduled":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-pair-execution-schedule-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation pair schedule requires one artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.pair_schedule_v1 import (
                ActivationPairExecutionScheduleV1,
            )

            artifacts = ActivationArtifactStore(self.artifact_root)
            expected_path = artifacts.relative_path("pair_schedule", str(payload["schedule_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_path:
                raise ValueError("Activation pair schedule path is not canonical")
            schedule = ActivationPairExecutionScheduleV1.from_dict(
                artifacts.get("pair_schedule", str(payload["schedule_hash"]))
            )
            plan = artifacts.get("plan", schedule.plan_hash)
            rebuilt = ActivationPairExecutionScheduleV1.from_plan(
                plan_hash=schedule.plan_hash,
                plan=plan,
                run_group_id=schedule.run_group_id,
                mechanism_family=schedule.mechanism_family,
                dag_region=schedule.dag_region,
            )
            if rebuilt != schedule:
                raise ValueError("Activation pair schedule does not replay")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation pair schedule cannot replay") from exc
        raw = schedule.to_dict()
        expected = {key: raw[key] for key in payload if key not in {"schedule_id", "artifact_refs"}}
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation pair schedule event differs from artifact")

    def _validate_external_activation_resource(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationResourceMeasured":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-resource-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation resource requires one evidence artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.resource_v1 import (
                validate_resource_evidence_mapping,
            )

            artifacts = ActivationArtifactStore(self.artifact_root)
            expected_relative = artifacts.relative_path("resource", str(payload["evidence_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("Activation resource artifact path is not canonical")
            raw = artifacts.get("resource", str(payload["evidence_hash"]))
            evidence = validate_resource_evidence_mapping(raw)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("Activation resource artifact is invalid") from exc
        expected = {key: value for key, value in evidence.items() if key != "schema_version"}
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation resource event differs from artifact")

    def _validate_external_activation_resource_v2(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationResourceMeasuredV2":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-resource-v2+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation resource v2 requires one evidence artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.pair_schedule_v1 import (
                ActivationPairExecutionScheduleV1,
            )
            from src.alpha_foundry.activation.resource_v2 import (
                validate_resource_evidence_v2_mapping,
            )

            artifacts = ActivationArtifactStore(self.artifact_root)
            expected_relative = artifacts.relative_path("resource", str(payload["evidence_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("Activation resource v2 path is not canonical")
            evidence = validate_resource_evidence_v2_mapping(artifacts.get("resource", str(payload["evidence_hash"])))
            by_hash = {event.event_hash: event for event in self.query_events()}
            schedule_event = by_hash.get(str(evidence["pair_schedule_event_hash"]))
            claim_event = by_hash.get(str(evidence["execution_claim_event_hash"]))
            if (
                schedule_event is None
                or schedule_event.event_type != "ActivationPairExecutionScheduled"
                or claim_event is None
                or claim_event.event_type != "ActivationPairExecutionClaimed"
            ):
                raise ValueError("Activation resource v2 schedule authority is missing")
            schedule = ActivationPairExecutionScheduleV1.from_dict(
                artifacts.get("pair_schedule", str(evidence["pair_schedule_hash"]))
            )
            expected_policy_hash = canonical_json_hash(
                {
                    "schema_version": "activation_resource_measurement_policy.v2",
                    "wall_clock": "time.perf_counter.v1",
                    "cpu_clock": "time.process_time.v1",
                    "measurement_boundary": ("immediately_around_scheduled_arm_executor.v1"),
                    "arm_order_rule": schedule.order_rule,
                    "arm_order": list(schedule.arm_order),
                    "pair_schedule_event_hash": schedule_event.event_hash,
                    "pair_schedule_hash": schedule.schedule_hash,
                    "execution_claim_event_hash": claim_event.event_hash,
                    "timeout_limit_seconds": schedule.timeout_seconds,
                    "timeout_enforcement": "observed_not_enforced.v1",
                    "peak_rss_method": ("unavailable_without_isolated_worker.v1"),
                }
            )
            if (
                schedule_event.payload["schedule_hash"] != schedule.schedule_hash
                or claim_event.payload["schedule_event_hash"] != schedule_event.event_hash
                or claim_event.payload["schedule_hash"] != schedule.schedule_hash
                or schedule.plan_hash != evidence["plan_hash"]
                or schedule.pair_id != evidence["pair_id"]
                or schedule.run_group_id != evidence["run_group_id"]
                or schedule.arm_order[int(evidence["arm_order_position"])] != evidence["arm"]
                or schedule.timeout_seconds != evidence["timeout_limit_seconds"]
                or expected_policy_hash != evidence["measurement_policy_hash"]
            ):
                raise ValueError("Activation resource v2 schedule binding differs")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation resource v2 artifact is invalid") from exc
        expected = {key: value for key, value in evidence.items() if key != "schema_version"}
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation resource v2 event differs from artifact")

    def _validate_external_generation_consumption(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationGenerationConsumptionRecorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-generation-consumption-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation generation requires one source artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.generation_consumption_v1 import (
                ActivationGenerationConsumptionV1,
            )
            from src.alpha_foundry.dsl.identity import build_expression_identity
            from src.alpha_foundry.mutators import SeedMutator
            from src.alpha_foundry.retrieval.evidence_v4 import (
                RetrieverInputArtifactStoreV4,
            )
            from src.alpha_foundry.search import AlphaFoundrySearch
            from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank

            artifacts = ActivationArtifactStore(self.artifact_root)
            expected_relative = artifacts.relative_path("generation_consumption", str(payload["evidence_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("generation-consumption artifact path is not canonical")
            evidence = ActivationGenerationConsumptionV1.from_dict(
                artifacts.get("generation_consumption", str(payload["evidence_hash"]))
            )
            plan_matches = [
                event
                for event in self.query_events(event_type="ActivationPlanRegistered")
                if event.payload["plan_hash"] == evidence.plan_hash
            ]
            if len(plan_matches) != 1:
                raise ValueError("generation-consumption plan source is missing")
            plan = artifacts.get("plan", evidence.plan_hash)
            design = plan.get("design")
            provenance = plan.get("provenance")
            if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
                raise ValueError("generation-consumption plan is invalid")
            if (
                evidence.run_group_id not in design.get("run_group_ids", [])
                or evidence.mechanism_family not in design.get("mechanism_families", [])
                or evidence.dag_region not in design.get("dag_regions", [])
                or design.get("candidate_budget") != evidence.candidate_budget
                or design.get("compute_budget") != evidence.compute_budget
            ):
                raise ValueError("generation-consumption differs from frozen plan")
            retriever_matches = [
                event
                for event in self.query_events(event_type="RetrieverDecisionV4Recorded")
                if event.event_hash == evidence.retriever_decision_event_hash
            ]
            if len(retriever_matches) != 1:
                raise ValueError("generation-consumption retriever source is missing")
            retriever_event = retriever_matches[0]
            if (
                retriever_event.run_id != evidence.execution_run_id
                or retriever_event.payload["decision_hash"] != evidence.retriever_decision_hash
                or tuple(retriever_event.payload["selected_factor_spec_ids"])
                != evidence.selected_parent_factor_spec_ids
                or retriever_event.payload["control_evidence_event_hash"] != evidence.control_evidence_event_hash
                or provenance.get("treatment_policy_hash") != retriever_event.payload["policy_hash"]
                or provenance.get("train_snapshot_hash") != retriever_event.payload["data_snapshot_hash"]
            ):
                raise ValueError("generation-consumption retriever binding differs")
            bundle_refs = [
                reference
                for reference in retriever_event.payload["artifact_refs"]
                if reference["media_type"] == "application/vnd.vibe.retriever-input-bundle-v4+json"
            ]
            if len(bundle_refs) != 1:
                raise ValueError("generation-consumption retriever bundle is missing")
            bundle = RetrieverInputArtifactStoreV4(self.artifact_root).read(
                str(bundle_refs[0]["relative_path"]),
                str(retriever_event.payload["input_bundle_hash"]),
            )
            candidate_inputs = {item.factor_spec_id: item for item in bundle.retriever_input.candidates}
            for parent in evidence.selected_parents:
                source = candidate_inputs.get(str(parent["factor_spec_id"]))
                if source is None or (
                    build_expression_identity(str(parent["formula"])).canonical_ast != source.canonical_ast
                ):
                    raise ValueError("generation-consumption parent AST differs")
            control_matches = [
                event
                for event in self.query_events(event_type="OfficialSearchControlRecorded")
                if event.event_hash == evidence.control_evidence_event_hash
            ]
            if len(control_matches) != 1:
                raise ValueError("generation-consumption control source is missing")
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )

            control_event = control_matches[0]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(control_refs) != 1:
                raise ValueError("generation-consumption control artifact is missing")
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                str(control_event.payload["evidence_hash"]),
            )
            policy = control.policy
            if provenance.get("control_policy_hash") != policy.policy_hash:
                raise ValueError("generation-consumption control policy differs from plan")
            expected_policy_hash = canonical_json_hash(
                {
                    "generator_version": policy.generator_version,
                    "mutator_version": policy.mutator_version,
                    "mutation_templates": list(policy.mutation_templates),
                    "max_candidates_per_seed": policy.max_candidates_per_seed,
                    "max_candidates": evidence.candidate_budget,
                    "trial_budget": evidence.compute_budget,
                }
            )
            if expected_policy_hash != evidence.generator_policy_hash:
                raise ValueError("generation-consumption generator policy differs")
            replay = AlphaFoundrySearch(
                seed_bank=SeedBank(
                    [
                        AlphaSeed(
                            seed_id=str(parent["factor_spec_id"]),
                            formula=str(parent["formula"]),
                            source="content-addressed:" + str(parent["source_hash"]),
                        )
                        for parent in evidence.selected_parents
                    ]
                ),
                mutator=SeedMutator(max_candidates_per_seed=policy.max_candidates_per_seed),
                max_candidates=evidence.candidate_budget,
                trial_budget=evidence.compute_budget,
            ).generate()
            replay_records = tuple(
                (
                    candidate.candidate_id,
                    candidate.parent_seed_id,
                    candidate.formula_hash,
                )
                for candidate in replay.candidates
            )
            evidence_records = tuple(
                (
                    str(record["candidate_id"]),
                    str(record["parent_factor_spec_id"]),
                    str(record["formula_hash"]),
                )
                for record in evidence.generated_candidates
            )
            if replay_records != evidence_records:
                raise ValueError("generation-consumption search does not replay")
            all_events = self.query_events()
            by_hash = {event.event_hash: event for event in all_events}
            starts = {
                str(event.payload["trial_id"]): event for event in all_events if event.event_type == "TrialStarted"
            }
            for record in evidence.generated_candidates:
                terminal = by_hash.get(str(record["terminal_event_hash"]))
                if terminal is None or terminal.event_type != "TrialTerminated":
                    raise ValueError("generation-consumption terminal is missing")
                start = starts.get(str(terminal.payload["trial_id"]))
                if (
                    terminal.run_id != evidence.execution_run_id
                    or start is None
                    or start.run_id != evidence.execution_run_id
                    or start.payload["candidate_id"] != record["candidate_id"]
                ):
                    raise ValueError("generation-consumption trial binding differs")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation generation-consumption evidence is invalid") from exc
        expected = {
            "plan_hash": evidence.plan_hash,
            "pair_id": evidence.pair_id,
            "run_group_id": evidence.run_group_id,
            "mechanism_family": evidence.mechanism_family,
            "dag_region": evidence.dag_region,
            "execution_run_id": evidence.execution_run_id,
            "retriever_decision_event_hash": evidence.retriever_decision_event_hash,
            "retriever_decision_hash": evidence.retriever_decision_hash,
            "control_evidence_event_hash": evidence.control_evidence_event_hash,
            "generator_policy_hash": evidence.generator_policy_hash,
            "selected_parent_factor_spec_ids": list(evidence.selected_parent_factor_spec_ids),
            "consumed_parent_factor_spec_ids": list(evidence.consumed_parent_factor_spec_ids),
            "generated_candidate_count": len(evidence.generated_candidates),
            "candidate_budget": evidence.candidate_budget,
            "compute_budget": evidence.compute_budget,
            "source_failure_codes": list(evidence.source_failure_codes),
            "source_complete": evidence.source_complete,
            "evidence_hash": evidence.evidence_hash,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation generation event differs from its source artifact")

    def _validate_external_generation_consumption_v2(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationGenerationConsumptionV2Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-generation-consumption-v2+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation exact generation requires one source artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.generation_consumption_v2 import (
                ActivationGenerationConsumptionV2,
                ExactSelectedActionMutatorV2,
            )
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )
            from src.alpha_foundry.dsl.identity import build_expression_identity
            from src.alpha_foundry.mutators import (
                SEED_MUTATION_TEMPLATE_REGISTRY_V1,
            )
            from src.alpha_foundry.retrieval.evidence_v5 import (
                RetrieverInputArtifactStoreV5,
            )
            from src.alpha_foundry.search import AlphaFoundrySearch
            from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank

            artifacts = ActivationArtifactStore(self.artifact_root)
            kind: Literal["generation_consumption_v2"] = "generation_consumption_v2"
            expected_relative = artifacts.relative_path(kind, str(payload["evidence_hash"]))
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("exact generation artifact path is not canonical")
            evidence = ActivationGenerationConsumptionV2.from_dict(artifacts.get(kind, str(payload["evidence_hash"])))
            plans = [
                event
                for event in self.query_events(event_type="ActivationPlanRegistered")
                if event.payload["plan_hash"] == evidence.plan_hash
            ]
            if len(plans) != 1:
                raise ValueError("exact generation plan source is missing")
            plan = artifacts.get("plan", evidence.plan_hash)
            design = plan.get("design")
            provenance = plan.get("provenance")
            if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
                raise ValueError("exact generation plan is invalid")
            if (
                evidence.run_group_id not in design.get("run_group_ids", [])
                or evidence.mechanism_family not in design.get("mechanism_families", [])
                or evidence.dag_region not in design.get("dag_regions", [])
                or design.get("candidate_budget") != evidence.candidate_budget
                or design.get("compute_budget") != evidence.compute_budget
            ):
                raise ValueError("exact generation differs from frozen plan")
            retrievers = [
                event
                for event in self.query_events(event_type="RetrieverDecisionV5Recorded")
                if event.event_hash == evidence.retriever_decision_event_hash
            ]
            if len(retrievers) != 1:
                raise ValueError("exact generation v5 Retriever source is missing")
            retriever = retrievers[0]
            action_hash_by_id = {
                str(component["action_id"]): str(event_hash)
                for component, event_hash in zip(
                    retriever.payload["components"],
                    retriever.payload["action_template_event_hashes"],
                    strict=True,
                )
            }
            expected_selected_hashes = tuple(
                action_hash_by_id[action_id] for action_id in retriever.payload["selected_action_ids"]
            )
            if (
                retriever.run_id != evidence.execution_run_id
                or retriever.payload["decision_hash"] != evidence.retriever_decision_hash
                or tuple(retriever.payload["selected_action_ids"]) != evidence.selected_action_ids
                or tuple(retriever.payload["selected_parent_factor_spec_ids"])
                != evidence.selected_parent_factor_spec_ids
                or expected_selected_hashes != evidence.selected_action_event_hashes
                or retriever.payload["control_evidence_event_hash"] != evidence.control_evidence_event_hash
                or provenance.get("treatment_policy_hash") != retriever.payload["policy_hash"]
                or provenance.get("train_snapshot_hash") != retriever.payload["data_snapshot_hash"]
            ):
                raise ValueError("exact generation Retriever binding differs")
            bundle_refs = [
                reference
                for reference in retriever.payload["artifact_refs"]
                if reference["media_type"] == "application/vnd.vibe.retriever-input-bundle-v5+json"
            ]
            if len(bundle_refs) != 1:
                raise ValueError("exact generation v5 input bundle is missing")
            bundle = RetrieverInputArtifactStoreV5(self.artifact_root).read(
                str(bundle_refs[0]["relative_path"]),
                str(retriever.payload["input_bundle_hash"]),
            )
            source_by_action = {candidate.action_id: candidate for candidate in bundle.retriever_input.candidates}
            all_events = self.query_events()
            by_hash = {event.event_hash: event for event in all_events}
            for bound, parent in zip(
                evidence.selected_actions,
                evidence.selected_parents,
                strict=True,
            ):
                action_event = by_hash.get(bound.action_event_hash)
                source = source_by_action.get(bound.action.action_id)
                if (
                    action_event is None
                    or action_event.event_type != "RetrieverActionTemplateFrozen"
                    or action_event.run_id != evidence.execution_run_id
                    or action_event.payload != bound.action.to_dict()
                    or source is None
                    or source.factor_spec_id != parent["factor_spec_id"]
                    or build_expression_identity(str(parent["formula"])).canonical_ast != source.canonical_ast
                ):
                    raise ValueError("exact generation action or parent source differs")
            controls = [
                event
                for event in all_events
                if event.event_type == "OfficialSearchControlRecorded"
                and event.event_hash == evidence.control_evidence_event_hash
            ]
            if len(controls) != 1:
                raise ValueError("exact generation control source is missing")
            control_event = controls[0]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(control_refs) != 1:
                raise ValueError("exact generation control artifact is missing")
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                str(control_event.payload["evidence_hash"]),
            )
            if provenance.get("control_policy_hash") != control.policy.policy_hash:
                raise ValueError("exact generation control policy differs from plan")
            expected_policy_hash = canonical_json_hash(
                {
                    "schema_version": "activation_exact_action_generator_policy.v2",
                    "generator_version": control.policy.generator_version,
                    "mutator_version": "exact_selected_action_mutator.v2",
                    "template_registry_hash": (SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash),
                    "selection_order": "retriever_v5_selected_action_order.v1",
                    "selected_action_ids": list(evidence.selected_action_ids),
                    "selected_action_event_hashes": list(evidence.selected_action_event_hashes),
                    "max_candidates": evidence.candidate_budget,
                    "trial_budget": evidence.compute_budget,
                }
            )
            if expected_policy_hash != evidence.generator_policy_hash:
                raise ValueError("exact generation policy hash differs")
            parent_catalog = {
                str(parent["factor_spec_id"]): AlphaSeed(
                    seed_id=str(parent["factor_spec_id"]),
                    formula=str(parent["formula"]),
                    source="content-addressed:" + str(parent["source_hash"]),
                )
                for parent in evidence.selected_parents
            }
            mutator = ExactSelectedActionMutatorV2(evidence.selected_actions)
            replay = AlphaFoundrySearch(
                seed_bank=SeedBank(mutator.action_seeds(parent_catalog)),
                mutator=mutator,
                max_candidates=evidence.candidate_budget,
                trial_budget=evidence.compute_budget,
            ).generate()
            replay_records = tuple(
                (
                    str(candidate.metadata["retriever_action_id"]),
                    str(candidate.metadata["retriever_action_event_hash"]),
                    candidate.candidate_id,
                    candidate.parent_seed_id,
                    str(candidate.metadata["mutation"]),
                    candidate.formula_hash,
                )
                for candidate in replay.candidates
            )
            evidence_records = tuple(
                (
                    str(record["action_id"]),
                    str(record["action_event_hash"]),
                    str(record["candidate_id"]),
                    str(record["parent_factor_spec_id"]),
                    str(record["template_id"]),
                    str(record["formula_hash"]),
                )
                for record in evidence.generated_candidates
            )
            if replay_records != evidence_records:
                raise ValueError("exact generation search does not replay")
            starts = {
                str(event.payload["trial_id"]): event for event in all_events if event.event_type == "TrialStarted"
            }
            for record in evidence.generated_candidates:
                terminal = by_hash.get(str(record["terminal_event_hash"]))
                if terminal is None or terminal.event_type != "TrialTerminated":
                    raise ValueError("exact generation terminal is missing")
                start = starts.get(str(terminal.payload["trial_id"]))
                if (
                    terminal.run_id != evidence.execution_run_id
                    or start is None
                    or start.run_id != evidence.execution_run_id
                    or start.payload["candidate_id"] != record["candidate_id"]
                ):
                    raise ValueError("exact generation trial binding differs")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation exact generation-consumption evidence is invalid") from exc
        expected = {
            "plan_hash": evidence.plan_hash,
            "pair_id": evidence.pair_id,
            "run_group_id": evidence.run_group_id,
            "mechanism_family": evidence.mechanism_family,
            "dag_region": evidence.dag_region,
            "execution_run_id": evidence.execution_run_id,
            "retriever_decision_event_hash": evidence.retriever_decision_event_hash,
            "retriever_decision_hash": evidence.retriever_decision_hash,
            "control_evidence_event_hash": evidence.control_evidence_event_hash,
            "generator_policy_hash": evidence.generator_policy_hash,
            "selected_action_ids": list(evidence.selected_action_ids),
            "selected_action_event_hashes": list(evidence.selected_action_event_hashes),
            "selected_parent_factor_spec_ids": list(evidence.selected_parent_factor_spec_ids),
            "consumed_action_ids": list(evidence.consumed_action_ids),
            "consumed_parent_factor_spec_ids": list(evidence.consumed_parent_factor_spec_ids),
            "generated_candidate_count": len(evidence.generated_candidates),
            "candidate_budget": evidence.candidate_budget,
            "compute_budget": evidence.compute_budget,
            "source_failure_codes": list(evidence.source_failure_codes),
            "source_complete": evidence.source_complete,
            "evidence_hash": evidence.evidence_hash,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation exact generation event differs from source artifact")

    def _validate_external_generation_consumption_v3(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationGenerationConsumptionV3Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-generation-consumption-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation source-bound generation requires one artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.generation_consumption_v2 import (
                ExactSelectedActionMutatorV2,
            )
            from src.alpha_foundry.activation.generation_consumption_v3 import (
                ActivationGenerationConsumptionV3,
            )
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )
            from src.alpha_foundry.dsl.identity import build_expression_identity
            from src.alpha_foundry.mutators import SEED_MUTATION_TEMPLATE_REGISTRY_V1
            from src.alpha_foundry.retrieval.feature_producer_v1 import (
                RetrieverFeatureSourceArtifactStoreV1,
            )
            from src.alpha_foundry.search import AlphaFoundrySearch
            from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank

            artifacts = ActivationArtifactStore(self.artifact_root)
            kind: Literal["generation_consumption_v3"] = "generation_consumption_v3"
            if str(references[0]["relative_path"]).replace("\\", "/") != (
                artifacts.relative_path(kind, str(payload["evidence_hash"]))
            ):
                raise ValueError("source-bound generation path is not canonical")
            evidence = ActivationGenerationConsumptionV3.from_dict(artifacts.get(kind, str(payload["evidence_hash"])))
            all_events = self.query_events()
            by_hash = {event.event_hash: event for event in all_events}
            retriever = by_hash.get(evidence.base.retriever_decision_event_hash)
            source_event = by_hash.get(evidence.feature_source_event_hash)
            control_event = by_hash.get(evidence.base.control_evidence_event_hash)
            if (
                retriever is None
                or retriever.event_type != "RetrieverDecisionV6Recorded"
                or source_event is None
                or source_event.event_type != "RetrieverFeatureSourceRecorded"
                or control_event is None
                or control_event.event_type != "OfficialSearchControlRecorded"
            ):
                raise ValueError("source-bound generation authority is missing")
            self._validate_external_retriever_v6_evidence(retriever.event_type, retriever.to_dict()["payload"])
            source_refs = [
                reference
                for reference in source_event.payload["artifact_refs"]
                if reference["media_type"] == RetrieverFeatureSourceArtifactStoreV1.media_type
            ]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"] == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(source_refs) != 1 or len(control_refs) != 1:
                raise ValueError("source-bound generation source artifact is missing")
            source = RetrieverFeatureSourceArtifactStoreV1(self.artifact_root).read(
                str(source_refs[0]["relative_path"]), evidence.feature_source_hash
            )
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                str(control_event.payload["evidence_hash"]),
            )
            plans = [
                event
                for event in all_events
                if event.event_type == "ActivationPlanRegistered"
                and event.payload["plan_hash"] == evidence.base.plan_hash
            ]
            if len(plans) != 1:
                raise ValueError("source-bound generation plan is missing")
            plan = artifacts.get("plan", evidence.base.plan_hash)
            design = plan.get("design")
            provenance = plan.get("provenance")
            if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
                raise ValueError("source-bound generation plan is invalid")
            action_hash_by_id = {
                candidate.action_id: event_hash
                for candidate, event_hash in zip(source.candidates, source.action_event_hashes, strict=True)
            }
            expected_selected_hashes = tuple(
                action_hash_by_id[action_id] for action_id in retriever.payload["selected_action_ids"]
            )
            if (
                retriever.run_id != evidence.base.execution_run_id
                or retriever.payload["decision_hash"] != evidence.base.retriever_decision_hash
                or retriever.payload["input_bundle_hash"] != evidence.retriever_input_bundle_hash
                or retriever.payload["feature_source_event_hash"] != evidence.feature_source_event_hash
                or retriever.payload["feature_source_hash"] != evidence.feature_source_hash
                or tuple(retriever.payload["selected_action_ids"]) != evidence.base.selected_action_ids
                or tuple(retriever.payload["selected_parent_factor_spec_ids"])
                != evidence.base.selected_parent_factor_spec_ids
                or expected_selected_hashes != evidence.base.selected_action_event_hashes
                or source.snapshot_event_hash != evidence.feature_snapshot_event_hash
                or source.snapshot_hash != evidence.feature_snapshot_hash
                or source.feature_policy_hash != evidence.feature_policy_hash
                or source.scorecard_event_hashes != evidence.feature_scorecard_event_hashes
                or provenance.get("treatment_policy_hash") != retriever.payload["policy_hash"]
                or provenance.get("train_snapshot_hash") != source.snapshot_hash
                or provenance.get("control_policy_hash") != control.policy.policy_hash
                or design.get("candidate_budget") != evidence.base.candidate_budget
                or design.get("compute_budget") != evidence.base.compute_budget
            ):
                raise ValueError("source-bound generation authority differs")
            source_by_action = {candidate.action_id: candidate for candidate in source.candidates}
            for bound, parent in zip(
                evidence.base.selected_actions,
                evidence.base.selected_parents,
                strict=True,
            ):
                action_event = by_hash.get(bound.action_event_hash)
                candidate = source_by_action.get(bound.action.action_id)
                if (
                    action_event is None
                    or action_event.event_type != "RetrieverActionTemplateFrozen"
                    or action_event.payload != bound.action.to_dict()
                    or candidate is None
                    or candidate.factor_spec_id != parent["factor_spec_id"]
                    or build_expression_identity(str(parent["formula"])).canonical_ast != candidate.canonical_ast
                ):
                    raise ValueError("source-bound generation action differs")
            expected_policy_hash = canonical_json_hash(
                {
                    "schema_version": "activation_exact_action_generator_policy.v3",
                    "generator_version": control.policy.generator_version,
                    "mutator_version": "exact_selected_action_mutator.v2",
                    "template_registry_hash": SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash,
                    "selection_order": ("retriever_v6_source_bound_selected_action_order.v1"),
                    "retriever_input_bundle_hash": evidence.retriever_input_bundle_hash,
                    "feature_source_event_hash": evidence.feature_source_event_hash,
                    "feature_source_hash": evidence.feature_source_hash,
                    "selected_action_ids": list(evidence.base.selected_action_ids),
                    "selected_action_event_hashes": list(evidence.base.selected_action_event_hashes),
                    "max_candidates": evidence.base.candidate_budget,
                    "trial_budget": evidence.base.compute_budget,
                }
            )
            if expected_policy_hash != evidence.base.generator_policy_hash:
                raise ValueError("source-bound generation policy differs")
            parent_catalog = {
                str(parent["factor_spec_id"]): AlphaSeed(
                    seed_id=str(parent["factor_spec_id"]),
                    formula=str(parent["formula"]),
                    source="content-addressed:" + str(parent["source_hash"]),
                )
                for parent in evidence.base.selected_parents
            }
            mutator = ExactSelectedActionMutatorV2(evidence.base.selected_actions)
            replay = AlphaFoundrySearch(
                seed_bank=SeedBank(mutator.action_seeds(parent_catalog)),
                mutator=mutator,
                max_candidates=evidence.base.candidate_budget,
                trial_budget=evidence.base.compute_budget,
            ).generate()
            replay_records = tuple(
                (
                    str(candidate.metadata["retriever_action_id"]),
                    str(candidate.metadata["retriever_action_event_hash"]),
                    candidate.candidate_id,
                    candidate.parent_seed_id,
                    str(candidate.metadata["mutation"]),
                    candidate.formula_hash,
                )
                for candidate in replay.candidates
            )
            evidence_records = tuple(
                (
                    str(record["action_id"]),
                    str(record["action_event_hash"]),
                    str(record["candidate_id"]),
                    str(record["parent_factor_spec_id"]),
                    str(record["template_id"]),
                    str(record["formula_hash"]),
                )
                for record in evidence.base.generated_candidates
            )
            if replay_records != evidence_records:
                raise ValueError("source-bound generation search does not replay")
            starts = {
                str(event.payload["trial_id"]): event for event in all_events if event.event_type == "TrialStarted"
            }
            for record in evidence.base.generated_candidates:
                terminal = by_hash.get(str(record["terminal_event_hash"]))
                start = None if terminal is None else starts.get(str(terminal.payload["trial_id"]))
                if (
                    terminal is None
                    or terminal.event_type != "TrialTerminated"
                    or terminal.run_id != evidence.base.execution_run_id
                    or start is None
                    or start.payload["candidate_id"] != record["candidate_id"]
                ):
                    raise ValueError("source-bound generation terminal differs")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation source-bound generation evidence is invalid") from exc
        raw = evidence.to_dict()
        expected = {
            key: raw[key]
            for key in payload
            if key not in {"generation_id", "generated_candidate_count", "artifact_refs"}
        }
        if any(payload[name] != value for name, value in expected.items()) or payload[
            "generated_candidate_count"
        ] != len(evidence.base.generated_candidates):
            raise EventValidationError("Activation source-bound generation event differs from artifact")

    def _validate_external_generation_consumption_v4(self, event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type != "ActivationGenerationConsumptionV4Recorded":
            return
        references = [
            reference
            for reference in payload["artifact_refs"]
            if reference["media_type"] == "application/vnd.vibe.activation-generation-consumption-v4+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation schedule-bound generation requires one artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.generation_consumption_v2 import (
                ExactSelectedActionMutatorV2,
            )
            from src.alpha_foundry.activation.generation_consumption_v4 import (
                ActivationGenerationConsumptionV4,
            )
            from src.alpha_foundry.mutators import SEED_MUTATION_TEMPLATE_REGISTRY_V1
            from src.alpha_foundry.search import AlphaFoundrySearch
            from src.alpha_foundry.seed_bank import AlphaSeed, SeedBank

            artifacts = ActivationArtifactStore(self.artifact_root)
            kind: Literal["generation_consumption_v4"] = "generation_consumption_v4"
            if str(references[0]["relative_path"]).replace("\\", "/") != (
                artifacts.relative_path(kind, str(payload["evidence_hash"]))
            ):
                raise ValueError("schedule-bound generation path is not canonical")
            evidence = ActivationGenerationConsumptionV4.from_dict(artifacts.get(kind, str(payload["evidence_hash"])))
            events = self.query_events()
            by_hash = {event.event_hash: event for event in events}
            retriever = by_hash.get(evidence.base.retriever_decision_event_hash)
            schedule = by_hash.get(evidence.schedule_event_hash)
            source = by_hash.get(evidence.feature_source_event_hash)
            if (
                retriever is None
                or retriever.event_type != "RetrieverDecisionV7Recorded"
                or schedule is None
                or schedule.event_type != "PreArmFlatScheduleFrozen"
                or source is None
                or source.event_type != "RetrieverFeatureSourceRecorded"
            ):
                raise ValueError("schedule-bound generation authority is missing")
            self._validate_external_retriever_v7_evidence(retriever.event_type, retriever.to_dict()["payload"])
            plan = artifacts.get("plan", evidence.base.plan_hash)
            design = plan.get("design")
            provenance = plan.get("provenance")
            if not isinstance(design, Mapping) or not isinstance(provenance, Mapping):
                raise ValueError("schedule-bound generation plan is invalid")
            if (
                retriever.run_id != evidence.base.execution_run_id
                or retriever.payload["decision_hash"] != evidence.base.retriever_decision_hash
                or retriever.payload["input_bundle_hash"] != evidence.retriever_input_bundle_hash
                or retriever.payload["schedule_event_hash"] != evidence.schedule_event_hash
                or retriever.payload["schedule_hash"] != evidence.schedule_hash
                or retriever.payload["feature_source_event_hash"] != evidence.feature_source_event_hash
                or retriever.payload["feature_source_hash"] != evidence.feature_source_hash
                or tuple(retriever.payload["selected_action_ids"]) != evidence.base.selected_action_ids
                or tuple(retriever.payload["selected_parent_factor_spec_ids"])
                != evidence.base.selected_parent_factor_spec_ids
                or schedule.payload["plan_hash"] != evidence.base.plan_hash
                or schedule.payload["pair_id"] != evidence.base.pair_id
                or schedule.payload["schedule_hash"] != evidence.schedule_hash
                or source.payload["source_hash"] != evidence.feature_source_hash
                or source.payload["snapshot_event_hash"] != evidence.feature_snapshot_event_hash
                or source.payload["snapshot_hash"] != evidence.feature_snapshot_hash
                or source.payload["feature_policy_hash"] != evidence.feature_policy_hash
                or tuple(source.payload["scorecard_event_hashes"]) != evidence.feature_scorecard_event_hashes
                or design.get("candidate_budget") != evidence.base.candidate_budget
                or design.get("compute_budget") != evidence.base.compute_budget
                or provenance.get("train_snapshot_hash") != evidence.feature_snapshot_hash
            ):
                raise ValueError("schedule-bound generation authority differs")
            expected_policy_hash = canonical_json_hash(
                {
                    "schema_version": "activation_exact_action_generator_policy.v4",
                    "generator_version": "alpha_foundry_search.v1",
                    "mutator_version": "exact_selected_action_mutator.v2",
                    "template_registry_hash": SEED_MUTATION_TEMPLATE_REGISTRY_V1.registry_hash,
                    "selection_order": ("retriever_v7_schedule_bound_selected_action_order.v1"),
                    "retriever_input_bundle_hash": evidence.retriever_input_bundle_hash,
                    "schedule_event_hash": evidence.schedule_event_hash,
                    "schedule_hash": evidence.schedule_hash,
                    "feature_source_event_hash": evidence.feature_source_event_hash,
                    "feature_source_hash": evidence.feature_source_hash,
                    "selected_action_ids": list(evidence.base.selected_action_ids),
                    "selected_action_event_hashes": list(evidence.base.selected_action_event_hashes),
                    "max_candidates": evidence.base.candidate_budget,
                    "trial_budget": evidence.base.compute_budget,
                }
            )
            if expected_policy_hash != evidence.base.generator_policy_hash:
                raise ValueError("schedule-bound generation policy differs")
            parent_catalog = {
                str(parent["factor_spec_id"]): AlphaSeed(
                    seed_id=str(parent["factor_spec_id"]),
                    formula=str(parent["formula"]),
                    source="content-addressed:" + str(parent["source_hash"]),
                )
                for parent in evidence.base.selected_parents
            }
            mutator = ExactSelectedActionMutatorV2(evidence.base.selected_actions)
            replay = AlphaFoundrySearch(
                seed_bank=SeedBank(mutator.action_seeds(parent_catalog)),
                mutator=mutator,
                max_candidates=evidence.base.candidate_budget,
                trial_budget=evidence.base.compute_budget,
            ).generate()
            replay_records = tuple(
                (
                    str(candidate.metadata["retriever_action_id"]),
                    str(candidate.metadata["retriever_action_event_hash"]),
                    candidate.candidate_id,
                    candidate.parent_seed_id,
                    str(candidate.metadata["mutation"]),
                    candidate.formula_hash,
                )
                for candidate in replay.candidates
            )
            evidence_records = tuple(
                (
                    str(record["action_id"]),
                    str(record["action_event_hash"]),
                    str(record["candidate_id"]),
                    str(record["parent_factor_spec_id"]),
                    str(record["template_id"]),
                    str(record["formula_hash"]),
                )
                for record in evidence.base.generated_candidates
            )
            if replay_records != evidence_records:
                raise ValueError("schedule-bound generation search does not replay")
            starts = {str(event.payload["trial_id"]): event for event in events if event.event_type == "TrialStarted"}
            for record in evidence.base.generated_candidates:
                terminal = by_hash.get(str(record["terminal_event_hash"]))
                start = None if terminal is None else starts.get(str(terminal.payload["trial_id"]))
                if (
                    terminal is None
                    or terminal.event_type != "TrialTerminated"
                    or terminal.run_id != evidence.base.execution_run_id
                    or start is None
                    or start.payload["candidate_id"] != record["candidate_id"]
                ):
                    raise ValueError("schedule-bound generation terminal differs")
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError("Activation schedule-bound generation evidence is invalid") from exc
        raw = evidence.to_dict()
        expected = {
            key: raw[key]
            for key in payload
            if key not in {"generation_id", "generated_candidate_count", "artifact_refs"}
        }
        if any(payload[name] != value for name, value in expected.items()) or payload[
            "generated_candidate_count"
        ] != len(evidence.base.generated_candidates):
            raise EventValidationError("Activation schedule-bound generation event differs from artifact")

    @staticmethod
    def _validate_factor_definition_identity(event_type: str, payload: Mapping[str, Any]) -> None:
        metadata = payload.get("metadata")
        if (
            event_type != "FactorDefinitionRecorded"
            or not isinstance(metadata, Mapping)
            or "identity_schema_version" not in metadata
        ):
            return
        from src.alpha_foundry.dsl.identity import validate_factor_definition_payload

        try:
            validate_factor_definition_payload(payload)
        except (TypeError, ValueError) as exc:
            raise EventValidationError("factor definition identity is not reproducible") from exc

    @staticmethod
    def _validate_registry_bootstrap_identity(event_type: str, payload: Mapping[str, Any]) -> None:
        if event_type != "RegistryBootstrapRecordedV2":
            return
        from src.alpha_foundry.dag.bootstrap import validate_registry_bootstrap_payload

        try:
            validate_registry_bootstrap_payload(payload)
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError("registry bootstrap identity is not reproducible") from exc

    def _validate_transition(
        self,
        conn: sqlite3.Connection,
        draft: EventDraft,
        payload: Mapping[str, Any],
    ) -> None:
        event_type = draft.event_type
        if (
            event_type == "FactorDefinitionRecorded"
            and payload.get("metadata", {}).get("identity_schema_version") == "factor_spec.v1"
        ):
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND entity_id = ?",
                (event_type, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("production factor definition already exists")
            return
        if event_type in {"RegistryBootstrapRecorded", "RegistryBootstrapRecordedV2"}:
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type IN ('RegistryBootstrapRecorded', 'RegistryBootstrapRecordedV2')
                  AND entity_id = ?
                """,
                (payload["snapshot_id"],),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("registry snapshot is already recorded")
            return
        if event_type == "DerivationRecorded":
            terminal = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'TrialTerminated' AND event_hash = ?
                """,
                (payload["trial_terminal_event_hash"],),
            ).fetchone()
            if terminal is None:
                raise EventTransitionError("derivation references no prior terminal trial event")
            child = str(payload["child_factor_spec_id"])
            parents = [str(parent) for parent in payload["parent_factor_spec_ids"]]
            if child in parents:
                raise EventTransitionError("derivation cannot contain a self-edge")
            if len(parents) != len(set(parents)):
                raise EventTransitionError("derivation cannot contain duplicate parents")
            definition_ids = {child, *parents}
            known_definitions = {
                factor_id
                for factor_id in definition_ids
                if conn.execute(
                    """
                    SELECT 1 FROM research_events
                    WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
                    """,
                    (factor_id,),
                ).fetchone()
                is not None
            }
            if known_definitions != definition_ids:
                raise EventTransitionError("derivation references no prior factor definition")
            definition_row = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
                ORDER BY seq ASC LIMIT 1
                """,
                (child,),
            ).fetchone()
            assert definition_row is not None
            definition = json.loads(str(definition_row["payload"]))
            terminal_payload = json.loads(str(terminal["payload"]))
            originating_trial_id = str(definition.get("metadata", {}).get("originating_trial_id", ""))
            if not originating_trial_id or terminal_payload["trial_id"] != originating_trial_id:
                raise EventTransitionError("derivation terminal is not bound to the child trial")
            if terminal_payload["status"] not in {"success", "reject"}:
                raise EventTransitionError("derivation terminal is not an evaluated outcome")
            evaluation_hash = terminal_payload["evaluation_event_hash"]
            evaluation_row = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'EvaluationRecorded' AND event_hash = ?
                """,
                (evaluation_hash,),
            ).fetchone()
            if evaluation_row is None:
                raise EventTransitionError("derivation lacks matching train/valid evaluation evidence")
            evaluation = json.loads(str(evaluation_row["payload"]))
            if (
                evaluation["trial_id"] != originating_trial_id
                or evaluation["factor_spec_id"] != child
                or evaluation["data_scope"] not in {"valid", "train_valid"}
            ):
                raise EventTransitionError("derivation lacks matching train/valid evaluation evidence")
            prior_rows = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'DerivationRecorded' ORDER BY seq ASC"
            ).fetchall()
            graph: dict[str, set[str]] = {}
            for row in prior_rows:
                prior = json.loads(str(row["payload"]))
                prior_child = str(prior["child_factor_spec_id"])
                if prior_child == child:
                    raise EventTransitionError("multiple lineage derivations for one child are ambiguous")
                for parent in prior["parent_factor_spec_ids"]:
                    graph.setdefault(str(parent), set()).add(prior_child)
            if any(self._graph_has_path(graph, child, parent) for parent in parents):
                raise EventTransitionError("derivation creates a lineage cycle")
            return
        if event_type == "ProcessActionFrozen":
            started = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
                (payload["trial_id"],),
            ).fetchone()
            if started is None:
                raise EventTransitionError("process action references no prior trial start")
            return
        if event_type == "ProcessActionFrozenV2":
            self._validate_process_action_v2_transition(conn, draft, payload)
            return
        if event_type == "ProcessOutcomeRecordedV2":
            self._validate_process_outcome_v2_transition(conn, draft, payload)
            return
        if event_type == "RetrieverActionTemplateFrozen":
            self._validate_retriever_action_template_transition(conn, draft, payload)
            return
        if event_type == "EvaluationPolicyRegistered":
            from src.alpha_quality.evaluation_registry_v1 import (
                EvaluationPolicyRegistryServiceV1,
            )

            expected_id = EvaluationPolicyRegistryServiceV1.registration_id(
                draft.run_id,
                str(payload["bundle_hash"]),
            )
            if draft.entity_id != expected_id:
                raise EventTransitionError("evaluation policy registration identity differs from run")
            if self._tail_hash(conn) != payload["preregistration_watermark"]:
                raise EventTransitionError("evaluation policy preregistration watermark is stale")
            prior_same_run = conn.execute(
                "SELECT event_type FROM research_events WHERE run_id = ? LIMIT 1",
                (draft.run_id,),
            ).fetchone()
            if prior_same_run is not None:
                raise EventTransitionError("evaluation policy must be the first event in its run")
            return
        if event_type == "ResolvedEvaluationContractRegistered":
            policy = conn.execute(
                """
                SELECT seq, run_id, payload FROM research_events
                WHERE event_type = 'EvaluationPolicyRegistered' AND event_hash = ?
                """,
                (payload["evaluation_policy_event_hash"],),
            ).fetchone()
            if policy is None or str(policy["run_id"]) != draft.run_id:
                raise EventTransitionError("resolved contract lacks exact run policy")
            policy_payload = json.loads(str(policy["payload"]))
            if policy_payload["bundle_hash"] != payload["evaluation_policy_bundle_hash"]:
                raise EventTransitionError("resolved contract policy bundle differs")
            prior_run = conn.execute(
                "SELECT event_type, event_hash FROM research_events WHERE run_id = ? ORDER BY seq",
                (draft.run_id,),
            ).fetchall()
            if len(prior_run) != 1 or (
                str(prior_run[0]["event_type"]) != "EvaluationPolicyRegistered"
                or str(prior_run[0]["event_hash"]) != payload["evaluation_policy_event_hash"]
            ):
                raise EventTransitionError("resolved contract must be the second run event before trials")
            duplicate = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ResolvedEvaluationContractRegistered'
                  AND (run_id = ? OR json_extract(payload, '$.contract_hash') = ?)
                """,
                (draft.run_id, payload["contract_hash"]),
            ).fetchone()
            if duplicate is not None:
                raise EventTransitionError("resolved contract is already registered")
            return
        if event_type == "ApplicabilityAssessmentRecorded":
            contract = conn.execute(
                """
                SELECT event_hash, run_id, payload FROM research_events
                WHERE event_type = 'ResolvedEvaluationContractRegistered'
                  AND json_extract(payload, '$.contract_hash') = ?
                """,
                (payload["resolved_contract_hash"],),
            ).fetchone()
            definition = conn.execute(
                """
                SELECT event_hash, run_id, payload FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND event_hash = ?
                """,
                (payload["factor_definition_event_hash"],),
            ).fetchone()
            if contract is None or definition is None:
                raise EventTransitionError("applicability lacks contract or factor source")
            if str(contract["run_id"]) != draft.run_id or str(definition["run_id"]) != draft.run_id:
                raise EventTransitionError("applicability cannot mix runs")
            if str(contract["event_hash"]) not in payload["source_event_hashes"]:
                raise EventTransitionError("applicability sources omit contract event")
            definition_payload = json.loads(str(definition["payload"]))
            if definition_payload["factor_spec_id"] != payload["factor_spec_id"]:
                raise EventTransitionError("applicability factor identity differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ApplicabilityAssessmentRecorded'
                  AND run_id = ?
                  AND json_extract(payload, '$.resolved_contract_hash') = ?
                  AND json_extract(payload, '$.factor_spec_id') = ?
                  AND json_extract(payload, '$.claim_type') = ?
                """,
                (
                    draft.run_id,
                    payload["resolved_contract_hash"],
                    payload["factor_spec_id"],
                    payload["claim_type"],
                ),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("applicability claim is already assessed")
            return
        if event_type == "AsharePITAdapterRegistered":
            if self._tail_hash(conn) != payload["source_watermark_event_hash"]:
                raise EventTransitionError("PIT adapter registration watermark is stale")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'AsharePITAdapterRegistered'
                  AND (
                    json_extract(payload, '$.adapter_id') = ?
                    OR json_extract(payload, '$.registration_hash') = ?
                  )
                """,
                (payload["adapter_id"], payload["registration_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("PIT adapter is already registered")
            return
        if event_type == "AsharePITSnapshotRecorded":
            if self._tail_hash(conn) != payload["source_watermark_event_hash"]:
                raise EventTransitionError("PIT snapshot watermark is stale")
            registration = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'AsharePITAdapterRegistered' AND event_hash = ?
                """,
                (payload["adapter_registration_event_hash"],),
            ).fetchone()
            policy = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'EvaluationPolicyRegistered' AND event_hash = ?
                """,
                (payload["evaluation_policy_event_hash"],),
            ).fetchone()
            started = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrialStarted' AND run_id = ?
                """,
                (draft.run_id,),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'AsharePITSnapshotRecorded' AND run_id = ?
                """,
                (draft.run_id,),
            ).fetchone()
            if registration is None or policy is None:
                raise EventTransitionError("PIT snapshot source event is missing")
            registration_payload = json.loads(str(registration["payload"]))
            policy_payload = json.loads(str(policy["payload"]))
            if (
                started is not None
                or prior is not None
                or str(policy["run_id"]) != draft.run_id
                or registration_payload["adapter_id"] != payload["adapter_id"]
                or registration_payload["registration_hash"] != payload["registration_hash"]
                or policy_payload["calendar_hash"] != payload["calendar_hash"]
                or policy_payload["evaluation_time_policy_hash"] != payload["evaluation_time_policy_hash"]
                or policy_payload["split_plan_hash"] != payload["split_plan_hash"]
            ):
                raise EventTransitionError("PIT snapshot source identity, order or scope differs")
            return
        if event_type == "FactorOutputRecordedV3":
            contract = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'ResolvedEvaluationContractRegistered'
                  AND event_hash = ?
                """,
                (payload["contract_event_hash"],),
            ).fetchone()
            snapshot = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'AsharePITSnapshotRecorded' AND event_hash = ?
                """,
                (payload["pit_snapshot_event_hash"],),
            ).fetchone()
            definitions = conn.execute(
                """
                SELECT run_id, event_hash FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
                """,
                (payload["factor_spec_id"],),
            ).fetchall()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'FactorOutputRecordedV3'
                  AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?
                """,
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if (
                contract is None
                or snapshot is None
                or len(definitions) != 1
                or prior is not None
                or str(contract["run_id"]) != draft.run_id
                or str(snapshot["run_id"]) != draft.run_id
                or str(definitions[0]["run_id"]) != draft.run_id
            ):
                raise EventTransitionError("factor output source identity or order differs")
            contract_payload = json.loads(str(contract["payload"]))
            snapshot_payload = json.loads(str(snapshot["payload"]))
            if (
                contract_payload["contract_hash"] != payload["resolved_contract_hash"]
                or snapshot_payload["snapshot_hash"] != payload["pit_snapshot_hash"]
                or contract_payload["evaluation_policy_event_hash"] != payload["evaluation_policy_event_hash"]
                or str(definitions[0]["event_hash"]) not in payload["source_event_hashes"]
            ):
                raise EventTransitionError("factor output source hashes differ")
            return
        if event_type in {
            "ObservedPanelPredictiveEvidenceRecorded",
            "PITPredictiveEvidenceRecorded",
        }:
            factor = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'FactorOutputRecordedV3' AND event_hash = ?
                """,
                (payload["factor_output_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = ? AND run_id = ?
                  AND json_extract(payload, '$.factor_spec_id') = ?
                """,
                (event_type, draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if factor is None or prior is not None or str(factor["run_id"]) != draft.run_id:
                raise EventTransitionError("predictive evidence source identity differs")
            factor_payload = json.loads(str(factor["payload"]))
            for name in (
                "factor_output_hash",
                "factor_spec_id",
                "resolved_contract_hash",
                "contract_event_hash",
                "pit_snapshot_hash",
                "pit_snapshot_event_hash",
            ):
                if factor_payload[name] != payload[name]:
                    raise EventTransitionError("predictive evidence factor binding differs")
            return
        if event_type == "ScorecardDecisionEvidenceV4Recorded":
            source_types = {
                "factor_output_event_hash": "FactorOutputRecordedV3",
                "observed_event_hash": "ObservedPanelPredictiveEvidenceRecorded",
                "pit_event_hash": "PITPredictiveEvidenceRecorded",
            }
            source_rows: dict[str, Mapping[str, Any]] = {}
            for name, expected_type in source_types.items():
                row = conn.execute(
                    "SELECT run_id, payload FROM research_events WHERE event_type = ? AND event_hash = ?",
                    (expected_type, payload[name]),
                ).fetchone()
                if row is None or str(row["run_id"]) != draft.run_id:
                    raise EventTransitionError("scorecard v4 source event is missing")
                source_rows[name] = json.loads(str(row["payload"]))
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ScorecardDecisionEvidenceV4Recorded'
                  AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?
                """,
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("scorecard v4 already exists for factor/run")
            factor_payload = source_rows["factor_output_event_hash"]
            observed_payload = source_rows["observed_event_hash"]
            pit_payload = source_rows["pit_event_hash"]
            if (
                factor_payload["factor_output_hash"] != payload["factor_output_hash"]
                or observed_payload["evidence_hash"] != payload["observed_evidence_hash"]
                or pit_payload["evidence_hash"] != payload["pit_evidence_hash"]
                or any(
                    source["factor_spec_id"] != payload["factor_spec_id"]
                    for source in (factor_payload, observed_payload, pit_payload)
                )
            ):
                raise EventTransitionError("scorecard v4 source binding differs")
            return
        if event_type == "ExecutionEvidenceRecorded":
            factor = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'FactorOutputRecordedV3' AND event_hash = ?
                """,
                (payload["factor_output_event_hash"],),
            ).fetchone()
            observed = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'ObservedPanelPredictiveEvidenceRecorded'
                  AND event_hash = ?
                """,
                (payload["observed_predictive_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ExecutionEvidenceRecorded'
                  AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?
                """,
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            execution_source_rows = [
                conn.execute(
                    "SELECT 1 FROM research_events WHERE event_hash = ?",
                    (event_hash,),
                ).fetchone()
                for event_hash in payload["source_event_hashes"]
            ]
            if (
                factor is None
                or observed is None
                or prior is not None
                or str(factor["run_id"]) != draft.run_id
                or str(observed["run_id"]) != draft.run_id
                or any(row is None for row in execution_source_rows)
            ):
                raise EventTransitionError("execution evidence source identity differs")
            factor_payload = json.loads(str(factor["payload"]))
            observed_payload = json.loads(str(observed["payload"]))
            if (
                factor_payload["factor_spec_id"] != payload["factor_spec_id"]
                or factor_payload["resolved_contract_hash"] != payload["resolved_contract_hash"]
                or observed_payload["factor_output_event_hash"] != payload["factor_output_event_hash"]
            ):
                raise EventTransitionError("execution evidence source binding differs")
            return
        if event_type == "ComparisonPoolFrozen":
            watermark = conn.execute(
                "SELECT seq FROM research_events WHERE event_hash = ?",
                (payload["source_watermark_event_hash"],),
            ).fetchone()
            if watermark is None:
                raise EventTransitionError("comparison pool watermark is missing")
            position = conn.execute(
                "SELECT COUNT(*) AS count FROM research_events WHERE seq <= ?",
                (watermark["seq"],),
            ).fetchone()
            if int(position["count"]) - 1 != int(payload["evidence_watermark"]):
                raise EventTransitionError("comparison pool watermark position differs")
            for member in payload["members"]:
                definition = conn.execute(
                    "SELECT seq, payload FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND event_hash = ?",
                    (member["factor_definition_event_hash"],),
                ).fetchone()
                if (
                    definition is None
                    or definition["seq"] > watermark["seq"]
                    or json.loads(str(definition["payload"]))["factor_spec_id"] != member["factor_spec_id"]
                ):
                    raise EventTransitionError("comparison pool definition differs")
                for field, expected_type in (
                    ("factor_output_event_hash", "FactorOutputRecordedV3"),
                    ("execution_event_hash", "ExecutionEvidenceRecorded"),
                    ("decision_event_hash", "QualityDecisionV4Recorded"),
                ):
                    source_hash = member[field]
                    if source_hash is None:
                        continue
                    source = conn.execute(
                        "SELECT seq, payload FROM research_events WHERE event_type = ? AND event_hash = ?",
                        (expected_type, source_hash),
                    ).fetchone()
                    if (
                        source is None
                        or source["seq"] > watermark["seq"]
                        or json.loads(str(source["payload"])).get("factor_spec_id") != member["factor_spec_id"]
                    ):
                        raise EventTransitionError("comparison pool evidence differs")
            return
        if event_type == "SecondaryEvidenceRecorded":
            factor = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (payload["factor_spec_id"],),
            ).fetchone()
            pool = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'ComparisonPoolFrozen' AND json_extract(payload, '$.comparison_pool_hash') = ?",
                (payload["comparison_pool_hash"],),
            ).fetchall()
            applicability = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'ApplicabilityAssessmentRecorded' AND event_hash = ?",
                (payload["applicability_event_hash"],),
            ).fetchone()
            if (
                factor is None
                or len(pool) != 1
                or applicability is None
                or str(factor["run_id"]) != draft.run_id
                or str(applicability["run_id"]) != draft.run_id
                or json.loads(str(applicability["payload"]))["factor_spec_id"] != payload["factor_spec_id"]
            ):
                raise EventTransitionError("secondary source binding differs")
            for field, expected_type in (
                ("factor_output_event_hash", "FactorOutputRecordedV3"),
                ("execution_event_hash", "ExecutionEvidenceRecorded"),
            ):
                source_hash = payload[field]
                if source_hash is None:
                    continue
                source = conn.execute(
                    "SELECT run_id, payload FROM research_events WHERE event_type = ? AND event_hash = ?",
                    (expected_type, source_hash),
                ).fetchone()
                if (
                    source is None
                    or str(source["run_id"]) != draft.run_id
                    or json.loads(str(source["payload"]))["factor_spec_id"] != payload["factor_spec_id"]
                ):
                    raise EventTransitionError("secondary candidate evidence differs")
            source_count = conn.execute(
                "SELECT COUNT(*) AS count FROM research_events WHERE event_hash IN (%s)"
                % ",".join("?" for _ in payload["source_event_hashes"]),
                tuple(payload["source_event_hashes"]),
            ).fetchone()
            if int(source_count["count"]) != len(payload["source_event_hashes"]):
                raise EventTransitionError("secondary source event is missing")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'SecondaryEvidenceRecorded' AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?",
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("secondary evidence already exists")
            return
        if event_type == "SelectionAssessmentRecorded":
            contracts = conn.execute(
                "SELECT event_hash, payload FROM research_events WHERE event_type = 'ResolvedEvaluationContractRegistered' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            starts = conn.execute(
                "SELECT event_hash, payload FROM research_events WHERE event_type = 'TrialStarted' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            terminals = conn.execute(
                "SELECT event_hash, payload FROM research_events WHERE event_type = 'TrialTerminated' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            final_events = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type IN ('FinalTestAccessRecorded', 'FinalTestArtifactRecorded') AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            if len(contracts) != 1:
                raise EventTransitionError("selection requires one exact run contract")
            contract_payload = json.loads(str(contracts[0]["payload"]))
            if contract_payload["research_family_id"] != payload["research_family_id"]:
                raise EventTransitionError("selection research family differs")
            expected_sources = sorted(
                {
                    str(contracts[0]["event_hash"]),
                    *(str(row["event_hash"]) for row in starts),
                    *(str(row["event_hash"]) for row in terminals),
                    *(str(row["event_hash"]) for row in final_events),
                }
            )
            if payload["source_event_hashes"] != expected_sources:
                raise EventTransitionError("selection source population is incomplete")
            if payload["trial_count"] != len(starts) or payload["final_access_count"] != len(final_events):
                raise EventTransitionError("selection population counts differ")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'SelectionAssessmentRecorded' AND run_id = ?",
                (draft.run_id,),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("selection assessment already exists")
            return
        if event_type == "ClaimMatrixRecorded":
            selection = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'SelectionAssessmentRecorded' AND event_hash = ?",
                (payload["selection_event_hash"],),
            ).fetchone()
            definition = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (payload["factor_spec_id"],),
            ).fetchone()
            sources = [
                conn.execute(
                    "SELECT event_type FROM research_events WHERE event_hash = ?",
                    (event_hash,),
                ).fetchone()
                for event_hash in payload["source_event_hashes"]
            ]
            allowed = {
                "SelectionAssessmentRecorded",
                "ObservedPanelPredictiveEvidenceRecorded",
                "PITPredictiveEvidenceRecorded",
                "ExecutionEvidenceRecorded",
                "SecondaryEvidenceRecorded",
            }
            if (
                selection is None
                or definition is None
                or str(selection["run_id"]) != draft.run_id
                or str(definition["run_id"]) != draft.run_id
                or json.loads(str(selection["payload"]))["assessment_hash"] != payload["selection_assessment_hash"]
                or any(row is None or row["event_type"] not in allowed for row in sources)
            ):
                raise EventTransitionError("claim matrix protected sources differ")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'ClaimMatrixRecorded' AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?",
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("claim matrix already exists")
            return
        if event_type == "QualityDecisionV4Recorded":
            matrix = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'ClaimMatrixRecorded' AND event_hash = ?",
                (payload["claim_matrix_event_hash"],),
            ).fetchone()
            selection = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'SelectionAssessmentRecorded' AND event_hash = ?",
                (payload["selection_event_hash"],),
            ).fetchone()
            if matrix is None or selection is None:
                raise EventTransitionError("narrow decision source event is missing")
            matrix_payload = json.loads(str(matrix["payload"]))
            selection_payload = json.loads(str(selection["payload"]))
            if (
                str(matrix["run_id"]) != draft.run_id
                or str(selection["run_id"]) != draft.run_id
                or matrix_payload["factor_spec_id"] != payload["factor_spec_id"]
                or matrix_payload["claim_matrix_hash"] != payload["claim_matrix_hash"]
                or selection_payload["assessment_hash"] != payload["selection_assessment_hash"]
                or payload["evidence_event_hashes"]
                != sorted({payload["claim_matrix_event_hash"], payload["selection_event_hash"]})
            ):
                raise EventTransitionError("narrow decision source binding differs")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'QualityDecisionV4Recorded' AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?",
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("narrow decision already exists")
            return
        if event_type == "ResearchDossierRecorded":
            terminal = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'TrialTerminalDossierRecorded' AND event_hash = ?",
                (payload["terminal_dossier_event_hash"],),
            ).fetchone()
            definition = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (payload["factor_spec_id"],),
            ).fetchone()
            sources = [
                conn.execute("SELECT 1 FROM research_events WHERE event_hash = ?", (event_hash,)).fetchone()
                for event_hash in payload["source_event_hashes"]
            ]
            decision = None
            if payload["quality_decision_event_hash"] is not None:
                decision = conn.execute(
                    "SELECT run_id, payload FROM research_events WHERE event_type = 'QualityDecisionV4Recorded' AND event_hash = ?",
                    (payload["quality_decision_event_hash"],),
                ).fetchone()
            if (
                terminal is None
                or definition is None
                or str(terminal["run_id"]) != draft.run_id
                or str(definition["run_id"]) != draft.run_id
                or any(row is None for row in sources)
                or (payload["quality_decision_event_hash"] is not None and decision is None)
                or (
                    decision is not None
                    and (
                        str(decision["run_id"]) != draft.run_id
                        or json.loads(str(decision["payload"]))["factor_spec_id"] != payload["factor_spec_id"]
                    )
                )
            ):
                raise EventTransitionError("research dossier source binding differs")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'ResearchDossierRecorded' AND run_id = ? AND json_extract(payload, '$.factor_spec_id') = ?",
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("research dossier already exists")
            return
        if event_type == "ExperimentRunReportRecorded":
            starts = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'TrialStarted' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            terminals = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'TrialTerminated' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            dossiers = conn.execute(
                "SELECT event_hash, payload FROM research_events WHERE event_type = 'ResearchDossierRecorded' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            decisions = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'QualityDecisionV4Recorded' AND run_id = ?",
                (draft.run_id,),
            ).fetchall()
            expected_sources = sorted(
                {
                    *(str(row["event_hash"]) for row in starts),
                    *(str(row["event_hash"]) for row in terminals),
                    *(str(row["event_hash"]) for row in dossiers),
                    *(str(row["event_hash"]) for row in decisions),
                }
            )
            if (
                not starts
                or len(terminals) != len(starts)
                or payload["trial_count"] != len(starts)
                or payload["source_event_hashes"] != expected_sources
                or payload["candidate_dossier_hashes"]
                != sorted(json.loads(str(row["payload"]))["dossier_hash"] for row in dossiers)
            ):
                raise EventTransitionError("run report population differs")
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'ExperimentRunReportRecorded' AND run_id = ?",
                (draft.run_id,),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("run report already exists")
            return
        if event_type == "ResearchReleaseManifestRecorded":
            sources = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type IN ('ResearchDossierRecorded', 'ExperimentRunReportRecorded') ORDER BY seq",
            ).fetchall()
            expected = sorted(str(row["event_hash"]) for row in sources)
            if not sources or payload["source_event_hashes"] != expected:
                raise EventTransitionError("release manifest source prefix differs")
            return
        if event_type == "ResearchDossierMaterializationFailed":
            terminal = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'TrialTerminalDossierRecorded' AND event_hash = ?",
                (payload["terminal_dossier_event_hash"],),
            ).fetchone()
            decision = None
            if payload["quality_decision_event_hash"] is not None:
                decision = conn.execute(
                    "SELECT run_id, payload FROM research_events WHERE event_type = 'QualityDecisionV4Recorded' AND event_hash = ?",
                    (payload["quality_decision_event_hash"],),
                ).fetchone()
            if (
                terminal is None
                or str(terminal["run_id"]) != draft.run_id
                or json.loads(str(terminal["payload"]))["trial_id"] != payload["trial_id"]
                or (payload["quality_decision_event_hash"] is not None and decision is None)
            ):
                raise EventTransitionError("research dossier failure source differs")
            return
        if event_type == "ProductionEvaluationNodeRecorded":
            trial = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
                (payload["trial_id"],),
            ).fetchone()
            terminal = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'TrialTerminated' AND entity_id = ?",
                (payload["trial_id"],),
            ).fetchone()
            factor = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (payload["factor_spec_id"],),
            ).fetchone()
            contract = conn.execute(
                """
                SELECT run_id FROM research_events
                WHERE event_type = 'ResolvedEvaluationContractRegistered'
                  AND json_extract(payload, '$.contract_hash') = ?
                """,
                (payload["resolved_contract_hash"],),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ProductionEvaluationNodeRecorded'
                  AND json_extract(payload, '$.trial_id') = ?
                  AND json_extract(payload, '$.node_name') = ?
                """,
                (payload["trial_id"], payload["node_name"]),
            ).fetchone()
            node_source_rows = [
                conn.execute(
                    "SELECT 1 FROM research_events WHERE event_hash = ?",
                    (event_hash,),
                ).fetchone()
                for event_hash in payload["source_event_hashes"]
            ]
            if (
                trial is None
                or terminal is not None
                or factor is None
                or contract is None
                or prior is not None
                or payload["run_id"] != draft.run_id
                or str(trial["run_id"]) != draft.run_id
                or str(factor["run_id"]) != draft.run_id
                or str(contract["run_id"]) != draft.run_id
                or any(row is None for row in node_source_rows)
            ):
                raise EventTransitionError("production node source identity or order differs")
            return
        if event_type == "TrialTerminalDossierRecorded":
            terminal = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'TrialTerminated' AND event_hash = ?
                """,
                (payload["terminal_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrialTerminalDossierRecorded'
                  AND json_extract(payload, '$.trial_id') = ?
                """,
                (payload["trial_id"],),
            ).fetchone()
            dossier_source_rows = [
                conn.execute(
                    "SELECT 1 FROM research_events WHERE event_hash = ?",
                    (event_hash,),
                ).fetchone()
                for event_hash in payload["source_event_hashes"]
            ]
            if (
                terminal is None
                or prior is not None
                or str(terminal["run_id"]) != draft.run_id
                or json.loads(str(terminal["payload"]))["trial_id"] != payload["trial_id"]
                or any(row is None for row in dossier_source_rows)
            ):
                raise EventTransitionError("terminal dossier source identity differs")
            return
        if event_type == "ReportMaterializationFailed":
            terminal = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'TrialTerminated' AND event_hash = ?
                """,
                (payload["terminal_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ReportMaterializationFailed'
                  AND json_extract(payload, '$.trial_id') = ?
                """,
                (payload["trial_id"],),
            ).fetchone()
            if (
                terminal is None
                or prior is not None
                or str(terminal["run_id"]) != draft.run_id
                or json.loads(str(terminal["payload"]))["trial_id"] != payload["trial_id"]
            ):
                raise EventTransitionError("report materialization failure source differs")
            return
        if event_type == "TrainValidDataSnapshotFrozen":
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrainValidDataSnapshotFrozen'
                  AND (entity_id = ? OR json_extract(payload, '$.snapshot_hash') = ?)
                """,
                (payload["snapshot_id"], payload["snapshot_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("train/valid snapshot identity already exists")
            return
        if event_type == "RetrieverFeatureSourceRecorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError("Retriever feature event run differs from execution run")
            snapshot = conn.execute(
                """
                SELECT seq, payload FROM research_events
                WHERE event_type = 'TrainValidDataSnapshotFrozen' AND event_hash = ?
                """,
                (payload["snapshot_event_hash"],),
            ).fetchone()
            watermark = conn.execute(
                "SELECT seq FROM research_events WHERE event_hash = ?",
                (payload["eligible_event_watermark"],),
            ).fetchone()
            if snapshot is None or watermark is None:
                raise EventTransitionError("Retriever feature source or watermark is missing")
            snapshot_payload = json.loads(str(snapshot["payload"]))
            if (
                int(snapshot["seq"]) >= int(watermark["seq"])
                or snapshot_payload["snapshot_hash"] != payload["snapshot_hash"]
            ):
                raise EventTransitionError("Retriever feature snapshot ordering or identity differs")
            for action_hash in payload["action_event_hashes"]:
                action = conn.execute(
                    """
                    SELECT seq, run_id, payload FROM research_events
                    WHERE event_type = 'RetrieverActionTemplateFrozen'
                      AND event_hash = ?
                    """,
                    (action_hash,),
                ).fetchone()
                if action is None:
                    raise EventTransitionError("Retriever feature source lacks an action event")
                action_payload = json.loads(str(action["payload"]))
                if (
                    str(action["run_id"]) != draft.run_id
                    or int(action["seq"]) <= int(watermark["seq"])
                    or action_payload["data_snapshot_hash"] != payload["snapshot_hash"]
                    or action_payload["retrieval_policy_hash"] != payload["retrieval_policy_hash"]
                ):
                    raise EventTransitionError("Retriever feature action binding differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'RetrieverFeatureSourceRecorded'
                  AND (entity_id = ? OR json_extract(payload, '$.source_hash') = ?)
                """,
                (payload["feature_source_id"], payload["source_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("Retriever feature source identity already exists")
            return
        if event_type == "RetrieverDecisionV2Recorded":
            self._validate_retriever_v2_transition(conn, payload)
            return
        if event_type == "RetrieverDecisionV3Recorded":
            self._validate_retriever_v2_transition(conn, payload)
            return
        if event_type == "RetrieverDecisionV4Recorded":
            self._validate_retriever_v2_transition(conn, payload)
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'RetrieverDecisionV4Recorded'
                  AND entity_id = ?
                """,
                (payload["decision_id"],),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("retriever v4 decision identity already exists")
            control = conn.execute(
                """
                SELECT seq, run_id, payload FROM research_events
                WHERE event_type = 'OfficialSearchControlRecorded'
                AND event_hash = ?
                """,
                (payload["control_evidence_event_hash"],),
            ).fetchone()
            if control is None:
                raise EventTransitionError("retriever v4 lacks prior official control")
            control_payload = json.loads(str(control["payload"]))
            watermark = conn.execute(
                "SELECT seq FROM research_events WHERE event_hash = ?",
                (payload["eligible_event_watermark"],),
            ).fetchone()
            if watermark is None or int(watermark["seq"]) >= int(control["seq"]):
                raise EventTransitionError("retriever v4 discovery watermark must precede its control evidence")
            control_terminal_rows = conn.execute(
                """
                SELECT seq, event_hash FROM research_events
                WHERE event_type = 'TrialTerminated'
                """
            ).fetchall()
            terminal_seq_by_hash = {str(row["event_hash"]): int(row["seq"]) for row in control_terminal_rows}
            cited_control_terminals = tuple(str(item) for item in control_payload["terminal_event_hashes"])
            if len(cited_control_terminals) != len(set(cited_control_terminals)) or any(
                terminal_seq_by_hash.get(event_hash, -1) <= int(watermark["seq"])
                for event_hash in cited_control_terminals
            ):
                raise EventTransitionError("retriever v4 discovery watermark includes control-arm outcomes")
            if (
                control_payload["evidence_hash"] != payload["control_evidence_hash"]
                or control_payload["policy_hash"] != payload["control_policy_hash"]
                or control_payload["output_hash"] != payload["official_output_hash"]
                or control_payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
            ):
                raise EventTransitionError("retriever v4 official control binding differs")
            return
        if event_type == "RetrieverDecisionV5Recorded":
            self._validate_retriever_v2_transition(conn, payload)
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'RetrieverDecisionV5Recorded' AND entity_id = ?
                """,
                (payload["decision_id"],),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("retriever v5 decision identity already exists")
            control = conn.execute(
                """
                SELECT seq, payload FROM research_events
                WHERE event_type = 'OfficialSearchControlRecorded' AND event_hash = ?
                """,
                (payload["control_evidence_event_hash"],),
            ).fetchone()
            watermark = conn.execute(
                "SELECT seq FROM research_events WHERE event_hash = ?",
                (payload["eligible_event_watermark"],),
            ).fetchone()
            if control is None or watermark is None or int(watermark["seq"]) >= int(control["seq"]):
                raise EventTransitionError("retriever v5 watermark must precede official control")
            control_payload = json.loads(str(control["payload"]))
            if (
                control_payload["evidence_hash"] != payload["control_evidence_hash"]
                or control_payload["policy_hash"] != payload["control_policy_hash"]
                or control_payload["output_hash"] != payload["official_output_hash"]
                or control_payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
            ):
                raise EventTransitionError("retriever v5 official control binding differs")
            action_hashes = tuple(payload["action_template_event_hashes"])
            components = tuple(payload["components"])
            if len(action_hashes) != len(components):
                raise EventTransitionError("retriever v5 actions and components differ")
            for action_event_hash, component in zip(action_hashes, components, strict=True):
                row = conn.execute(
                    """
                    SELECT seq, run_id, payload FROM research_events
                    WHERE event_type = 'RetrieverActionTemplateFrozen'
                      AND event_hash = ?
                    """,
                    (action_event_hash,),
                ).fetchone()
                if row is None:
                    raise EventTransitionError("retriever v5 lacks a frozen action event")
                action_payload = json.loads(str(row["payload"]))
                if (
                    str(row["run_id"]) != draft.run_id
                    or not int(watermark["seq"]) < int(row["seq"]) < int(control["seq"])
                    or action_payload["execution_run_id"] != draft.run_id
                    or action_payload["eligible_event_watermark"] != payload["eligible_event_watermark"]
                    or action_payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
                    or action_payload["retrieval_policy_hash"] != payload["policy_hash"]
                    or action_payload["action_id"] != component["action_id"]
                    or action_payload["parent_factor_spec_id"] != component["factor_spec_id"]
                    or action_payload["expected_motif"] != component["motif"]
                    or action_payload["identity_action"]
                ):
                    raise EventTransitionError("retriever v5 frozen action binding differs")
            return
        if event_type == "RetrieverDecisionV6Recorded":
            self._validate_retriever_v2_transition(conn, payload)
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'RetrieverDecisionV6Recorded' AND entity_id = ?
                """,
                (payload["decision_id"],),
            ).fetchone()
            source = conn.execute(
                """
                SELECT seq, run_id, payload FROM research_events
                WHERE event_type = 'RetrieverFeatureSourceRecorded' AND event_hash = ?
                """,
                (payload["feature_source_event_hash"],),
            ).fetchone()
            control = conn.execute(
                """
                SELECT seq, payload FROM research_events
                WHERE event_type = 'OfficialSearchControlRecorded' AND event_hash = ?
                """,
                (payload["control_evidence_event_hash"],),
            ).fetchone()
            watermark = conn.execute(
                "SELECT seq FROM research_events WHERE event_hash = ?",
                (payload["eligible_event_watermark"],),
            ).fetchone()
            if (
                prior is not None
                or source is None
                or control is None
                or watermark is None
                or not int(watermark["seq"]) < int(source["seq"]) < int(control["seq"])
            ):
                raise EventTransitionError("retriever v6 source/control ordering or identity is invalid")
            source_payload = json.loads(str(source["payload"]))
            control_payload = json.loads(str(control["payload"]))
            if (
                str(source["run_id"]) != draft.run_id
                or source_payload["execution_run_id"] != draft.run_id
                or source_payload["source_hash"] != payload["feature_source_hash"]
                or source_payload["eligible_event_watermark"] != payload["eligible_event_watermark"]
                or source_payload["snapshot_hash"] != payload["data_snapshot_hash"]
                or source_payload["retrieval_policy_hash"] != payload["policy_hash"]
                or source_payload["action_event_hashes"] != payload["action_template_event_hashes"]
                or control_payload["evidence_hash"] != payload["control_evidence_hash"]
                or control_payload["policy_hash"] != payload["control_policy_hash"]
                or control_payload["output_hash"] != payload["official_output_hash"]
                or control_payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
            ):
                raise EventTransitionError("retriever v6 authoritative binding differs")
            components = tuple(payload["components"])
            for action_event_hash, component in zip(payload["action_template_event_hashes"], components, strict=True):
                action = conn.execute(
                    """
                    SELECT seq, run_id, payload FROM research_events
                    WHERE event_type = 'RetrieverActionTemplateFrozen' AND event_hash = ?
                    """,
                    (action_event_hash,),
                ).fetchone()
                if action is None:
                    raise EventTransitionError("retriever v6 action source is missing")
                action_payload = json.loads(str(action["payload"]))
                if (
                    str(action["run_id"]) != draft.run_id
                    or not int(watermark["seq"]) < int(action["seq"]) < int(source["seq"])
                    or action_payload["action_id"] != component["action_id"]
                    or action_payload["parent_factor_spec_id"] != component["factor_spec_id"]
                    or action_payload["expected_motif"] != component["motif"]
                    or action_payload["eligible_event_watermark"] != payload["eligible_event_watermark"]
                    or action_payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
                    or action_payload["retrieval_policy_hash"] != payload["policy_hash"]
                    or action_payload["identity_action"]
                ):
                    raise EventTransitionError("retriever v6 frozen action binding differs")
            return
        if event_type == "RetrieverDecisionV7Recorded":
            from src.alpha_foundry.activation.runner import (
                activation_arm_execution_run_id,
            )

            self._validate_retriever_v2_transition(conn, payload)
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'RetrieverDecisionV7Recorded' AND entity_id = ?",
                (payload["decision_id"],),
            ).fetchone()
            schedule = conn.execute(
                "SELECT seq, payload FROM research_events WHERE event_type = 'PreArmFlatScheduleFrozen' AND event_hash = ?",
                (payload["schedule_event_hash"],),
            ).fetchone()
            source = conn.execute(
                "SELECT seq, run_id, payload FROM research_events WHERE event_type = 'RetrieverFeatureSourceRecorded' AND event_hash = ?",
                (payload["feature_source_event_hash"],),
            ).fetchone()
            if prior is not None or schedule is None or source is None:
                raise EventTransitionError("retriever v7 authority is missing or duplicate")
            schedule_payload = json.loads(str(schedule["payload"]))
            source_payload = json.loads(str(source["payload"]))
            if (
                int(schedule["seq"]) >= int(source["seq"])
                or str(source["run_id"]) != draft.run_id
                or schedule_payload["schedule_hash"] != payload["schedule_hash"]
                or schedule_payload["plan_hash"] != payload["plan_hash"]
                or schedule_payload["pair_id"] != payload["pair_id"]
                or schedule_payload["output_hash"] != payload["official_output_hash"]
                or schedule_payload["candidate_count"] != payload["candidate_budget"]
                or source_payload["source_hash"] != payload["feature_source_hash"]
                or source_payload["snapshot_hash"] != payload["data_snapshot_hash"]
                or source_payload["retrieval_policy_hash"] != payload["policy_hash"]
                or source_payload["action_event_hashes"] != payload["action_template_event_hashes"]
            ):
                raise EventTransitionError("retriever v7 authoritative binding differs")
            control_run_id = activation_arm_execution_run_id(
                plan_hash=str(payload["plan_hash"]),
                run_group_id=str(schedule_payload["run_group_id"]),
                arm="control",
            )
            observed = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type IN ('TrialStarted', 'TrialTerminated', 'EvaluationRecorded')
                  AND run_id IN (?, ?)
                """,
                (draft.run_id, control_run_id),
            ).fetchone()
            if observed is not None:
                raise EventTransitionError("retriever v7 follows paired-arm outcomes")
            return
        if event_type == "PreArmFlatScheduleFrozen":
            if draft.run_id != payload["run_group_id"]:
                raise EventTransitionError("pre-arm schedule run group differs")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'PreArmFlatScheduleFrozen'
                  AND (entity_id = ? OR json_extract(payload, '$.schedule_hash') = ?)
                """,
                (payload["schedule_id"], payload["schedule_hash"]),
            ).fetchone()
            run = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationRunRecorded'
                  AND json_extract(payload, '$.plan_hash') = ?
                  AND json_extract(payload, '$.pair_id') = ?
                """,
                (payload["plan_hash"], payload["pair_id"]),
            ).fetchone()
            if prior is not None or run is not None:
                raise EventTransitionError("pre-arm schedule is duplicate or follows arm outcomes")
            return
        if event_type == "ActivationPairExecutionScheduled":
            if draft.run_id != payload["run_group_id"]:
                raise EventTransitionError("Activation pair schedule run differs")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationPairExecutionScheduled'
                  AND (
                    entity_id = ?
                    OR json_extract(payload, '$.schedule_hash') = ?
                    OR (
                      json_extract(payload, '$.plan_hash') = ?
                      AND json_extract(payload, '$.pair_id') = ?
                    )
                  )
                """,
                (
                    payload["schedule_id"],
                    payload["schedule_hash"],
                    payload["plan_hash"],
                    payload["pair_id"],
                ),
            ).fetchone()
            from src.alpha_foundry.activation.runner import (
                activation_arm_execution_run_id,
            )

            arm_run_ids = tuple(
                activation_arm_execution_run_id(
                    plan_hash=str(payload["plan_hash"]),
                    run_group_id=str(payload["run_group_id"]),
                    arm=arm,
                )
                for arm in ("control", "treatment")
            )
            outcome = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type IN (
                  'TrialStarted', 'EvaluationRecorded', 'TrialTerminated'
                ) AND run_id IN (?, ?)
                """,
                arm_run_ids,
            ).fetchone()
            if prior is not None or outcome is not None:
                raise EventTransitionError("Activation pair schedule is duplicate or follows arm outcomes")
            return
        if event_type == "ActivationPairExecutionClaimed":
            schedule_row = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'ActivationPairExecutionScheduled'
                  AND event_hash = ?
                """,
                (payload["schedule_event_hash"],),
            ).fetchone()
            if schedule_row is None:
                raise EventTransitionError("Activation pair claim lacks its schedule")
            schedule_payload = json.loads(str(schedule_row["payload"]))
            if (
                draft.run_id != payload["run_group_id"]
                or str(schedule_row["run_id"]) != draft.run_id
                or schedule_payload["schedule_hash"] != payload["schedule_hash"]
                or schedule_payload["plan_hash"] != payload["plan_hash"]
                or schedule_payload["pair_id"] != payload["pair_id"]
                or schedule_payload["run_group_id"] != payload["run_group_id"]
            ):
                raise EventTransitionError("Activation pair claim differs from schedule")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationPairExecutionClaimed'
                  AND json_extract(payload, '$.schedule_hash') = ?
                """,
                (payload["schedule_hash"],),
            ).fetchone()
            from src.alpha_foundry.activation.runner import (
                activation_arm_execution_run_id,
            )

            arm_run_ids = tuple(
                activation_arm_execution_run_id(
                    plan_hash=str(payload["plan_hash"]),
                    run_group_id=str(payload["run_group_id"]),
                    arm=arm,
                )
                for arm in ("control", "treatment")
            )
            outcome = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type IN (
                  'TrialStarted', 'EvaluationRecorded', 'TrialTerminated'
                ) AND run_id IN (?, ?)
                """,
                arm_run_ids,
            ).fetchone()
            if prior is not None or outcome is not None:
                raise EventTransitionError("Activation pair claim is duplicate or follows arm outcomes")
            return
        if event_type == "OfficialSearchControlRecorded":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'OfficialSearchControlRecorded' AND entity_id = ?",
                (payload["control_id"],),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("official search control evidence already exists")
            return
        if event_type == "ActivationPlanRegistered":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'ActivationPlanRegistered' AND entity_id = ?",
                (draft.entity_id,),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("activation experiment is already registered")
            return
        if event_type == "ActivationRunRecorded":
            self._validate_activation_run_transition(conn, payload)
            return
        if event_type == "ActivationRunSourceAudited":
            self._validate_activation_source_transition(conn, payload)
            return
        if event_type == "ActivationResourceMeasured":
            if draft.run_id != payload["run_group_id"]:
                raise EventTransitionError("Activation resource event run does not match its run group")
            self._validate_activation_resource_transition(conn, payload)
            return
        if event_type == "ActivationResourceMeasuredV2":
            if draft.run_id != payload["run_group_id"]:
                raise EventTransitionError("Activation resource v2 event run differs from run group")
            schedule = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'ActivationPairExecutionScheduled'
                  AND event_hash = ?
                """,
                (payload["pair_schedule_event_hash"],),
            ).fetchone()
            claim = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'ActivationPairExecutionClaimed'
                  AND event_hash = ?
                """,
                (payload["execution_claim_event_hash"],),
            ).fetchone()
            if schedule is None or claim is None:
                raise EventTransitionError("Activation resource v2 lacks schedule or claim")
            schedule_payload = json.loads(str(schedule["payload"]))
            claim_payload = json.loads(str(claim["payload"]))
            if (
                schedule_payload["schedule_hash"] != payload["pair_schedule_hash"]
                or claim_payload["schedule_event_hash"] != payload["pair_schedule_event_hash"]
                or claim_payload["schedule_hash"] != payload["pair_schedule_hash"]
            ):
                raise EventTransitionError("Activation resource v2 schedule authority differs")
            self._validate_activation_resource_transition(conn, payload)
            return
        if event_type == "ActivationGenerationConsumptionRecorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError("Activation generation event run differs from execution run")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            retriever = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'RetrieverDecisionV4Recorded'
                  AND event_hash = ?
                """,
                (payload["retriever_decision_event_hash"],),
            ).fetchone()
            if retriever is None:
                raise EventTransitionError("Activation generation lacks its retriever decision")
            retriever_payload = json.loads(str(retriever["payload"]))
            if (
                str(retriever["run_id"]) != payload["execution_run_id"]
                or retriever_payload["decision_hash"] != payload["retriever_decision_hash"]
                or retriever_payload["control_evidence_event_hash"] != payload["control_evidence_event_hash"]
            ):
                raise EventTransitionError("Activation generation retriever binding differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationGenerationConsumptionRecorded'
                  AND (entity_id = ? OR json_extract(payload, '$.evidence_hash') = ?)
                """,
                (payload["generation_id"], payload["evidence_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("Activation generation-consumption evidence already exists")
            result = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationResultRecorded'
                  AND json_extract(payload, '$.plan_hash') = ?
                """,
                (payload["plan_hash"],),
            ).fetchone()
            if result is not None:
                raise EventTransitionError("Activation generation cannot append after result")
            return
        if event_type == "ActivationGenerationConsumptionV2Recorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError("Activation exact generation run differs from execution run")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            retriever = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'RetrieverDecisionV5Recorded' AND event_hash = ?
                """,
                (payload["retriever_decision_event_hash"],),
            ).fetchone()
            if retriever is None:
                raise EventTransitionError("Activation exact generation lacks its v5 Retriever decision")
            retriever_payload = json.loads(str(retriever["payload"]))
            action_hash_by_id = {
                str(component["action_id"]): str(event_hash)
                for component, event_hash in zip(
                    retriever_payload["components"],
                    retriever_payload["action_template_event_hashes"],
                    strict=True,
                )
            }
            expected_selected_hashes = [
                action_hash_by_id[action_id] for action_id in retriever_payload["selected_action_ids"]
            ]
            if (
                str(retriever["run_id"]) != payload["execution_run_id"]
                or retriever_payload["decision_hash"] != payload["retriever_decision_hash"]
                or retriever_payload["control_evidence_event_hash"] != payload["control_evidence_event_hash"]
                or retriever_payload["selected_action_ids"] != payload["selected_action_ids"]
                or expected_selected_hashes != payload["selected_action_event_hashes"]
                or retriever_payload["selected_parent_factor_spec_ids"] != payload["selected_parent_factor_spec_ids"]
            ):
                raise EventTransitionError("Activation exact generation v5 Retriever binding differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationGenerationConsumptionV2Recorded'
                  AND (entity_id = ? OR json_extract(payload, '$.evidence_hash') = ?)
                """,
                (payload["generation_id"], payload["evidence_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("Activation exact generation evidence already exists")
            result = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationResultRecorded'
                  AND json_extract(payload, '$.plan_hash') = ?
                """,
                (payload["plan_hash"],),
            ).fetchone()
            if result is not None:
                raise EventTransitionError("Activation exact generation cannot append after result")
            return
        if event_type == "ActivationGenerationConsumptionV3Recorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError("source-bound generation run differs from execution run")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            retriever = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'RetrieverDecisionV6Recorded' AND event_hash = ?
                """,
                (payload["retriever_decision_event_hash"],),
            ).fetchone()
            source = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'RetrieverFeatureSourceRecorded' AND event_hash = ?
                """,
                (payload["feature_source_event_hash"],),
            ).fetchone()
            if retriever is None or source is None:
                raise EventTransitionError("source-bound generation authority is missing")
            retriever_payload = json.loads(str(retriever["payload"]))
            source_payload = json.loads(str(source["payload"]))
            if (
                str(retriever["run_id"]) != draft.run_id
                or str(source["run_id"]) != draft.run_id
                or retriever_payload["decision_hash"] != payload["retriever_decision_hash"]
                or retriever_payload["input_bundle_hash"] != payload["retriever_input_bundle_hash"]
                or retriever_payload["feature_source_event_hash"] != payload["feature_source_event_hash"]
                or retriever_payload["feature_source_hash"] != payload["feature_source_hash"]
                or retriever_payload["control_evidence_event_hash"] != payload["control_evidence_event_hash"]
                or retriever_payload["selected_action_ids"] != payload["selected_action_ids"]
                or retriever_payload["selected_parent_factor_spec_ids"] != payload["selected_parent_factor_spec_ids"]
                or source_payload["source_hash"] != payload["feature_source_hash"]
                or source_payload["snapshot_event_hash"] != payload["feature_snapshot_event_hash"]
                or source_payload["snapshot_hash"] != payload["feature_snapshot_hash"]
                or source_payload["feature_policy_hash"] != payload["feature_policy_hash"]
                or source_payload["scorecard_event_hashes"] != payload["feature_scorecard_event_hashes"]
            ):
                raise EventTransitionError("source-bound generation authority differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationGenerationConsumptionV3Recorded'
                  AND (entity_id = ? OR json_extract(payload, '$.evidence_hash') = ?)
                """,
                (payload["generation_id"], payload["evidence_hash"]),
            ).fetchone()
            result = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationResultRecorded'
                  AND json_extract(payload, '$.plan_hash') = ?
                """,
                (payload["plan_hash"],),
            ).fetchone()
            if prior is not None or result is not None:
                raise EventTransitionError("source-bound generation is duplicate or follows result")
            return
        if event_type == "ActivationGenerationConsumptionV4Recorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError("schedule-bound generation run differs from execution run")
            self._activation_plan_payload(conn, str(payload["plan_hash"]))
            retriever = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'RetrieverDecisionV7Recorded' AND event_hash = ?",
                (payload["retriever_decision_event_hash"],),
            ).fetchone()
            schedule = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'PreArmFlatScheduleFrozen' AND event_hash = ?",
                (payload["schedule_event_hash"],),
            ).fetchone()
            source = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'RetrieverFeatureSourceRecorded' AND event_hash = ?",
                (payload["feature_source_event_hash"],),
            ).fetchone()
            if retriever is None or schedule is None or source is None:
                raise EventTransitionError("schedule-bound generation authority is missing")
            rp = json.loads(str(retriever["payload"]))
            sp = json.loads(str(schedule["payload"]))
            fp = json.loads(str(source["payload"]))
            if (
                str(retriever["run_id"]) != draft.run_id
                or str(source["run_id"]) != draft.run_id
                or rp["decision_hash"] != payload["retriever_decision_hash"]
                or rp["input_bundle_hash"] != payload["retriever_input_bundle_hash"]
                or rp["schedule_event_hash"] != payload["schedule_event_hash"]
                or rp["feature_source_event_hash"] != payload["feature_source_event_hash"]
                or rp["selected_action_ids"] != payload["selected_action_ids"]
                or rp["selected_parent_factor_spec_ids"] != payload["selected_parent_factor_spec_ids"]
                or sp["schedule_hash"] != payload["schedule_hash"]
                or sp["plan_hash"] != payload["plan_hash"]
                or sp["pair_id"] != payload["pair_id"]
                or fp["source_hash"] != payload["feature_source_hash"]
                or fp["snapshot_event_hash"] != payload["feature_snapshot_event_hash"]
                or fp["snapshot_hash"] != payload["feature_snapshot_hash"]
                or fp["feature_policy_hash"] != payload["feature_policy_hash"]
                or fp["scorecard_event_hashes"] != payload["feature_scorecard_event_hashes"]
            ):
                raise EventTransitionError("schedule-bound generation authority differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationGenerationConsumptionV4Recorded'
                  AND (entity_id = ? OR json_extract(payload, '$.evidence_hash') = ?)
                """,
                (payload["generation_id"], payload["evidence_hash"]),
            ).fetchone()
            result = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.plan_hash') = ?",
                (payload["plan_hash"],),
            ).fetchone()
            if prior is not None or result is not None:
                raise EventTransitionError("schedule-bound generation is duplicate or follows result")
            return
        if event_type == "ActivationResultRecorded":
            self._validate_activation_result_transition(conn, payload)
            return
        if event_type == "RetrieverActivationDecisionRecorded":
            self._validate_activation_decision_transition(conn, payload)
            return
        if event_type == "ProcessOutcomeRecorded":
            action = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'ProcessActionFrozen' AND entity_id = ?",
                (payload["action_id"],),
            ).fetchone()
            terminal = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'TrialTerminated' AND event_hash = ?",
                (payload["terminal_event_hash"],),
            ).fetchone()
            if action is None or terminal is None:
                raise EventTransitionError("process outcome lacks prior action or terminal evidence")
            if json.loads(action["payload"])["trial_id"] != payload["trial_id"]:
                raise EventTransitionError("process outcome trial does not match frozen action")
            if json.loads(terminal["payload"])["trial_id"] != payload["trial_id"]:
                raise EventTransitionError("process outcome trial does not match terminal evidence")
            if payload["child_factor_spec_id"] is None and payload["ast_diff"] is not None:
                raise EventValidationError("invalid process outcome cannot carry an AST diff")
            return
        if event_type == "SequentialProtocolRegistered":
            self._validate_sequential_protocol_transition(conn, payload)
            return
        if event_type == "SequentialLookRecorded":
            self._validate_sequential_look_transition(conn, payload)
            return
        if event_type == "FalsificationResultRecorded":
            contract = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'FalsificationContractRegistered' AND entity_id = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (payload["contract_id"],),
            ).fetchone()
            if contract is None or json.loads(contract["payload"])["contract_hash"] != payload["contract_hash"]:
                raise EventTransitionError("falsification result has no matching prior contract")
            self._require_terminal_sequential_look_if_applicable(conn, payload)
            return
        if event_type == "MechanismEvidenceIndexRecorded":
            self._validate_mechanism_evidence_index_transition(conn, payload)
            return
        if event_type == "ComplementEvidenceRecorded":
            self._validate_complement_evidence_transition(conn, payload)
            return
        if event_type == "DecisionEvidenceV3Recorded":
            if draft.run_id != payload["evidence_run_id"]:
                raise EventTransitionError("Decision evidence event run differs from producer run")
            if self._tail_hash(conn) != payload["ledger_watermark_event_hash"]:
                raise EventTransitionError("Decision evidence watermark is stale or widened")
            definition = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
                """,
                (payload["factor_spec_id"],),
            ).fetchone()
            if definition is None:
                raise EventTransitionError("Decision evidence has no prior factor definition")
            prior_rows = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'DecisionEvidenceV3Recorded'
                  AND run_id = ?
                """,
                (draft.run_id,),
            ).fetchall()
            if any(
                (prior := json.loads(row["payload"]))["factor_spec_id"] == payload["factor_spec_id"]
                and prior["evidence_kind"] == payload["evidence_kind"]
                for row in prior_rows
            ):
                raise EventTransitionError("Decision evidence kind already exists for factor and run")
            return
        if event_type == "ScorecardDecisionEvidenceV3Recorded":
            if draft.run_id != payload["evidence_run_id"]:
                raise EventTransitionError("scorecard evidence event run differs from producer run")
            if self._tail_hash(conn) != payload["source_watermark_event_hash"]:
                raise EventTransitionError("scorecard evidence watermark is stale")
            definition = conn.execute(
                """
                SELECT run_id, entity_id FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND event_hash = ?
                """,
                (payload["factor_definition_event_hash"],),
            ).fetchone()
            policy = conn.execute(
                """
                SELECT run_id FROM research_events
                WHERE event_type = 'EvaluationPolicyRegistered' AND event_hash = ?
                """,
                (payload["evaluation_policy_event_hash"],),
            ).fetchone()
            snapshot = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrainValidDataSnapshotFrozen' AND event_hash = ?
                """,
                (payload["snapshot_event_hash"],),
            ).fetchone()
            started = conn.execute(
                """
                SELECT run_id FROM research_events
                WHERE event_type = 'TrialStarted' AND entity_id = ?
                """,
                (payload["trial_id"],),
            ).fetchone()
            terminal = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrialTerminated' AND entity_id = ?
                """,
                (payload["trial_id"],),
            ).fetchone()
            if (
                definition is None
                or policy is None
                or snapshot is None
                or started is None
                or terminal is not None
                or str(definition["run_id"]) != draft.run_id
                or str(definition["entity_id"]) != payload["factor_spec_id"]
                or str(policy["run_id"]) != draft.run_id
                or str(started["run_id"]) != draft.run_id
            ):
                raise EventTransitionError("scorecard evidence source identity or order differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ScorecardDecisionEvidenceV3Recorded'
                  AND run_id = ?
                  AND json_extract(payload, '$.factor_spec_id') = ?
                  AND json_extract(payload, '$.trial_id') = ?
                """,
                (draft.run_id, payload["factor_spec_id"], payload["trial_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("scorecard evidence already exists for factor trial")
            return
        if event_type == "SnapshotDecisionEvidenceV3Recorded":
            if draft.run_id != payload["evidence_run_id"]:
                raise EventTransitionError("snapshot evidence event run differs from producer run")
            if self._tail_hash(conn) != payload["source_watermark_event_hash"]:
                raise EventTransitionError("snapshot evidence watermark is stale")
            definition = conn.execute(
                """
                SELECT run_id, entity_id FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND event_hash = ?
                """,
                (payload["factor_definition_event_hash"],),
            ).fetchone()
            policy = conn.execute(
                """
                SELECT run_id FROM research_events
                WHERE event_type = 'EvaluationPolicyRegistered' AND event_hash = ?
                """,
                (payload["evaluation_policy_event_hash"],),
            ).fetchone()
            snapshot = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'TrainValidDataSnapshotFrozen' AND event_hash = ?
                """,
                (payload["snapshot_event_hash"],),
            ).fetchone()
            if (
                definition is None
                or policy is None
                or snapshot is None
                or str(definition["run_id"]) != draft.run_id
                or str(definition["entity_id"]) != payload["factor_spec_id"]
                or str(policy["run_id"]) != draft.run_id
            ):
                raise EventTransitionError("snapshot evidence source identity or order differs")
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'SnapshotDecisionEvidenceV3Recorded'
                  AND run_id = ?
                  AND json_extract(payload, '$.factor_spec_id') = ?
                """,
                (draft.run_id, payload["factor_spec_id"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("snapshot evidence already exists for factor and run")
            return
        if event_type in {"QualityDecisionV2Recorded", "QualityDecisionV3Recorded"}:
            definition = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
                """,
                (payload["factor_spec_id"],),
            ).fetchone()
            if definition is None:
                raise EventTransitionError("Decision v2 has no prior factor definition")
            return
        if event_type in {
            "FalsificationCatalogV2Registered",
            "FalsificationContractV2Registered",
            "FalsificationOutcomeAccessV2Recorded",
            "FalsificationSourceArtifactV2Recorded",
            "FalsificationResultV2Recorded",
        }:
            self._validate_falsification_v2_transition(conn, event_type, draft.run_id, payload)
            return
        if event_type in {
            "FinalRawProviderV2Registered",
            "FinalEvaluationEligibilityRecorded",
            "FinalTestCapabilityV2Issued",
            "FinalOutcomeAccessV2Recorded",
            "FinalContaminationV2Recorded",
            "FinalTestArtifactV2Recorded",
            "FinalTestFailedV2Recorded",
            "FinalSelectionAssessmentV2Recorded",
        }:
            self._validate_final_v2_transition(conn, event_type, draft.run_id, payload)
            return
        if event_type == "FinalCandidateFrozen":
            self._validate_final_candidate_transition(conn, payload)
            return
        if event_type == "FinalTestCapabilityIssued":
            self._validate_final_capability_transition(conn, payload)
            return
        if event_type == "FinalTestAccessRecorded":
            self._validate_final_access_transition(conn, payload)
            return
        if event_type == "FinalTestArtifactRecorded":
            self._validate_final_artifact_transition(conn, payload)
            return
        if event_type in {
            "ForwardMonitoringProviderV3Registered",
            "ForwardPlanV3Recorded",
            "ForwardSourceArtifactV3Recorded",
            "ForwardObservationV3Recorded",
            "DataRevisionRecorded",
        }:
            self._validate_forward_v3_transition(conn, event_type, draft.run_id, payload)
            return
        if event_type == "ForwardPlanV2Recorded":
            self._validate_forward_plan_v2_transition(conn, payload)
            return
        if event_type == "ForwardObservationV2Recorded":
            self._validate_forward_observation_v2_transition(conn, payload)
            return
        if event_type == "ForwardObservationRecorded":
            plan = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ForwardPlanRecorded' AND entity_id = ?
                """,
                (payload["plan_id"],),
            ).fetchone()
            if plan is None:
                raise EventTransitionError("forward observation has no prior plan")
            return
        if event_type == "GenerationFailureRecorded":
            trial_id = str(payload["trial_id"])
            started = conn.execute(
                "SELECT run_id FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
                (trial_id,),
            ).fetchone()
            terminal = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'TrialTerminated' AND entity_id = ?",
                (trial_id,),
            ).fetchone()
            if started is None or terminal is not None or str(started["run_id"]) != draft.run_id:
                raise EventTransitionError("generation failure requires a prior active trial in the same run")
            return
        if event_type not in {"TrialStarted", "EvaluationRecorded", "TrialTerminated"}:
            return
        trial_id = str(payload["trial_id"])
        if event_type in {"TrialStarted", "TrialTerminated"} and draft.entity_id != trial_id:
            raise EventValidationError("trial entity_id must equal payload trial_id")
        started = conn.execute(
            "SELECT event_hash, run_id FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
            (trial_id,),
        ).fetchone()
        terminal = conn.execute(
            "SELECT event_hash FROM research_events WHERE event_type = 'TrialTerminated' AND entity_id = ?",
            (trial_id,),
        ).fetchone()
        if event_type == "TrialStarted":
            if started is not None:
                raise EventTransitionError(f"trial already started: {trial_id}")
            if terminal is not None:
                raise EventTransitionError(f"trial already terminated: {trial_id}")
            return
        if started is None:
            raise EventTransitionError(f"trial was not started: {trial_id}")
        if terminal is not None:
            raise EventTransitionError(f"trial already terminated: {trial_id}")
        if str(started["run_id"]) != draft.run_id:
            raise EventTransitionError("trial lifecycle events must share one run")
        if event_type == "TrialTerminated" and payload["status"] == "success":
            evaluation_hash = payload["evaluation_event_hash"]
            if evaluation_hash is None:
                raise EventTransitionError("successful trial requires terminal evaluation evidence")
            evaluation = conn.execute(
                """
                SELECT run_id, payload FROM research_events
                WHERE event_type = 'EvaluationRecorded' AND event_hash = ?
                """,
                (evaluation_hash,),
            ).fetchone()
            if (
                evaluation is None
                or str(evaluation["run_id"]) != draft.run_id
                or json.loads(evaluation["payload"])["trial_id"] != trial_id
            ):
                raise EventTransitionError("successful trial evaluation evidence is missing or mismatched")

    @staticmethod
    def _validate_process_action_v2_transition(
        conn: sqlite3.Connection,
        draft: EventDraft,
        payload: Mapping[str, Any],
    ) -> None:
        started = conn.execute(
            "SELECT seq, run_id, payload FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
            (payload["trial_id"],),
        ).fetchone()
        if started is None:
            raise EventTransitionError("process action v2 references no prior trial start")
        started_payload = json.loads(str(started["payload"]))
        if started_payload["candidate_id"] != payload["candidate_id"] or str(started["run_id"]) != draft.run_id:
            raise EventTransitionError("process action v2 does not match its frozen trial")
        parent = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
            (payload["parent_factor_spec_id"],),
        ).fetchone()
        if parent is None:
            raise EventTransitionError("process action v2 has no prior parent definition")
        watermark = conn.execute(
            "SELECT seq FROM research_events WHERE event_hash = ?",
            (payload["eligible_event_watermark"],),
        ).fetchone()
        if watermark is None or int(watermark["seq"]) >= int(started["seq"]):
            raise EventTransitionError("process action v2 watermark must exist before its trial")
        definitions = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'FactorDefinitionRecorded' ORDER BY seq ASC"
        ).fetchall()
        if any(
            str(json.loads(str(row["payload"]))["metadata"].get("originating_trial_id", "")) == payload["trial_id"]
            for row in definitions
        ):
            raise EventTransitionError("process action v2 must be frozen before child generation")
        prior_actions = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ProcessActionFrozenV2' ORDER BY seq ASC"
        ).fetchall()
        if any(json.loads(str(row["payload"]))["trial_id"] == payload["trial_id"] for row in prior_actions):
            raise EventTransitionError("trial already has a frozen process action v2")

    @staticmethod
    def _validate_process_outcome_v2_transition(
        conn: sqlite3.Connection,
        draft: EventDraft,
        payload: Mapping[str, Any],
    ) -> None:
        action_row = conn.execute(
            "SELECT run_id, payload FROM research_events WHERE event_type = 'ProcessActionFrozenV2' AND entity_id = ?",
            (payload["action_id"],),
        ).fetchone()
        if action_row is None:
            raise EventTransitionError("process outcome v2 has no prior frozen action")
        prior_outcomes = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ProcessOutcomeRecordedV2' ORDER BY seq ASC"
        ).fetchall()
        if any(json.loads(str(row["payload"]))["action_id"] == payload["action_id"] for row in prior_outcomes):
            raise EventTransitionError("process action v2 already has a terminal outcome")
        action = json.loads(str(action_row["payload"]))
        matching_fields = (
            "trial_id",
            "policy_hash",
            "utility_policy_hash",
            "data_snapshot_hash",
            "regime_config_hash",
            "run_group_id",
        )
        if any(action[name] != payload[name] for name in matching_fields) or str(action_row["run_id"]) != draft.run_id:
            raise EventTransitionError("process outcome v2 does not match its frozen action")

        terminal_row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'TrialTerminated' AND event_hash = ?",
            (payload["terminal_event_hash"],),
        ).fetchone()
        evaluation_row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'EvaluationRecorded' AND event_hash = ?",
            (payload["evaluation_event_hash"],),
        ).fetchone()
        derivation_row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'DerivationRecorded' AND event_hash = ?",
            (payload["derivation_event_hash"],),
        ).fetchone()
        if terminal_row is None or evaluation_row is None or derivation_row is None:
            raise EventTransitionError("process outcome v2 lacks terminal, evaluation, or derivation evidence")
        terminal = json.loads(str(terminal_row["payload"]))
        evaluation = json.loads(str(evaluation_row["payload"]))
        derivation = json.loads(str(derivation_row["payload"]))
        if terminal["trial_id"] != payload["trial_id"] or terminal["status"] not in {"success", "reject"}:
            raise EventTransitionError("process outcome v2 requires an evaluated terminal trial")
        if terminal["status"] == "success" and terminal["evaluation_event_hash"] != payload["evaluation_event_hash"]:
            raise EventTransitionError("successful process outcome v2 cites another evaluation")
        if (
            evaluation["trial_id"] != payload["trial_id"]
            or evaluation["factor_spec_id"] != payload["child_factor_spec_id"]
            or evaluation["data_scope"] != payload["data_scope"]
            or evaluation["scorecard_hash"] != payload["scorecard_hash"]
        ):
            raise EventTransitionError("process outcome v2 evaluation binding is mismatched")
        if not any(
            ref["artifact_hash"] == payload["scorecard_hash"]
            and ref["media_type"] == "application/vnd.vibe.alpha-quality-scorecard+json"
            for ref in evaluation["artifact_refs"]
        ):
            raise EventTransitionError("process outcome v2 requires its immutable scorecard artifact")
        if (
            derivation["child_factor_spec_id"] != payload["child_factor_spec_id"]
            or derivation["trial_terminal_event_hash"] != payload["terminal_event_hash"]
            or action["parent_factor_spec_id"] not in derivation["parent_factor_spec_ids"]
        ):
            raise EventTransitionError("process outcome v2 derivation binding is mismatched")

        definitions: dict[str, dict[str, Any]] = {}
        for factor_id in (action["parent_factor_spec_id"], payload["child_factor_spec_id"]):
            row = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (factor_id,),
            ).fetchone()
            if row is None:
                raise EventTransitionError("process outcome v2 has no canonical factor definition")
            definitions[str(factor_id)] = json.loads(str(row["payload"]))
        parent_definition = definitions[str(action["parent_factor_spec_id"])]
        child_definition = definitions[str(payload["child_factor_spec_id"])]
        try:
            from src.alpha_foundry.dsl.diff import ast_diff_from_dict

            diff = ast_diff_from_dict(
                payload["ast_diff"],
                parent_ast=parent_definition["metadata"]["canonical_ast"],
                child_ast=child_definition["metadata"]["canonical_ast"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventTransitionError("process outcome v2 contains an invalid canonical AST diff") from exc
        if (
            diff.parent_expression_id != parent_definition["expression_id"]
            or diff.child_expression_id != child_definition["expression_id"]
            or diff.grammar_version != child_definition["grammar_version"]
            or diff.grammar_hash != child_definition["grammar_hash"]
            or canonical_json_hash(diff.to_dict()) != payload["ast_diff_hash"]
        ):
            raise EventTransitionError("process outcome v2 AST identity binding is mismatched")

    @staticmethod
    def _validate_retriever_action_template_transition(
        conn: sqlite3.Connection,
        draft: EventDraft,
        payload: Mapping[str, Any],
    ) -> None:
        if draft.run_id != payload["execution_run_id"]:
            raise EventTransitionError("Retriever action envelope run differs from its execution run")
        existing = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type = 'RetrieverActionTemplateFrozen' AND entity_id = ?
            """,
            (payload["action_id"],),
        ).fetchone()
        if existing is not None:
            raise EventTransitionError("Retriever action identity already exists")
        definition = conn.execute(
            """
            SELECT seq, event_hash FROM research_events
            WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
            ORDER BY seq ASC LIMIT 1
            """,
            (payload["parent_factor_spec_id"],),
        ).fetchone()
        if definition is None or str(definition["event_hash"]) != payload["parent_definition_event_hash"]:
            raise EventTransitionError("Retriever action parent definition hash differs")
        ResearchEventStore._validate_retriever_v2_transition(
            conn,
            {
                "eligible_event_watermark": payload["eligible_event_watermark"],
                "components": [{"factor_spec_id": payload["parent_factor_spec_id"]}],
            },
        )

    @staticmethod
    def _validate_retriever_v2_transition(
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        watermark = conn.execute(
            "SELECT seq FROM research_events WHERE event_hash = ?",
            (payload["eligible_event_watermark"],),
        ).fetchone()
        if watermark is None:
            raise EventTransitionError("retriever v2 cites an unknown discovery watermark")
        watermark_seq = int(watermark["seq"])
        evaluations = conn.execute(
            "SELECT seq, event_hash, payload FROM research_events WHERE event_type = 'EvaluationRecorded' ORDER BY seq ASC"
        ).fetchall()
        terminals = conn.execute(
            "SELECT seq, event_hash, payload FROM research_events WHERE event_type = 'TrialTerminated' ORDER BY seq ASC"
        ).fetchall()
        outcomes = conn.execute(
            "SELECT seq, payload FROM research_events WHERE event_type = 'ProcessOutcomeRecordedV2' ORDER BY seq ASC"
        ).fetchall()
        evaluation_payloads = [
            (int(row["seq"]), str(row["event_hash"]), json.loads(str(row["payload"]))) for row in evaluations
        ]
        terminal_payloads = [
            (int(row["seq"]), str(row["event_hash"]), json.loads(str(row["payload"]))) for row in terminals
        ]
        outcome_payloads = [(int(row["seq"]), json.loads(str(row["payload"]))) for row in outcomes]
        for component in payload["components"]:
            factor_id = component["factor_spec_id"]
            definition = conn.execute(
                "SELECT seq FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
                (factor_id,),
            ).fetchone()
            if definition is None or int(definition["seq"]) > watermark_seq:
                raise EventTransitionError("retriever v2 candidate has no factor definition")
            eligible = False
            for evaluation_seq, evaluation_hash, evaluation in evaluation_payloads:
                if evaluation_seq > watermark_seq:
                    continue
                if evaluation["factor_spec_id"] != factor_id or evaluation["data_scope"] not in {
                    "valid",
                    "train_valid",
                }:
                    continue
                for terminal_seq, terminal_hash, terminal in terminal_payloads:
                    if terminal_seq > watermark_seq:
                        continue
                    if terminal["trial_id"] != evaluation["trial_id"] or terminal["status"] not in {
                        "success",
                        "reject",
                    }:
                        continue
                    directly_cited = terminal["evaluation_event_hash"] == evaluation_hash
                    outcome_cited = any(
                        outcome_seq <= watermark_seq
                        and outcome["terminal_event_hash"] == terminal_hash
                        and outcome["evaluation_event_hash"] == evaluation_hash
                        for outcome_seq, outcome in outcome_payloads
                    )
                    if directly_cited or outcome_cited:
                        eligible = True
                        break
                if eligible:
                    break
            if not eligible:
                raise EventTransitionError("retriever v2 candidate lacks terminal train/valid evidence")

    @staticmethod
    def _activation_plan_payload(
        conn: sqlite3.Connection,
        plan_hash: str,
    ) -> dict[str, Any]:
        row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ActivationPlanRegistered' AND json_extract(payload, '$.plan_hash') = ? ORDER BY seq DESC LIMIT 1",
            (plan_hash,),
        ).fetchone()
        if row is None:
            raise EventTransitionError("activation evidence has no prior registered plan")
        return json.loads(str(row["payload"]))

    @classmethod
    def _validate_activation_run_transition(
        cls,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        cls._activation_plan_payload(conn, str(payload["plan_hash"]))
        prior = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationRunRecorded' AND entity_id = ?",
            (payload["manifest_id"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("activation run manifest is already recorded")
        result = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.plan_hash') = ?",
            (payload["plan_hash"],),
        ).fetchone()
        if result is not None:
            raise EventTransitionError("activation run cannot append after its frozen result")

    @classmethod
    def _validate_activation_source_transition(
        cls,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        cls._activation_plan_payload(conn, str(payload["plan_hash"]))
        summary = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type = 'ActivationRunRecorded'
            AND json_extract(payload, '$.manifest_hash') = ?
            AND json_extract(payload, '$.plan_hash') = ?
            """,
            (payload["summary_manifest_hash"], payload["plan_hash"]),
        ).fetchone()
        if summary is None:
            raise EventTransitionError("Activation source audit has no prior run summary")
        prior = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationRunSourceAudited' AND entity_id = ?",
            (payload["audit_id"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("Activation run source audit already exists")
        result = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.plan_hash') = ?",
            (payload["plan_hash"],),
        ).fetchone()
        if result is not None:
            raise EventTransitionError("Activation source audit cannot append after result")

    @classmethod
    def _validate_activation_resource_transition(
        cls,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        cls._activation_plan_payload(conn, str(payload["plan_hash"]))
        summary = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'ActivationRunRecorded'
            AND json_extract(payload, '$.manifest_hash') = ?
            AND json_extract(payload, '$.plan_hash') = ?
            """,
            (payload["manifest_hash"], payload["plan_hash"]),
        ).fetchone()
        if summary is None:
            raise EventTransitionError("Activation resource lacks prior run summary")
        run = json.loads(str(summary["payload"]))
        if (
            run["run_group_id"] != payload["run_group_id"]
            or run["pair_id"] != payload["pair_id"]
            or run["arm"] != payload["arm"]
        ):
            raise EventTransitionError("Activation resource differs from run summary")
        prior = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type IN (
              'ActivationResourceMeasured', 'ActivationResourceMeasuredV2'
            )
              AND (
                entity_id = ?
                OR json_extract(payload, '$.evidence_hash') = ?
                OR json_extract(payload, '$.manifest_hash') = ?
              )
            """,
            (
                payload["resource_id"],
                payload["evidence_hash"],
                payload["manifest_hash"],
            ),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("Activation run already has resource evidence")
        result = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.plan_hash') = ?",
            (payload["plan_hash"],),
        ).fetchone()
        if result is not None:
            raise EventTransitionError("Activation resource cannot append after result")

    @classmethod
    def _validate_activation_result_transition(
        cls,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        cls._activation_plan_payload(conn, str(payload["plan_hash"]))
        prior = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.plan_hash') = ?",
            (payload["plan_hash"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("activation plan already has a frozen result")
        runs = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ActivationRunRecorded' AND json_extract(payload, '$.plan_hash') = ? ORDER BY seq ASC",
            (payload["plan_hash"],),
        ).fetchall()
        if not runs:
            if payload["replayable"] or not payload["invalidation_reasons"]:
                raise EventTransitionError("outcome-free activation result must explicitly invalidate preflight")
            return
        arms: dict[str, set[str]] = {}
        for row in runs:
            run = json.loads(str(row["payload"]))
            arms.setdefault(str(run["pair_id"]), set()).add(str(run["arm"]))
        complete_pairs = sum(1 for pair_arms in arms.values() if pair_arms == {"control", "treatment"})
        if int(payload["complete_pairs"]) != complete_pairs:
            raise EventTransitionError("activation result pair count does not replay from run events")

    @classmethod
    def _validate_activation_decision_transition(
        cls,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        plan = cls._activation_plan_payload(conn, str(payload["plan_hash"]))
        result_row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ActivationResultRecorded' AND json_extract(payload, '$.result_hash') = ? ORDER BY seq DESC LIMIT 1",
            (payload["result_hash"],),
        ).fetchone()
        if result_row is None:
            raise EventTransitionError("activation decision has no prior frozen result")
        result = json.loads(str(result_row["payload"]))
        if result["plan_hash"] != payload["plan_hash"]:
            raise EventTransitionError("activation decision result belongs to another plan")
        if payload["verdict"] == "approved":
            if plan["phase"] != "confirmatory":
                raise EventTransitionError("pilot activation cannot be approved")
            if not result["replayable"] or result["invalidation_reasons"]:
                raise EventTransitionError("invalid or unreplayable activation cannot be approved")
        prior = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'RetrieverActivationDecisionRecorded' AND json_extract(payload, '$.plan_hash') = ?",
            (payload["plan_hash"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("activation plan already has a deterministic decision")

    @staticmethod
    def _validate_complement_evidence_transition(
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        factor_spec_id = str(payload["factor_spec_id"])
        definition = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?
            """,
            (factor_spec_id,),
        ).fetchone()
        if definition is None:
            raise EventTransitionError("complement evidence has no prior factor definition")

        evaluation_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'EvaluationRecorded' AND event_hash = ?
            """,
            (payload["source_evaluation_event_hash"],),
        ).fetchone()
        terminal_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'TrialTerminated' AND event_hash = ?
            """,
            (payload["source_terminal_event_hash"],),
        ).fetchone()
        if evaluation_row is None or terminal_row is None:
            raise EventTransitionError("complement evidence lacks prior evaluation or terminal evidence")

        evaluation = json.loads(str(evaluation_row["payload"]))
        terminal = json.loads(str(terminal_row["payload"]))
        if evaluation["factor_spec_id"] != factor_spec_id:
            raise EventTransitionError("complement evidence factor does not match evaluation")
        if evaluation["data_scope"] != payload["data_scope"]:
            raise EventTransitionError("complement evidence scope does not match evaluation")
        if evaluation["data_scope"] not in {"valid", "train_valid"}:
            raise EventTransitionError("complement evidence requires train/valid evaluation")
        if terminal["trial_id"] != evaluation["trial_id"]:
            raise EventTransitionError("complement terminal and evaluation trials do not match")
        if terminal["status"] not in {"success", "reject"}:
            raise EventTransitionError("complement evidence requires an evaluated terminal outcome")
        if terminal["status"] == "success" and (
            terminal["evaluation_event_hash"] != payload["source_evaluation_event_hash"]
        ):
            raise EventTransitionError("successful terminal does not reference complement evaluation")

    @staticmethod
    def _validate_falsification_v2_transition(
        conn: sqlite3.Connection,
        event_type: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "FalsificationCatalogV2Registered":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.catalog_hash') = ?",
                (event_type, payload["catalog_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("falsification v2 catalog is already registered")
            return
        if event_type == "FalsificationContractV2Registered":
            catalog = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FalsificationCatalogV2Registered' AND event_hash = ?",
                (payload["catalog_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.contract_hash') = ?",
                (event_type, payload["contract_hash"]),
            ).fetchone()
            if (
                catalog is None
                or str(catalog["run_id"]) != run_id
                or json.loads(str(catalog["payload"]))["catalog_hash"] != payload["catalog_hash"]
                or prior is not None
            ):
                raise EventTransitionError("falsification v2 contract lacks its exact catalog")
            return
        contract = conn.execute(
            "SELECT run_id, payload FROM research_events WHERE event_type = 'FalsificationContractV2Registered' AND event_hash = ?",
            (payload["contract_event_hash"],),
        ).fetchone()
        if contract is None or str(contract["run_id"]) != run_id:
            raise EventTransitionError("falsification v2 event lacks its exact contract")
        contract_payload = json.loads(str(contract["payload"]))
        if (
            contract_payload["contract_hash"] != payload["contract_hash"]
            or contract_payload["factor_spec_id"] != payload["factor_spec_id"]
        ):
            raise EventTransitionError("falsification v2 contract binding differs")
        expected_test_ids = [item["test_id"] for item in contract_payload["contract"]["tests"]]
        if event_type == "FalsificationOutcomeAccessV2Recorded":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.contract_hash') = ?",
                (event_type, payload["contract_hash"]),
            ).fetchone()
            if prior is not None or payload["test_ids"] != expected_test_ids:
                raise EventTransitionError("falsification v2 outcome access differs or repeats")
            return
        if event_type == "FalsificationSourceArtifactV2Recorded":
            access = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FalsificationOutcomeAccessV2Recorded' AND event_hash = ?",
                (payload["access_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.contract_hash') = ?",
                (event_type, payload["contract_hash"]),
            ).fetchone()
            if (
                access is None
                or str(access["run_id"]) != run_id
                or json.loads(str(access["payload"]))["contract_event_hash"] != payload["contract_event_hash"]
                or payload["test_ids"] != expected_test_ids
                or prior is not None
            ):
                raise EventTransitionError("falsification v2 source authority differs")
            return
        if event_type == "FalsificationResultV2Recorded":
            source = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FalsificationSourceArtifactV2Recorded' AND event_hash = ?",
                (payload["source_event_hash"],),
            ).fetchone()
            access = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FalsificationOutcomeAccessV2Recorded' AND event_hash = ?",
                (payload["outcome_access_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.contract_hash') = ?",
                (event_type, payload["contract_hash"]),
            ).fetchone()
            result_ids = [item["test_id"] for item in payload["test_results"]]
            if (
                source is None
                or access is None
                or str(source["run_id"]) != run_id
                or str(access["run_id"]) != run_id
                or json.loads(str(source["payload"]))["access_event_hash"] != payload["outcome_access_event_hash"]
                or json.loads(str(source["payload"]))["source_hash"] != payload["source_hash"]
                or payload["family_id"] != contract_payload["family_id"]
                or result_ids != expected_test_ids
                or prior is not None
            ):
                raise EventTransitionError("falsification v2 result authority differs")
            return

    @staticmethod
    def _validate_final_v2_transition(
        conn: sqlite3.Connection,
        event_type: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "FinalRawProviderV2Registered":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.descriptor.provider_id') = ? AND json_extract(payload, '$.descriptor.provider_version') = ?",
                (
                    event_type,
                    payload["descriptor"]["provider_id"],
                    payload["descriptor"]["provider_version"],
                ),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("final raw provider is already registered")
            return
        eligibility_hash = payload.get("eligibility_event_hash")
        if event_type == "FinalEvaluationEligibilityRecorded":
            expected = {
                payload["factor_definition_event_hash"]: "FactorDefinitionRecorded",
                payload["prefinal_decision_event_hash"]: "QualityDecisionV4Recorded",
                payload["contract_event_hash"]: "ResolvedEvaluationContractRegistered",
                payload["provider_registration_event_hash"]: "FinalRawProviderV2Registered",
            }
            rows = {
                str(row["event_hash"]): row
                for row in conn.execute(
                    "SELECT event_hash, event_type, run_id, payload FROM research_events WHERE event_hash IN (%s)"
                    % ",".join("?" for _ in expected),
                    tuple(expected),
                ).fetchall()
            }
            if len(rows) != len(expected) or any(
                rows[event_hash]["event_type"] != expected_type or str(rows[event_hash]["run_id"]) != run_id
                for event_hash, expected_type in expected.items()
            ):
                raise EventTransitionError("final v2 eligibility sources differ")
            decision = json.loads(str(rows[payload["prefinal_decision_event_hash"]]["payload"]))
            contract = json.loads(str(rows[payload["contract_event_hash"]]["payload"]))
            definition = json.loads(str(rows[payload["factor_definition_event_hash"]]["payload"]))
            provider = json.loads(str(rows[payload["provider_registration_event_hash"]]["payload"]))
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.research_family_id') = ?",
                (event_type, payload["research_family_id"]),
            ).fetchone()
            if (
                prior is not None
                or decision["decision"] not in {"research_only", "candidate_zoo"}
                or decision["decision_hash"] != payload["prefinal_decision_hash"]
                or decision["factor_spec_id"] != payload["factor_spec_id"]
                or definition["factor_spec_id"] != payload["factor_spec_id"]
                or contract["research_family_id"] != payload["research_family_id"]
                or contract["contract_hash"] != payload["contract_hash"]
                or provider["descriptor"]["provider_id"] != payload["scope_config"]["provider_id"]
                or provider["descriptor"]["provider_version"] != payload["scope_config"]["provider_version"]
            ):
                raise EventTransitionError("final v2 eligibility authority differs")
            return
        if event_type == "FinalSelectionAssessmentV2Recorded":
            eligibility = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FinalEvaluationEligibilityRecorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchone()
        else:
            eligibility = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FinalEvaluationEligibilityRecorded' AND event_hash = ?",
                (eligibility_hash,),
            ).fetchone()
        if eligibility is None or str(eligibility["run_id"]) != run_id:
            raise EventTransitionError("final v2 event lacks exact eligibility")
        eligibility_payload = json.loads(str(eligibility["payload"]))
        if (
            eligibility_payload["eligibility_hash"]
            != payload.get("eligibility_hash", eligibility_payload["eligibility_hash"])
            or eligibility_payload["final_evaluation_key"] != payload["final_evaluation_key"]
        ):
            raise EventTransitionError("final v2 eligibility binding differs")
        if event_type == "FinalTestCapabilityV2Issued":
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.final_evaluation_key') = ?",
                (event_type, payload["final_evaluation_key"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("final v2 capability already exists")
            return
        if event_type == "FinalOutcomeAccessV2Recorded":
            capability = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FinalTestCapabilityV2Issued' AND json_extract(payload, '$.capability_hash') = ?",
                (payload["capability_hash"],),
            ).fetchone()
            if capability is None or str(capability["run_id"]) != run_id:
                raise EventTransitionError("final v2 access lacks capability")
            prior_allowed = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.final_evaluation_key') = ? AND json_extract(payload, '$.outcome') = 'allowed'",
                (event_type, payload["final_evaluation_key"]),
            ).fetchone()
            if payload["outcome"] == "allowed" and prior_allowed is not None:
                raise EventTransitionError("final v2 physical access already occurred")
            return
        if event_type == "FinalContaminationV2Recorded":
            return
        if event_type in {"FinalTestArtifactV2Recorded", "FinalTestFailedV2Recorded"}:
            access = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'FinalOutcomeAccessV2Recorded' AND event_hash = ?",
                (payload["access_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type IN ('FinalTestArtifactV2Recorded', 'FinalTestFailedV2Recorded') AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchone()
            if (
                access is None
                or str(access["run_id"]) != run_id
                or json.loads(str(access["payload"]))["outcome"] != "allowed"
                or prior is not None
                or payload["research_family_id"] != eligibility_payload["research_family_id"]
                or payload["factor_spec_id"] != eligibility_payload["factor_spec_id"]
            ):
                raise EventTransitionError("final v2 terminal authority differs")
            return
        if event_type == "FinalSelectionAssessmentV2Recorded":
            accesses = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'FinalOutcomeAccessV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchall()
            artifacts = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'FinalTestArtifactV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchall()
            failures = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'FinalTestFailedV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchall()
            taints = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type = 'FinalContaminationV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchall()
            expected_sources = sorted(str(row["event_hash"]) for row in (*accesses, *artifacts, *failures, *taints))
            if (
                payload["access_count"] != len(accesses)
                or payload["artifact_count"] != len(artifacts)
                or payload["failure_count"] != len(failures)
                or payload["terminal_count"] != len(artifacts) + len(failures)
                or payload["taint_count"] != len(taints)
                or payload["source_event_hashes"] != expected_sources
            ):
                raise EventTransitionError("final v2 selection population differs")
            return

    @staticmethod
    def _validate_final_candidate_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        definition = conn.execute(
            "SELECT event_hash FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ? ORDER BY seq DESC LIMIT 1",
            (payload["factor_spec_id"],),
        ).fetchone()
        if definition is None:
            raise EventTransitionError("final candidate has no prior factor definition")
        if definition["event_hash"] != payload["definition_hash"]:
            raise EventTransitionError("final candidate definition hash does not match ledger")
        prior = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'FinalCandidateFrozen' AND entity_id = ?",
            (payload["freeze_id"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("final candidate freeze already exists")

    @staticmethod
    def _validate_final_capability_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        freeze = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FinalCandidateFrozen'
            AND json_extract(payload, '$.candidate_hash') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["candidate_hash"],),
        ).fetchone()
        if freeze is None:
            raise EventTransitionError("final capability has no prior frozen candidate")
        frozen = json.loads(str(freeze["payload"]))
        if (
            frozen["factor_spec_id"] != payload["factor_spec_id"]
            or frozen["data_snapshot_hash"] != payload["data_snapshot_hash"]
        ):
            raise EventTransitionError("final capability does not match frozen candidate")
        prior = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type = 'FinalTestCapabilityIssued'
            AND json_extract(payload, '$.candidate_hash') = ?
            """,
            (payload["candidate_hash"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("frozen candidate already has a final capability")

    @staticmethod
    def _validate_final_access_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        definition = conn.execute(
            "SELECT 1 FROM research_events WHERE event_type = 'FactorDefinitionRecorded' AND entity_id = ?",
            (payload["factor_spec_id"],),
        ).fetchone()
        if payload["outcome"] == "allowed" and definition is None:
            raise EventTransitionError("final access has no prior factor definition")
        capability_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FinalTestCapabilityIssued'
            AND json_extract(payload, '$.capability_fingerprint') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["capability_fingerprint"],),
        ).fetchone()
        if payload["outcome"] == "allowed":
            if capability_row is None:
                raise EventTransitionError("allowed final access lacks an issued capability")
            capability = json.loads(str(capability_row["payload"]))
            if any(
                capability[field] != payload[field]
                for field in (
                    "candidate_hash",
                    "factor_spec_id",
                    "declared_run_id",
                )
            ):
                raise EventTransitionError("allowed final access does not match capability")
            prior_allowed = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'FinalTestAccessRecorded'
                AND json_extract(payload, '$.capability_fingerprint') = ?
                AND json_extract(payload, '$.outcome') = 'allowed'
                """,
                (payload["capability_fingerprint"],),
            ).fetchone()
            if prior_allowed is not None:
                raise EventTransitionError("final capability was already consumed")

    @staticmethod
    def _validate_final_artifact_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        access_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FinalTestAccessRecorded' AND event_hash = ?
            """,
            (payload["access_event_hash"],),
        ).fetchone()
        if access_row is None:
            raise EventTransitionError("final artifact lacks an audited access")
        access = json.loads(str(access_row["payload"]))
        if (
            access["outcome"] != "allowed"
            or access["candidate_hash"] != payload["candidate_hash"]
            or access["factor_spec_id"] != payload["factor_spec_id"]
        ):
            raise EventTransitionError("final artifact does not match allowed access")
        candidate_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FinalCandidateFrozen'
            AND json_extract(payload, '$.candidate_hash') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["candidate_hash"],),
        ).fetchone()
        if candidate_row is None:
            raise EventTransitionError("final artifact lacks its frozen candidate")
        candidate = json.loads(str(candidate_row["payload"]))
        if any(
            candidate[field] != payload[field]
            for field in (
                "definition_hash",
                "transform_pipeline_hash",
                "cost_model_hash",
                "regime_config_hash",
                "policy_hash",
                "data_snapshot_hash",
            )
        ):
            raise EventTransitionError("final artifact configuration differs from frozen candidate")
        prior = conn.execute(
            """
            SELECT 1 FROM research_events
            WHERE event_type = 'FinalTestArtifactRecorded'
            AND json_extract(payload, '$.candidate_hash') = ?
            """,
            (payload["candidate_hash"],),
        ).fetchone()
        if prior is not None:
            raise EventTransitionError("frozen candidate already has a final artifact")

    @staticmethod
    def _validate_forward_v3_transition(
        conn: sqlite3.Connection,
        event_type: str,
        run_id: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type == "ForwardMonitoringProviderV3Registered":
            descriptor = payload["descriptor"]
            prior = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = ? AND json_extract(payload, '$.descriptor.provider_id') = ? AND json_extract(payload, '$.descriptor.provider_version') = ?",
                (event_type, descriptor["provider_id"], descriptor["provider_version"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError("forward v3 provider is already registered")
            return
        if event_type == "ForwardPlanV3Recorded":
            final = conn.execute(
                "SELECT payload, created_at FROM research_events WHERE event_type = 'FinalTestArtifactV2Recorded' AND event_hash = ?",
                (payload["final_artifact_event_hash"],),
            ).fetchone()
            decision = conn.execute(
                "SELECT payload, created_at FROM research_events WHERE event_type IN ('QualityDecisionV2Recorded', 'QualityDecisionV3Recorded') AND event_hash = ?",
                (payload["decision_event_hash"],),
            ).fetchone()
            provider = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = 'ForwardMonitoringProviderV3Registered' AND event_hash = ?",
                (payload["provider_registration_event_hash"],),
            ).fetchone()
            config = payload["config"]
            if final is None or decision is None or provider is None:
                raise EventTransitionError("forward v3 plan authority sources are missing")
            final_payload = json.loads(str(final["payload"]))
            decision_payload = json.loads(str(decision["payload"]))
            provider_payload = json.loads(str(provider["payload"]))
            latest = conn.execute(
                "SELECT event_hash FROM research_events WHERE event_type IN ('QualityDecisionV2Recorded', 'QualityDecisionV3Recorded') AND json_extract(payload, '$.factor_spec_id') = ? ORDER BY seq DESC LIMIT 1",
                (config["factor_spec_id"],),
            ).fetchone()
            taint = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'FinalContaminationV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (payload["final_evaluation_key"],),
            ).fetchone()
            if (
                latest is None
                or latest["event_hash"] != payload["decision_event_hash"]
                or decision_payload["decision"] != "paper_candidate"
                or decision_payload["factor_spec_id"] != config["factor_spec_id"]
                or decision_payload["decision_hash"] != payload["decision_hash"]
                or final_payload["factor_spec_id"] != config["factor_spec_id"]
                or final_payload["artifact_hash"] != payload["final_artifact_hash"]
                or final_payload["final_evaluation_key"] != payload["final_evaluation_key"]
                or final_payload["contaminated"]
                or not final_payload["quality_passed"]
                or provider_payload["descriptor"]["provider_id"] != config["provider_id"]
                or provider_payload["descriptor"]["provider_version"] != config["provider_version"]
                or taint is not None
            ):
                raise EventTransitionError("forward v3 plan eligibility is not current and authoritative")
            return
        plan = conn.execute(
            "SELECT run_id, payload FROM research_events WHERE event_type = 'ForwardPlanV3Recorded' AND event_hash = ?",
            (payload["plan_event_hash"],),
        ).fetchone()
        if plan is None or str(plan["run_id"]) != run_id:
            raise EventTransitionError("forward v3 event lacks its exact plan")
        plan_payload = json.loads(str(plan["payload"]))
        if plan_payload["plan_hash"] != payload["plan_hash"]:
            raise EventTransitionError("forward v3 plan binding differs")
        if event_type == "ForwardSourceArtifactV3Recorded":
            prior = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = ? AND json_extract(payload, '$.plan_hash') = ? ORDER BY seq DESC LIMIT 1",
                (event_type, payload["plan_hash"]),
            ).fetchone()
            if (
                payload["plan_id"] != plan_payload["plan_id"]
                or payload["provider_registration_event_hash"] != plan_payload["provider_registration_event_hash"]
                or payload["provider_id"] != plan_payload["config"]["provider_id"]
                or payload["provider_version"] != plan_payload["config"]["provider_version"]
                or payload["vintage_policy_hash"] != plan_payload["config"]["vintage_policy_hash"]
                or payload["availability_contract_hash"] != plan_payload["config"]["availability_contract_hash"]
                or payload["period_start"] < plan_payload["config"]["forward_start"]
                or (prior is not None and payload["period_start"] <= json.loads(str(prior["payload"]))["period_end"])
            ):
                raise EventTransitionError("forward v3 source scope or period differs")
            return
        if event_type == "ForwardObservationV3Recorded":
            source = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'ForwardSourceArtifactV3Recorded' AND event_hash = ?",
                (payload["source_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = ? AND json_extract(payload, '$.plan_hash') = ? ORDER BY seq DESC LIMIT 1",
                (event_type, payload["plan_hash"]),
            ).fetchone()
            taint = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'FinalContaminationV2Recorded' AND json_extract(payload, '$.final_evaluation_key') = ?",
                (plan_payload["final_evaluation_key"],),
            ).fetchone()
            if source is None or str(source["run_id"]) != run_id:
                raise EventTransitionError("forward v3 observation lacks its source")
            source_payload = json.loads(str(source["payload"]))
            expected_previous = None if prior is None else json.loads(str(prior["payload"]))["observation_hash"]
            if (
                source_payload["plan_event_hash"] != payload["plan_event_hash"]
                or source_payload["source_hash"] != payload["source_hash"]
                or source_payload["period_start"] != payload["period_start"]
                or source_payload["period_end"] != payload["period_end"]
                or payload["previous_observation_hash"] != expected_previous
                or taint is not None
            ):
                raise EventTransitionError("forward v3 observation authority differs")
            return
        if event_type == "DataRevisionRecorded":
            original = conn.execute(
                "SELECT run_id, payload FROM research_events WHERE event_type = 'ForwardSourceArtifactV3Recorded' AND event_hash = ?",
                (payload["original_source_event_hash"],),
            ).fetchone()
            prior = conn.execute(
                "SELECT payload FROM research_events WHERE event_type = ? AND json_extract(payload, '$.original_source_event_hash') = ? ORDER BY seq DESC LIMIT 1",
                (event_type, payload["original_source_event_hash"]),
            ).fetchone()
            if original is None or str(original["run_id"]) != run_id:
                raise EventTransitionError("forward revision lacks its original source")
            original_payload = json.loads(str(original["payload"]))
            expected_number = 1 if prior is None else int(json.loads(str(prior["payload"]))["revision_number"]) + 1
            if (
                original_payload["plan_event_hash"] != payload["plan_event_hash"]
                or original_payload["source_hash"] != payload["original_source_hash"]
                or payload["revision_number"] != expected_number
                or payload["revised_source_hash"] == payload["original_source_hash"]
            ):
                raise EventTransitionError("forward revision authority differs")
            return

    @staticmethod
    def _validate_forward_plan_v2_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        artifact_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FinalTestArtifactRecorded'
            AND json_extract(payload, '$.artifact_hash') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["final_test_artifact_hash"],),
        ).fetchone()
        if artifact_row is None:
            raise EventTransitionError("forward plan lacks a prior final-test artifact")
        artifact = json.loads(str(artifact_row["payload"]))
        if (
            artifact["factor_spec_id"] != payload["factor_spec_id"]
            or not artifact["quality_passed"]
            or artifact["contaminated"]
            or any(
                artifact[field] != payload[field]
                for field in (
                    "definition_hash",
                    "transform_pipeline_hash",
                    "cost_model_hash",
                    "regime_config_hash",
                    "policy_hash",
                )
            )
        ):
            raise EventTransitionError("forward plan requires qualified uncontaminated final evidence")

    @staticmethod
    def _validate_forward_observation_v2_transition(conn: sqlite3.Connection, payload: Mapping[str, Any]) -> None:
        plan_row = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ForwardPlanV2Recorded' AND entity_id = ?",
            (payload["plan_id"],),
        ).fetchone()
        if plan_row is None:
            raise EventTransitionError("forward observation has no prior frozen plan")
        plan = json.loads(str(plan_row["payload"]))
        if plan["plan_hash"] != payload["plan_hash"]:
            raise EventTransitionError("forward observation plan hash mismatch")
        last_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'ForwardObservationV2Recorded'
            AND json_extract(payload, '$.plan_id') = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["plan_id"],),
        ).fetchone()
        if last_row is None:
            if payload["previous_observation_hash"] is not None:
                raise EventTransitionError("first forward observation cannot have a previous hash")
            return
        previous = json.loads(str(last_row["payload"]))
        if (
            payload["period_start"] <= previous["period_end"]
            or payload["previous_observation_hash"] != previous["observation_hash"]
        ):
            raise EventTransitionError("forward observations must append in ordered hash chain")

    @staticmethod
    def _event_payloads(
        conn: sqlite3.Connection,
        event_type: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        rows = conn.execute(
            "SELECT event_hash, payload FROM research_events WHERE event_type = ? ORDER BY seq ASC",
            (event_type,),
        ).fetchall()
        return [(str(row["event_hash"]), json.loads(str(row["payload"]))) for row in rows]

    def _validate_sequential_protocol_transition(
        self,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        contract_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'FalsificationContractRegistered' AND entity_id = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["contract_id"],),
        ).fetchone()
        if contract_row is None:
            raise EventTransitionError("sequential protocol has no prior falsification contract")
        contract = json.loads(str(contract_row["payload"]))
        if (
            contract["contract_hash"] != payload["contract_hash"]
            or contract["factor_spec_id"] != payload["factor_spec_id"]
            or contract["policy_hash"] != payload["policy_hash"]
        ):
            raise EventTransitionError("sequential protocol does not match its frozen contract")
        for _, access in self._event_payloads(conn, "OutcomeDataAccessed"):
            if access["factor_spec_id"] == payload["factor_spec_id"]:
                raise EventTransitionError("sequential protocol must precede outcome-data access")
        for _, result in self._event_payloads(conn, "FalsificationResultRecorded"):
            if result["contract_id"] == payload["contract_id"]:
                raise EventTransitionError("sequential protocol must precede falsification results")
        for _, existing in self._event_payloads(conn, "SequentialProtocolRegistered"):
            if existing["protocol_id"] == payload["protocol_id"]:
                raise EventTransitionError("sequential protocol ID is already registered")
            if existing["contract_id"] == payload["contract_id"]:
                raise EventTransitionError("falsification contract already has a sequential protocol")

    def _validate_sequential_look_transition(
        self,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        protocol_row = conn.execute(
            """
            SELECT payload FROM research_events
            WHERE event_type = 'SequentialProtocolRegistered' AND entity_id = ?
            ORDER BY seq DESC LIMIT 1
            """,
            (payload["protocol_id"],),
        ).fetchone()
        if protocol_row is None:
            raise EventTransitionError("sequential look has no prior registered protocol")
        protocol = json.loads(str(protocol_row["payload"]))
        for field in (
            "protocol_hash",
            "factor_spec_id",
            "support_log_boundary",
            "contradiction_log_boundary",
        ):
            if payload[field] != protocol[field]:
                raise EventTransitionError(f"sequential look does not match protocol {field}")
        self._validate_sequential_mixture_evidence(protocol, payload)

        prior = [
            (event_hash, look)
            for event_hash, look in self._event_payloads(conn, "SequentialLookRecorded")
            if look["protocol_id"] == payload["protocol_id"]
        ]
        if any(look["look_id"] == payload["look_id"] for _, look in prior):
            raise EventTransitionError("sequential look ID is already recorded")
        maximum_looks = int(protocol["maximum_looks"])
        look_index = int(payload["look_index"])
        if look_index > maximum_looks:
            raise EventTransitionError("sequential look exceeds frozen maximum_looks")

        used_block_ids = {str(look["block_id"]) for _, look in prior}
        used_block_hashes = {str(look["block_hash"]) for _, look in prior}
        used_unit_hashes = {str(unit_hash) for _, look in prior for unit_hash in look["unit_hashes"]}
        if payload["block_id"] in used_block_ids or payload["block_hash"] in used_block_hashes:
            raise EventTransitionError("sequential look cannot reuse an observed block")
        if used_unit_hashes.intersection(str(item) for item in payload["unit_hashes"]):
            raise EventTransitionError("sequential look cannot reuse an observed unit")

        if not prior:
            if look_index != 1 or payload["previous_look_event_hash"] is not None:
                raise EventTransitionError("first sequential look must start at index one")
            if payload["information_time"] != payload["incremental_information"]:
                raise EventTransitionError("first information time must equal incremental information")
        else:
            previous_hash, previous = prior[-1]
            if previous["status"] != "continue":
                raise EventTransitionError("sequential protocol is already stopped")
            if look_index != int(previous["look_index"]) + 1:
                raise EventTransitionError("sequential look index must be contiguous")
            if payload["previous_look_event_hash"] != previous_hash:
                raise EventTransitionError("sequential look previous hash does not match")
            expected_information = int(previous["information_time"]) + int(payload["incremental_information"])
            if payload["information_time"] != expected_information:
                raise EventTransitionError("sequential information time must advance cumulatively")

        if payload["status"] == "continue" and look_index == maximum_looks:
            raise EventTransitionError("final permitted look must stop at maximum_looks")
        if payload["status"] == "max_looks_reached" and look_index != maximum_looks:
            raise EventTransitionError("max-look stop is valid only at maximum_looks")

    def _require_terminal_sequential_look_if_applicable(
        self,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        protocols = [
            protocol
            for _, protocol in self._event_payloads(conn, "SequentialProtocolRegistered")
            if protocol["contract_id"] == payload["contract_id"]
        ]
        if not protocols:
            return
        protocol = protocols[-1]
        looks = [
            look
            for _, look in self._event_payloads(conn, "SequentialLookRecorded")
            if look["protocol_id"] == protocol["protocol_id"]
        ]
        if not looks or looks[-1]["status"] == "continue":
            raise EventTransitionError("sequential result requires a terminal sequential look")

    def _validate_mechanism_evidence_index_transition(
        self,
        conn: sqlite3.Connection,
        payload: Mapping[str, Any],
    ) -> None:
        source_hashes = tuple(str(item) for item in payload["source_event_hashes"])
        referenced_result_hashes = {str(item) for item in payload["source_result_hashes"]}
        available_result_hashes: set[str] = set()
        source_outcomes: dict[str, str] = {}
        for source_hash in source_hashes:
            result_row = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'FalsificationResultRecorded' AND event_hash = ?
                """,
                (source_hash,),
            ).fetchone()
            if result_row is None:
                raise EventTransitionError("MEI references no prior falsification result")
            result = json.loads(str(result_row["payload"]))
            source_outcomes[source_hash] = str(result["outcome"])
            available_result_hashes.update(str(reference["artifact_hash"]) for reference in result["artifact_refs"])
            contract_row = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'FalsificationContractRegistered' AND entity_id = ?
                ORDER BY seq DESC LIMIT 1
                """,
                (result["contract_id"],),
            ).fetchone()
            if contract_row is None:
                raise EventTransitionError("MEI source result has no registered contract")
            contract = json.loads(str(contract_row["payload"]))
            if contract["factor_spec_id"] != payload["factor_spec_id"]:
                raise EventTransitionError("MEI source results must belong to one factor")
            if contract["policy_hash"] != payload["policy_hash"]:
                raise EventTransitionError("MEI source results must use one frozen policy")
        if not referenced_result_hashes.issubset(available_result_hashes):
            raise EventTransitionError("MEI source result hashes lack prior artifact evidence")
        decisive = {str(item) for item in payload["decisive_event_hashes"]}
        expected_state = self._mechanism_ordinal_state(source_outcomes, decisive)
        if payload["ordinal_state"] != expected_state:
            raise EventTransitionError("MEI ordinal state does not match deterministic truth table")
        content = self._mechanism_index_content(payload)
        if canonical_json_hash(content) != payload["mei_hash"]:
            raise EventTransitionError("MEI hash does not match deterministic ordinal content")

    @staticmethod
    def _validate_sequential_mixture_evidence(protocol: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
        weights = tuple(float(value) for value in protocol["mixture_weights"])
        support = tuple(float(value) for value in payload["support_component_log_capitals"])
        contradiction = tuple(float(value) for value in payload["contradiction_component_log_capitals"])
        if len(support) != len(weights) or len(contradiction) != len(weights):
            raise EventTransitionError("sequential component state does not match frozen mixture")

        def mixture_log(components: tuple[float, ...]) -> float:
            terms = tuple(math.log(weight) + value for weight, value in zip(weights, components, strict=True))
            maximum = max(terms)
            return maximum + math.log(math.fsum(math.exp(value - maximum) for value in terms))

        expected_support = mixture_log(support)
        expected_contradiction = mixture_log(contradiction)
        if not math.isclose(
            float(payload["cumulative_support_log_e"]),
            expected_support,
            rel_tol=0.0,
            abs_tol=1e-12,
        ) or not math.isclose(
            float(payload["cumulative_contradiction_log_e"]),
            expected_contradiction,
            rel_tol=0.0,
            abs_tol=1e-12,
        ):
            raise EventTransitionError("sequential cumulative log-e does not match component state")

    @staticmethod
    def _mechanism_ordinal_state(source_outcomes: Mapping[str, str], decisive_event_hashes: set[str]) -> str:
        decisive_outcomes = [
            outcome for event_hash, outcome in source_outcomes.items() if event_hash in decisive_event_hashes
        ]
        if any(outcome == "falsified" for outcome in decisive_outcomes):
            return "falsified"
        if not decisive_outcomes or any(outcome != "supported" for outcome in decisive_outcomes):
            return "inconclusive"
        if all(outcome == "supported" for outcome in source_outcomes.values()):
            return "supported"
        return "partial_support"

    @staticmethod
    def _mechanism_index_content(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "schema_version": payload["mei_schema_version"],
            "truth_table_version": payload["truth_table_version"],
            "factor_spec_id": payload["factor_spec_id"],
            "ordinal_state": payload["ordinal_state"],
            "policy_version": payload["policy_version"],
            "policy_hash": payload["policy_hash"],
            "source_result_hashes": list(payload["source_result_hashes"]),
            "source_event_hashes": list(payload["source_event_hashes"]),
            "decisive_event_hashes": list(payload["decisive_event_hashes"]),
            "advisory_event_hashes": list(payload["advisory_event_hashes"]),
            "reason_codes": list(payload["reason_codes"]),
            "warning_codes": list(payload["warning_codes"]),
            "limitation_codes": list(payload["limitation_codes"]),
        }

    def _tail_hash(self, conn: sqlite3.Connection) -> str | None:
        row = conn.execute("SELECT event_hash FROM research_events ORDER BY seq DESC LIMIT 1").fetchone()
        return None if row is None else str(row["event_hash"])

    @staticmethod
    def _rollback_quietly(conn: sqlite3.Connection) -> None:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass

    @staticmethod
    def _is_retryable_lock(exc: sqlite3.OperationalError) -> bool:
        message = str(exc).lower()
        return "locked" in message or "busy" in message

    @staticmethod
    def _retry_delay(attempt: int) -> None:
        ceiling = min(0.5, 0.005 * (2**attempt))
        time.sleep(ceiling + random.uniform(0.0, ceiling / 4.0))

    def query_events(
        self,
        *,
        event_type: str | None = None,
        entity_id: str | None = None,
    ) -> list[ResearchEventEnvelope]:
        clauses: list[str] = []
        params: list[str] = []
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        if entity_id is not None:
            clauses.append("entity_id = ?")
            params.append(entity_id)
        sql = "SELECT * FROM research_events"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq ASC"
        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [self._row_to_event(row) for row in rows]

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> ResearchEventEnvelope:
        return ResearchEventEnvelope(
            schema_version=cast(Literal["research_event.v1"], str(row["schema_version"])),
            event_id=str(row["event_id"]),
            event_type=str(row["event_type"]),
            entity_id=str(row["entity_id"]),
            run_id=str(row["run_id"]),
            payload_schema_version=str(row["payload_schema_version"]),
            payload=json.loads(row["payload"]),
            payload_hash=str(row["payload_hash"]),
            idempotency_key=(None if row["idempotency_key"] is None else str(row["idempotency_key"])),
            previous_event_hash=(None if row["previous_event_hash"] is None else str(row["previous_event_hash"])),
            event_hash=str(row["event_hash"]),
            created_at=str(row["created_at"]),
            code_version=str(row["code_version"]),
            feature_flags=json.loads(row["feature_flags"]),
            warnings=tuple(json.loads(row["warnings"])),
            hard_failures=tuple(json.loads(row["hard_failures"])),
        )

    def verify_chain(self) -> bool:
        try:
            events = self.query_events()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return False
        return self._verify_events(events)

    def _verify_events(self, events: list[ResearchEventEnvelope]) -> bool:
        previous: str | None = None
        for event in events:
            try:
                if event.schema_version != "research_event.v1":
                    return False
                if UUID(event.event_id).version != 4:
                    return False
                created_at = event.created_at[:-1] + "+00:00" if event.created_at.endswith("Z") else event.created_at
                if datetime.fromisoformat(created_at).tzinfo is None:
                    return False
                if event.previous_event_hash != previous:
                    return False
                event_dict = event.to_dict()
                validated_payload = validate_and_redact_payload(
                    event.event_type,
                    event.payload_schema_version,
                    event_dict["payload"],
                )
                self._validate_artifacts(validated_payload)
                self._validate_external_retriever_action_template(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_train_valid_snapshot(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_feature_source(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_v4_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_v5_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_v6_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_retriever_v7_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_quality_decision_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_decision_evidence_v3(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_evaluation_policy(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_resolved_evaluation_contract(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_applicability_assessment(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_scorecard_evidence_v3(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_snapshot_evidence_v3(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_pit_adapter_registration(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_pit_snapshot(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_predictive_v4(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_execution_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_secondary_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_claim_decision(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_research_dossier(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_production_evaluator(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_activation_source_audit(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_official_control_evidence(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_prearm_flat_schedule(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_pair_execution_schedule(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_activation_resource(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_activation_resource_v2(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_generation_consumption(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_generation_consumption_v2(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_generation_consumption_v3(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_generation_consumption_v4(
                    event.event_type,
                    validated_payload,
                )
                if validated_payload != event_dict["payload"]:
                    return False
                self._validate_draft_identity(
                    EventDraft(
                        event_type=event.event_type,
                        entity_id=event.entity_id,
                        run_id=event.run_id,
                        payload_schema_version=event.payload_schema_version,
                        payload=validated_payload,
                        idempotency_key=event.idempotency_key,
                    )
                )
                self._validate_payload_entity(
                    EventDraft(
                        event_type=event.event_type,
                        entity_id=event.entity_id,
                        run_id=event.run_id,
                        payload_schema_version=event.payload_schema_version,
                        payload=validated_payload,
                        idempotency_key=event.idempotency_key,
                    ),
                    validated_payload,
                )
                self._validate_factor_definition_identity(event.event_type, validated_payload)
                self._validate_registry_bootstrap_identity(event.event_type, validated_payload)
                stored_flags = dict(event.feature_flags)
                if not is_valid_ags_flag_snapshot(stored_flags):
                    return False
                self._reject_secret_or_path(event.code_version, "code_version")
                expected_warnings, expected_hard_failures = envelope_diagnostics(
                    event.event_type,
                    validated_payload,
                    reduced_durability="REDUCED_DURABILITY" in event.warnings,
                )
                if event.warnings != expected_warnings or event.hard_failures != expected_hard_failures:
                    return False
                if canonical_json_hash(validated_payload) != event.payload_hash:
                    return False
                without_hash = event_dict
                without_hash.pop("event_hash")
                if canonical_json_hash(without_hash) != event.event_hash:
                    return False
            except (EventValidationError, ValueError, TypeError, KeyError, AttributeError):
                return False
            previous = event.event_hash
        return self._verify_references_and_lifecycle(events)

    @staticmethod
    def _verify_references_and_lifecycle(events: list[ResearchEventEnvelope]) -> bool:
        started: dict[str, str] = {}
        terminated: set[str] = set()
        evaluations: dict[str, tuple[str, str]] = {}
        evaluation_payloads: dict[str, Mapping[str, Any]] = {}
        terminal_hashes: set[str] = set()
        terminal_payloads: dict[str, Mapping[str, Any]] = {}
        definitions: dict[str, Mapping[str, Any]] = {}
        definition_event_hashes: dict[str, str] = {}
        contracts: dict[str, Mapping[str, Any]] = {}
        accessed_factors: set[str] = set()
        protocols: dict[str, Mapping[str, Any]] = {}
        protocol_by_contract: dict[str, str] = {}
        looks_by_protocol: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
        result_factors: dict[str, str] = {}
        result_policies: dict[str, str] = {}
        result_outcomes: dict[str, str] = {}
        result_artifact_hashes: dict[str, set[str]] = {}
        result_contracts: set[str] = set()
        plans: set[str] = set()
        final_candidates: dict[str, Mapping[str, Any]] = {}
        final_capabilities: dict[str, Mapping[str, Any]] = {}
        allowed_final_tokens: set[str] = set()
        final_accesses: dict[str, Mapping[str, Any]] = {}
        final_artifacts: dict[str, Mapping[str, Any]] = {}
        forward_v2_plans: dict[str, Mapping[str, Any]] = {}
        forward_v2_observations: dict[str, list[Mapping[str, Any]]] = {}
        activation_plans: dict[str, Mapping[str, Any]] = {}
        activation_experiments: set[str] = set()
        prearm_schedule_ids: set[str] = set()
        prearm_schedule_hashes: set[str] = set()
        prearm_schedule_events: dict[str, ResearchEventEnvelope] = {}
        pair_schedule_events: dict[str, ResearchEventEnvelope] = {}
        claimed_pair_schedule_hashes: set[str] = set()
        pair_claim_events: dict[str, ResearchEventEnvelope] = {}
        activation_runs: dict[str, list[Mapping[str, Any]]] = {}
        activation_manifest_ids: set[str] = set()
        activation_manifest_hashes: set[str] = set()
        activation_source_audit_ids: set[str] = set()
        activation_resource_ids: set[str] = set()
        activation_resource_evidence_hashes: set[str] = set()
        activation_resource_manifest_hashes: set[str] = set()
        activation_generation_ids: set[str] = set()
        activation_generation_hashes: set[str] = set()
        official_control_ids: set[str] = set()
        retriever_action_ids: set[str] = set()
        retriever_action_events: dict[str, ResearchEventEnvelope] = {}
        train_valid_snapshot_ids: set[str] = set()
        train_valid_snapshot_hashes: set[str] = set()
        train_valid_snapshot_events: dict[str, ResearchEventEnvelope] = {}
        retriever_feature_source_ids: set[str] = set()
        retriever_feature_source_hashes: set[str] = set()
        retriever_feature_source_events: dict[str, ResearchEventEnvelope] = {}
        retriever_v5_ids: set[str] = set()
        retriever_v5_events: dict[str, ResearchEventEnvelope] = {}
        retriever_v6_ids: set[str] = set()
        retriever_v6_events: dict[str, ResearchEventEnvelope] = {}
        retriever_v7_ids: set[str] = set()
        retriever_v7_events: dict[str, ResearchEventEnvelope] = {}
        process_outcome_links: list[tuple[str, str, str]] = []
        retriever_v4_events: dict[str, ResearchEventEnvelope] = {}
        activation_results: dict[str, Mapping[str, Any]] = {}
        activation_result_plans: set[str] = set()
        activation_decision_plans: set[str] = set()
        decision_evidence_keys: set[tuple[str, str, str]] = set()
        scorecard_evidence_keys: set[tuple[str, str, str]] = set()
        snapshot_evidence_keys: set[tuple[str, str]] = set()
        evaluation_policy_runs: set[str] = set()
        resolved_contract_runs: set[str] = set()
        resolved_contract_events: dict[str, ResearchEventEnvelope] = {}
        applicability_keys: set[tuple[str, str, str]] = set()
        pit_adapter_ids: set[str] = set()
        pit_adapter_registration_hashes: set[str] = set()
        pit_adapter_events: dict[str, ResearchEventEnvelope] = {}
        pit_snapshot_runs: set[str] = set()
        factor_output_keys: set[tuple[str, str]] = set()
        factor_output_events: dict[str, ResearchEventEnvelope] = {}
        observed_predictive_keys: set[tuple[str, str]] = set()
        pit_predictive_keys: set[tuple[str, str]] = set()
        predictive_events: dict[str, ResearchEventEnvelope] = {}
        scorecard_v4_keys: set[tuple[str, str]] = set()
        execution_evidence_keys: set[tuple[str, str]] = set()
        comparison_pool_hashes: set[str] = set()
        comparison_pool_events: dict[str, ResearchEventEnvelope] = {}
        secondary_evidence_keys: set[tuple[str, str]] = set()
        selection_assessment_runs: set[str] = set()
        selection_assessment_events: dict[str, ResearchEventEnvelope] = {}
        claim_matrix_keys: set[tuple[str, str]] = set()
        claim_matrix_events: dict[str, ResearchEventEnvelope] = {}
        quality_decision_v4_keys: set[tuple[str, str]] = set()
        research_dossier_keys: set[tuple[str, str]] = set()
        experiment_run_report_runs: set[str] = set()
        production_node_keys: set[tuple[str, str]] = set()
        production_node_events: set[str] = set()
        terminal_dossier_trials: set[str] = set()
        report_failure_trials: set[str] = set()
        seen_run_ids: set[str] = set()
        event_order = {event.event_hash: index for index, event in enumerate(events)}
        events_by_hash = {event.event_hash: event for event in events}
        for event in events:
            payload = event.payload
            run_seen_before = event.run_id in seen_run_ids
            seen_run_ids.add(event.run_id)
            if event.event_type == "RetrieverDecisionV4Recorded":
                retriever_v4_events[event.event_hash] = event
            if event.event_type == "FactorDefinitionRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                definitions[factor_spec_id] = payload
                definition_event_hashes[factor_spec_id] = event.event_hash
            elif event.event_type == "TrialStarted":
                trial_id = str(payload["trial_id"])
                if trial_id in started or trial_id in terminated:
                    return False
                started[trial_id] = event.run_id
            elif event.event_type == "ActivationPairExecutionScheduled":
                schedule_hash = str(payload["schedule_hash"])
                if (
                    event.run_id != payload["run_group_id"]
                    or event.event_hash in pair_schedule_events
                    or any(
                        prior.payload["schedule_hash"] == schedule_hash
                        or (
                            prior.payload["plan_hash"] == payload["plan_hash"]
                            and prior.payload["pair_id"] == payload["pair_id"]
                        )
                        for prior in pair_schedule_events.values()
                    )
                ):
                    return False
                pair_schedule_events[event.event_hash] = event
            elif event.event_type == "ActivationPairExecutionClaimed":
                from src.alpha_foundry.activation.runner import (
                    activation_arm_execution_run_id,
                )

                schedule = pair_schedule_events.get(str(payload["schedule_event_hash"]))
                schedule_hash = str(payload["schedule_hash"])
                arm_run_ids = {
                    activation_arm_execution_run_id(
                        plan_hash=str(payload["plan_hash"]),
                        run_group_id=str(payload["run_group_id"]),
                        arm=arm,
                    )
                    for arm in ("control", "treatment")
                }
                if (
                    schedule is None
                    or schedule_hash in claimed_pair_schedule_hashes
                    or event.run_id != payload["run_group_id"]
                    or schedule.run_id != event.run_id
                    or schedule.payload["schedule_hash"] != schedule_hash
                    or schedule.payload["plan_hash"] != payload["plan_hash"]
                    or schedule.payload["pair_id"] != payload["pair_id"]
                    or schedule.payload["run_group_id"] != payload["run_group_id"]
                    or any(run_id in arm_run_ids for run_id in started.values())
                ):
                    return False
                claimed_pair_schedule_hashes.add(schedule_hash)
                pair_claim_events[event.event_hash] = event
            elif event.event_type == "OfficialSearchControlRecorded":
                control_id = str(payload["control_id"])
                if control_id in official_control_ids:
                    return False
                official_control_ids.add(control_id)
            elif event.event_type == "GenerationFailureRecorded":
                trial_id = str(payload["trial_id"])
                if started.get(trial_id) != event.run_id or trial_id in terminated:
                    return False
            elif event.event_type == "EvaluationRecorded":
                trial_id = str(payload["trial_id"])
                if started.get(trial_id) != event.run_id or trial_id in terminated:
                    return False
                evaluations[event.event_hash] = (trial_id, event.run_id)
                evaluation_payloads[event.event_hash] = payload
            elif event.event_type == "TrialTerminated":
                trial_id = str(payload["trial_id"])
                if started.get(trial_id) != event.run_id or trial_id in terminated:
                    return False
                if payload["status"] == "success":
                    evaluation_hash = payload["evaluation_event_hash"]
                    if evaluations.get(str(evaluation_hash)) != (
                        trial_id,
                        event.run_id,
                    ):
                        return False
                terminated.add(trial_id)
                terminal_hashes.add(event.event_hash)
                terminal_payloads[event.event_hash] = payload
            elif event.event_type == "ProcessOutcomeRecordedV2":
                process_outcome_links.append(
                    (
                        event.event_hash,
                        str(payload["terminal_event_hash"]),
                        str(payload["evaluation_event_hash"]),
                    )
                )
            elif event.event_type == "RetrieverActionTemplateFrozen":
                action_id = str(payload["action_id"])
                watermark_hash = str(payload["eligible_event_watermark"])
                watermark_index = event_order.get(watermark_hash, -1)
                current_index = event_order[event.event_hash]
                factor_id = str(payload["parent_factor_spec_id"])
                definition_hash = definition_event_hashes.get(factor_id)
                if (
                    action_id in retriever_action_ids
                    or event.run_id != payload["execution_run_id"]
                    or watermark_index < 0
                    or watermark_index >= current_index
                    or definition_hash != payload["parent_definition_event_hash"]
                    or event_order.get(str(definition_hash), current_index) > watermark_index
                ):
                    return False
                eligible = False
                for evaluation_hash, candidate_evaluation in evaluation_payloads.items():
                    if (
                        event_order[evaluation_hash] > watermark_index
                        or candidate_evaluation["factor_spec_id"] != factor_id
                        or candidate_evaluation["data_scope"] not in {"valid", "train_valid"}
                    ):
                        continue
                    for terminal_hash, candidate_terminal in terminal_payloads.items():
                        if (
                            event_order[terminal_hash] > watermark_index
                            or candidate_terminal["trial_id"] != candidate_evaluation["trial_id"]
                            or candidate_terminal["status"] not in {"success", "reject"}
                        ):
                            continue
                        directly_cited = candidate_terminal["evaluation_event_hash"] == evaluation_hash
                        outcome_cited = any(
                            event_order[outcome_hash] <= watermark_index
                            and cited_terminal == terminal_hash
                            and cited_evaluation == evaluation_hash
                            for outcome_hash, cited_terminal, cited_evaluation in process_outcome_links
                        )
                        if directly_cited or outcome_cited:
                            eligible = True
                            break
                    if eligible:
                        break
                if not eligible:
                    return False
                retriever_action_ids.add(action_id)
                retriever_action_events[event.event_hash] = event
            elif event.event_type == "EvaluationPolicyRegistered":
                from src.alpha_quality.evaluation_registry_v1 import (
                    EvaluationPolicyRegistryServiceV1,
                )

                expected_id = EvaluationPolicyRegistryServiceV1.registration_id(
                    event.run_id,
                    str(payload["bundle_hash"]),
                )
                if (
                    run_seen_before
                    or event.run_id in evaluation_policy_runs
                    or event.entity_id != expected_id
                    or event.previous_event_hash != payload["preregistration_watermark"]
                ):
                    return False
                evaluation_policy_runs.add(event.run_id)
            elif event.event_type == "ResolvedEvaluationContractRegistered":
                policy = events_by_hash.get(str(payload["evaluation_policy_event_hash"]))
                prior_run_events = [
                    prior for prior in events[: event_order[event.event_hash]] if prior.run_id == event.run_id
                ]
                if (
                    event.run_id in resolved_contract_runs
                    or policy is None
                    or policy.event_type != "EvaluationPolicyRegistered"
                    or policy.run_id != event.run_id
                    or len(prior_run_events) != 1
                    or prior_run_events[0].event_hash != policy.event_hash
                    or policy.payload["bundle_hash"] != payload["evaluation_policy_bundle_hash"]
                    or payload["preregistration_watermark"] != policy.event_hash
                ):
                    return False
                resolved_contract_runs.add(event.run_id)
                resolved_contract_events[str(payload["contract_hash"])] = event
            elif event.event_type == "ApplicabilityAssessmentRecorded":
                contract = resolved_contract_events.get(str(payload["resolved_contract_hash"]))
                definition = events_by_hash.get(str(payload["factor_definition_event_hash"]))
                key = (
                    str(payload["resolved_contract_hash"]),
                    str(payload["factor_spec_id"]),
                    str(payload["claim_type"]),
                )
                if (
                    key in applicability_keys
                    or contract is None
                    or definition is None
                    or definition.event_type != "FactorDefinitionRecorded"
                    or contract.run_id != event.run_id
                    or definition.run_id != event.run_id
                    or contract.event_hash not in payload["source_event_hashes"]
                    or definition.event_hash not in payload["source_event_hashes"]
                    or definition.payload["factor_spec_id"] != payload["factor_spec_id"]
                ):
                    return False
                applicability_keys.add(key)
            elif event.event_type == "AsharePITAdapterRegistered":
                adapter_id = str(payload["adapter_id"])
                registration_hash = str(payload["registration_hash"])
                if (
                    adapter_id in pit_adapter_ids
                    or registration_hash in pit_adapter_registration_hashes
                    or event.previous_event_hash != payload["source_watermark_event_hash"]
                ):
                    return False
                pit_adapter_ids.add(adapter_id)
                pit_adapter_registration_hashes.add(registration_hash)
                pit_adapter_events[event.event_hash] = event
            elif event.event_type == "AsharePITSnapshotRecorded":
                registration = pit_adapter_events.get(str(payload["adapter_registration_event_hash"]))
                policy = events_by_hash.get(str(payload["evaluation_policy_event_hash"]))
                if (
                    event.run_id in pit_snapshot_runs
                    or registration is None
                    or policy is None
                    or policy.event_type != "EvaluationPolicyRegistered"
                    or policy.run_id != event.run_id
                    or event.previous_event_hash != payload["source_watermark_event_hash"]
                    or registration.payload["adapter_id"] != payload["adapter_id"]
                    or registration.payload["registration_hash"] != payload["registration_hash"]
                    or any(run == event.run_id for run in started.values())
                ):
                    return False
                pit_snapshot_runs.add(event.run_id)
            elif event.event_type == "FactorOutputRecordedV3":
                contract = events_by_hash.get(str(payload["contract_event_hash"]))
                snapshot = events_by_hash.get(str(payload["pit_snapshot_event_hash"]))
                definitions_for_factor = [
                    candidate
                    for candidate in events[: event_order[event.event_hash]]
                    if candidate.event_type == "FactorDefinitionRecorded"
                    and candidate.entity_id == payload["factor_spec_id"]
                ]
                factor_output_key = (event.run_id, str(payload["factor_spec_id"]))
                if (
                    factor_output_key in factor_output_keys
                    or contract is None
                    or contract.event_type != "ResolvedEvaluationContractRegistered"
                    or snapshot is None
                    or snapshot.event_type != "AsharePITSnapshotRecorded"
                    or len(definitions_for_factor) != 1
                    or contract.run_id != event.run_id
                    or snapshot.run_id != event.run_id
                    or definitions_for_factor[0].run_id != event.run_id
                    or contract.payload["contract_hash"] != payload["resolved_contract_hash"]
                    or snapshot.payload["snapshot_hash"] != payload["pit_snapshot_hash"]
                    or definitions_for_factor[0].event_hash not in payload["source_event_hashes"]
                ):
                    return False
                factor_output_keys.add(factor_output_key)
                factor_output_events[event.event_hash] = event
            elif event.event_type in {
                "ObservedPanelPredictiveEvidenceRecorded",
                "PITPredictiveEvidenceRecorded",
            }:
                factor = factor_output_events.get(str(payload["factor_output_event_hash"]))
                predictive_key = (event.run_id, str(payload["factor_spec_id"]))
                keys = (
                    observed_predictive_keys
                    if event.event_type == "ObservedPanelPredictiveEvidenceRecorded"
                    else pit_predictive_keys
                )
                if (
                    factor is None
                    or predictive_key in keys
                    or factor.run_id != event.run_id
                    or factor.payload["factor_output_hash"] != payload["factor_output_hash"]
                    or factor.payload["factor_spec_id"] != payload["factor_spec_id"]
                    or factor.payload["resolved_contract_hash"] != payload["resolved_contract_hash"]
                ):
                    return False
                keys.add(predictive_key)
                predictive_events[event.event_hash] = event
            elif event.event_type == "ScorecardDecisionEvidenceV4Recorded":
                factor = factor_output_events.get(str(payload["factor_output_event_hash"]))
                observed = predictive_events.get(str(payload["observed_event_hash"]))
                pit = predictive_events.get(str(payload["pit_event_hash"]))
                scorecard_key = (event.run_id, str(payload["factor_spec_id"]))
                if (
                    scorecard_key in scorecard_v4_keys
                    or factor is None
                    or observed is None
                    or pit is None
                    or observed.event_type != "ObservedPanelPredictiveEvidenceRecorded"
                    or pit.event_type != "PITPredictiveEvidenceRecorded"
                    or any(source.run_id != event.run_id for source in (factor, observed, pit))
                    or factor.payload["factor_output_hash"] != payload["factor_output_hash"]
                    or observed.payload["evidence_hash"] != payload["observed_evidence_hash"]
                    or pit.payload["evidence_hash"] != payload["pit_evidence_hash"]
                ):
                    return False
                scorecard_v4_keys.add(scorecard_key)
            elif event.event_type == "ExecutionEvidenceRecorded":
                factor = factor_output_events.get(str(payload["factor_output_event_hash"]))
                observed = predictive_events.get(str(payload["observed_predictive_event_hash"]))
                execution_key = (event.run_id, str(payload["factor_spec_id"]))
                sources = [events_by_hash.get(str(item)) for item in payload["source_event_hashes"]]
                if (
                    execution_key in execution_evidence_keys
                    or factor is None
                    or observed is None
                    or observed.event_type != "ObservedPanelPredictiveEvidenceRecorded"
                    or factor.run_id != event.run_id
                    or observed.run_id != event.run_id
                    or factor.payload["factor_spec_id"] != payload["factor_spec_id"]
                    or factor.payload["resolved_contract_hash"] != payload["resolved_contract_hash"]
                    or observed.payload["factor_output_event_hash"] != factor.event_hash
                    or any(source is None for source in sources)
                    or any(
                        source is not None and event_order[source.event_hash] >= event_order[event.event_hash]
                        for source in sources
                    )
                ):
                    return False
                execution_evidence_keys.add(execution_key)
            elif event.event_type == "ComparisonPoolFrozen":
                pool_hash = str(payload["comparison_pool_hash"])
                watermark = events_by_hash.get(str(payload["source_watermark_event_hash"]))
                valid_members = True
                for member in payload["members"]:
                    definition = events_by_hash.get(str(member["factor_definition_event_hash"]))
                    if (
                        definition is None
                        or definition.event_type != "FactorDefinitionRecorded"
                        or definition.payload["factor_spec_id"] != member["factor_spec_id"]
                        or event_order[definition.event_hash] > int(payload["evidence_watermark"])
                    ):
                        valid_members = False
                        break
                    for field, expected_type in (
                        ("factor_output_event_hash", "FactorOutputRecordedV3"),
                        ("execution_event_hash", "ExecutionEvidenceRecorded"),
                        ("decision_event_hash", "QualityDecisionV4Recorded"),
                    ):
                        source_hash = member[field]
                        if source_hash is None:
                            continue
                        source = events_by_hash.get(str(source_hash))
                        if (
                            source is None
                            or source.event_type != expected_type
                            or source.payload.get("factor_spec_id") != member["factor_spec_id"]
                            or event_order[source.event_hash] > int(payload["evidence_watermark"])
                        ):
                            valid_members = False
                            break
                if (
                    pool_hash in comparison_pool_hashes
                    or watermark is None
                    or event_order[watermark.event_hash] != int(payload["evidence_watermark"])
                    or event_order[watermark.event_hash] >= event_order[event.event_hash]
                    or not valid_members
                ):
                    return False
                comparison_pool_hashes.add(pool_hash)
                comparison_pool_events[pool_hash] = event
            elif event.event_type == "SecondaryEvidenceRecorded":
                key = (event.run_id, str(payload["factor_spec_id"]))
                definitions_for_factor = [
                    candidate
                    for candidate in events[: event_order[event.event_hash]]
                    if candidate.event_type == "FactorDefinitionRecorded"
                    and candidate.entity_id == payload["factor_spec_id"]
                ]
                pool = comparison_pool_events.get(str(payload["comparison_pool_hash"]))
                applicability = events_by_hash.get(str(payload["applicability_event_hash"]))
                sources = [events_by_hash.get(str(item)) for item in payload["source_event_hashes"]]
                if (
                    key in secondary_evidence_keys
                    or len(definitions_for_factor) != 1
                    or definitions_for_factor[0].run_id != event.run_id
                    or pool is None
                    or applicability is None
                    or applicability.event_type != "ApplicabilityAssessmentRecorded"
                    or applicability.run_id != event.run_id
                    or applicability.payload["factor_spec_id"] != payload["factor_spec_id"]
                    or any(source is None for source in sources)
                    or any(
                        source is not None and event_order[source.event_hash] >= event_order[event.event_hash]
                        for source in sources
                    )
                ):
                    return False
                secondary_evidence_keys.add(key)
            elif event.event_type == "SelectionAssessmentRecorded":
                prior_events = events[: event_order[event.event_hash]]
                contracts = [
                    item
                    for item in prior_events
                    if item.event_type == "ResolvedEvaluationContractRegistered" and item.run_id == event.run_id
                ]
                starts_for_run = [
                    item for item in prior_events if item.event_type == "TrialStarted" and item.run_id == event.run_id
                ]
                terminals_for_run = [
                    item
                    for item in prior_events
                    if item.event_type == "TrialTerminated" and item.run_id == event.run_id
                ]
                finals_for_run = [
                    item
                    for item in prior_events
                    if item.event_type in {"FinalTestAccessRecorded", "FinalTestArtifactRecorded"}
                    and item.run_id == event.run_id
                ]
                expected_sources = tuple(
                    sorted(
                        {
                            *(item.event_hash for item in contracts),
                            *(item.event_hash for item in starts_for_run),
                            *(item.event_hash for item in terminals_for_run),
                            *(item.event_hash for item in finals_for_run),
                        }
                    )
                )
                if (
                    event.run_id in selection_assessment_runs
                    or len(contracts) != 1
                    or contracts[0].payload["research_family_id"] != payload["research_family_id"]
                    or payload["source_event_hashes"] != expected_sources
                    or payload["trial_count"] != len(starts_for_run)
                    or payload["final_access_count"] != len(finals_for_run)
                ):
                    return False
                selection_assessment_runs.add(event.run_id)
                selection_assessment_events[event.event_hash] = event
            elif event.event_type == "ClaimMatrixRecorded":
                key = (event.run_id, str(payload["factor_spec_id"]))
                selection = selection_assessment_events.get(str(payload["selection_event_hash"]))
                sources = [events_by_hash.get(str(item)) for item in payload["source_event_hashes"]]
                allowed = {
                    "SelectionAssessmentRecorded",
                    "ObservedPanelPredictiveEvidenceRecorded",
                    "PITPredictiveEvidenceRecorded",
                    "ExecutionEvidenceRecorded",
                    "SecondaryEvidenceRecorded",
                }
                if (
                    key in claim_matrix_keys
                    or selection is None
                    or selection.run_id != event.run_id
                    or selection.payload["assessment_hash"] != payload["selection_assessment_hash"]
                    or any(source is None for source in sources)
                    or any(
                        source is not None
                        and (
                            source.event_type not in allowed
                            or event_order[source.event_hash] >= event_order[event.event_hash]
                        )
                        for source in sources
                    )
                ):
                    return False
                claim_matrix_keys.add(key)
                claim_matrix_events[event.event_hash] = event
            elif event.event_type == "QualityDecisionV4Recorded":
                key = (event.run_id, str(payload["factor_spec_id"]))
                matrix = claim_matrix_events.get(str(payload["claim_matrix_event_hash"]))
                selection = selection_assessment_events.get(str(payload["selection_event_hash"]))
                if (
                    key in quality_decision_v4_keys
                    or matrix is None
                    or selection is None
                    or matrix.run_id != event.run_id
                    or selection.run_id != event.run_id
                    or matrix.payload["factor_spec_id"] != payload["factor_spec_id"]
                    or matrix.payload["claim_matrix_hash"] != payload["claim_matrix_hash"]
                    or selection.payload["assessment_hash"] != payload["selection_assessment_hash"]
                ):
                    return False
                quality_decision_v4_keys.add(key)
            elif event.event_type == "ResearchDossierRecorded":
                key = (event.run_id, str(payload["factor_spec_id"]))
                terminal = events_by_hash.get(str(payload["terminal_dossier_event_hash"]))
                decision_hash = payload["quality_decision_event_hash"]
                decision = None if decision_hash is None else events_by_hash.get(str(decision_hash))
                sources = [events_by_hash.get(str(item)) for item in payload["source_event_hashes"]]
                if (
                    key in research_dossier_keys
                    or terminal is None
                    or terminal.event_type != "TrialTerminalDossierRecorded"
                    or terminal.run_id != event.run_id
                    or any(source is None for source in sources)
                    or any(
                        source is not None and event_order[source.event_hash] >= event_order[event.event_hash]
                        for source in sources
                    )
                    or (
                        decision_hash is not None
                        and (
                            decision is None
                            or decision.event_type != "QualityDecisionV4Recorded"
                            or decision.run_id != event.run_id
                            or decision.payload["factor_spec_id"] != payload["factor_spec_id"]
                        )
                    )
                ):
                    return False
                research_dossier_keys.add(key)
            elif event.event_type == "ExperimentRunReportRecorded":
                prior_events = events[: event_order[event.event_hash]]
                starts_for_run = [
                    item for item in prior_events if item.event_type == "TrialStarted" and item.run_id == event.run_id
                ]
                terminals_for_run = [
                    item
                    for item in prior_events
                    if item.event_type == "TrialTerminated" and item.run_id == event.run_id
                ]
                dossiers_for_run = [
                    item
                    for item in prior_events
                    if item.event_type == "ResearchDossierRecorded" and item.run_id == event.run_id
                ]
                decisions_for_run = [
                    item
                    for item in prior_events
                    if item.event_type == "QualityDecisionV4Recorded" and item.run_id == event.run_id
                ]
                expected_sources = tuple(
                    sorted(
                        {
                            *(item.event_hash for item in starts_for_run),
                            *(item.event_hash for item in terminals_for_run),
                            *(item.event_hash for item in dossiers_for_run),
                            *(item.event_hash for item in decisions_for_run),
                        }
                    )
                )
                if (
                    event.run_id in experiment_run_report_runs
                    or not starts_for_run
                    or len(terminals_for_run) != len(starts_for_run)
                    or payload["trial_count"] != len(starts_for_run)
                    or payload["source_event_hashes"] != expected_sources
                    or payload["candidate_dossier_hashes"]
                    != tuple(sorted(str(item.payload["dossier_hash"]) for item in dossiers_for_run))
                ):
                    return False
                experiment_run_report_runs.add(event.run_id)
            elif event.event_type == "ResearchReleaseManifestRecorded":
                prior_sources = [
                    item
                    for item in events[: event_order[event.event_hash]]
                    if item.event_type in {"ResearchDossierRecorded", "ExperimentRunReportRecorded"}
                ]
                if not prior_sources or payload["source_event_hashes"] != tuple(
                    sorted(item.event_hash for item in prior_sources)
                ):
                    return False
            elif event.event_type == "ResearchDossierMaterializationFailed":
                terminal = events_by_hash.get(str(payload["terminal_dossier_event_hash"]))
                decision_hash = payload["quality_decision_event_hash"]
                decision = None if decision_hash is None else events_by_hash.get(str(decision_hash))
                if (
                    terminal is None
                    or terminal.event_type != "TrialTerminalDossierRecorded"
                    or terminal.run_id != event.run_id
                    or terminal.payload["trial_id"] != payload["trial_id"]
                    or (
                        decision_hash is not None
                        and (
                            decision is None
                            or decision.event_type != "QualityDecisionV4Recorded"
                            or decision.run_id != event.run_id
                        )
                    )
                ):
                    return False
            elif event.event_type == "ProductionEvaluationNodeRecorded":
                production_node_key = (
                    str(payload["trial_id"]),
                    str(payload["node_name"]),
                )
                sources = [events_by_hash.get(str(item)) for item in payload["source_event_hashes"]]
                contract = resolved_contract_events.get(str(payload["resolved_contract_hash"]))
                if (
                    production_node_key in production_node_keys
                    or str(payload["trial_id"]) not in started
                    or str(payload["trial_id"]) in terminated
                    or str(payload["factor_spec_id"]) not in definitions
                    or contract is None
                    or contract.run_id != event.run_id
                    or payload["run_id"] != event.run_id
                    or any(source is None for source in sources)
                    or any(
                        source is not None and event_order[source.event_hash] >= event_order[event.event_hash]
                        for source in sources
                    )
                ):
                    return False
                production_node_keys.add(production_node_key)
                production_node_events.add(event.event_hash)
            elif event.event_type == "TrialTerminalDossierRecorded":
                trial_id = str(payload["trial_id"])
                terminal = events_by_hash.get(str(payload["terminal_event_hash"]))
                source_hashes = set(str(item) for item in payload["source_event_hashes"])
                if (
                    trial_id in terminal_dossier_trials
                    or terminal is None
                    or terminal.event_type != "TrialTerminated"
                    or terminal.event_hash not in terminal_hashes
                    or terminal.run_id != event.run_id
                    or not set(payload["node_event_hashes"]).issubset(production_node_events)
                    or not set(payload["evidence_event_hashes"]).issubset(events_by_hash)
                    or not source_hashes.issubset(events_by_hash)
                    or any(event_order[item] >= event_order[event.event_hash] for item in source_hashes)
                ):
                    return False
                terminal_dossier_trials.add(trial_id)
            elif event.event_type == "ReportMaterializationFailed":
                trial_id = str(payload["trial_id"])
                terminal = events_by_hash.get(str(payload["terminal_event_hash"]))
                if (
                    trial_id in report_failure_trials
                    or terminal is None
                    or terminal.event_type != "TrialTerminated"
                    or terminal.run_id != event.run_id
                    or terminal.event_hash not in terminal_hashes
                ):
                    return False
                report_failure_trials.add(trial_id)
            elif event.event_type == "TrainValidDataSnapshotFrozen":
                snapshot_id = str(payload["snapshot_id"])
                snapshot_hash = str(payload["snapshot_hash"])
                if snapshot_id in train_valid_snapshot_ids or snapshot_hash in train_valid_snapshot_hashes:
                    return False
                train_valid_snapshot_ids.add(snapshot_id)
                train_valid_snapshot_hashes.add(snapshot_hash)
                train_valid_snapshot_events[event.event_hash] = event
            elif event.event_type == "RetrieverFeatureSourceRecorded":
                source_id = str(payload["feature_source_id"])
                source_hash = str(payload["source_hash"])
                snapshot = train_valid_snapshot_events.get(str(payload["snapshot_event_hash"]))
                actions = tuple(
                    retriever_action_events.get(str(action_hash)) for action_hash in payload["action_event_hashes"]
                )
                watermark_index = event_order.get(str(payload["eligible_event_watermark"]), -1)
                if (
                    source_id in retriever_feature_source_ids
                    or source_hash in retriever_feature_source_hashes
                    or snapshot is None
                    or event.run_id != payload["execution_run_id"]
                    or event_order[snapshot.event_hash] >= watermark_index
                    or any(action is None for action in actions)
                    or any(
                        action is None
                        or action.run_id != event.run_id
                        or event_order[action.event_hash] <= watermark_index
                        or event_order[action.event_hash] >= event_order[event.event_hash]
                        for action in actions
                    )
                ):
                    return False
                retriever_feature_source_ids.add(source_id)
                retriever_feature_source_hashes.add(source_hash)
                retriever_feature_source_events[event.event_hash] = event
            elif event.event_type == "RetrieverDecisionV5Recorded":
                decision_id = str(payload["decision_id"])
                control_hash = str(payload["control_evidence_event_hash"])
                control_event = next(
                    (
                        candidate
                        for candidate in events
                        if candidate.event_hash == control_hash
                        and candidate.event_type == "OfficialSearchControlRecorded"
                    ),
                    None,
                )
                action_hashes = tuple(str(item) for item in payload["action_template_event_hashes"])
                components = tuple(payload["components"])
                action_events = tuple(retriever_action_events.get(action_hash) for action_hash in action_hashes)
                if (
                    decision_id in retriever_v5_ids
                    or control_event is None
                    or len(action_hashes) != len(components)
                    or any(action is None for action in action_events)
                ):
                    return False
                watermark_index = event_order.get(str(payload["eligible_event_watermark"]), -1)
                control_index = event_order[control_event.event_hash]
                for action_event, component in zip(action_events, components, strict=True):
                    if action_event is None:
                        return False
                    action_payload = action_event.payload
                    if (
                        action_event.run_id != event.run_id
                        or not watermark_index
                        < event_order[action_event.event_hash]
                        < control_index
                        < event_order[event.event_hash]
                        or action_payload["execution_run_id"] != event.run_id
                        or action_payload["action_id"] != component["action_id"]
                        or action_payload["parent_factor_spec_id"] != component["factor_spec_id"]
                        or action_payload["expected_motif"] != component["motif"]
                        or action_payload["identity_action"]
                    ):
                        return False
                retriever_v5_ids.add(decision_id)
                retriever_v5_events[event.event_hash] = event
            elif event.event_type == "RetrieverDecisionV6Recorded":
                decision_id = str(payload["decision_id"])
                source_event = retriever_feature_source_events.get(str(payload["feature_source_event_hash"]))
                control_event = next(
                    (
                        candidate
                        for candidate in events
                        if candidate.event_hash == payload["control_evidence_event_hash"]
                        and candidate.event_type == "OfficialSearchControlRecorded"
                    ),
                    None,
                )
                action_hashes = tuple(str(item) for item in payload["action_template_event_hashes"])
                components = tuple(payload["components"])
                action_events = tuple(retriever_action_events.get(action_hash) for action_hash in action_hashes)
                watermark_index = event_order.get(str(payload["eligible_event_watermark"]), -1)
                if (
                    decision_id in retriever_v6_ids
                    or source_event is None
                    or control_event is None
                    or source_event.run_id != event.run_id
                    or source_event.payload["source_hash"] != payload["feature_source_hash"]
                    or source_event.payload["snapshot_hash"] != payload["data_snapshot_hash"]
                    or source_event.payload["retrieval_policy_hash"] != payload["policy_hash"]
                    or tuple(source_event.payload["action_event_hashes"]) != action_hashes
                    or len(action_hashes) != len(components)
                    or any(action is None for action in action_events)
                    or not watermark_index
                    < event_order[source_event.event_hash]
                    < event_order[control_event.event_hash]
                    < event_order[event.event_hash]
                    or control_event.payload["evidence_hash"] != payload["control_evidence_hash"]
                    or control_event.payload["policy_hash"] != payload["control_policy_hash"]
                    or control_event.payload["output_hash"] != payload["official_output_hash"]
                    or control_event.payload["data_snapshot_hash"] != payload["data_snapshot_hash"]
                ):
                    return False
                for action_event, component in zip(action_events, components, strict=True):
                    if action_event is None:
                        return False
                    action_payload = action_event.payload
                    if (
                        action_event.run_id != event.run_id
                        or not watermark_index
                        < event_order[action_event.event_hash]
                        < event_order[source_event.event_hash]
                        or action_payload["action_id"] != component["action_id"]
                        or action_payload["parent_factor_spec_id"] != component["factor_spec_id"]
                        or action_payload["expected_motif"] != component["motif"]
                        or action_payload["identity_action"]
                    ):
                        return False
                retriever_v6_ids.add(decision_id)
                retriever_v6_events[event.event_hash] = event
            elif event.event_type == "RetrieverDecisionV7Recorded":
                from src.alpha_foundry.activation.runner import (
                    activation_arm_execution_run_id,
                )

                decision_id = str(payload["decision_id"])
                schedule = prearm_schedule_events.get(str(payload["schedule_event_hash"]))
                source = retriever_feature_source_events.get(str(payload["feature_source_event_hash"]))
                if schedule is None or source is None:
                    return False
                control_run_id = activation_arm_execution_run_id(
                    plan_hash=str(payload["plan_hash"]),
                    run_group_id=str(schedule.payload["run_group_id"]),
                    arm="control",
                )
                if (
                    decision_id in retriever_v7_ids
                    or source.run_id != event.run_id
                    or not event_order[schedule.event_hash]
                    < event_order[source.event_hash]
                    < event_order[event.event_hash]
                    or schedule.payload["schedule_hash"] != payload["schedule_hash"]
                    or schedule.payload["plan_hash"] != payload["plan_hash"]
                    or schedule.payload["pair_id"] != payload["pair_id"]
                    or schedule.payload["output_hash"] != payload["official_output_hash"]
                    or schedule.payload["candidate_count"] != payload["candidate_budget"]
                    or source.payload["source_hash"] != payload["feature_source_hash"]
                    or source.payload["snapshot_hash"] != payload["data_snapshot_hash"]
                    or tuple(source.payload["action_event_hashes"]) != tuple(payload["action_template_event_hashes"])
                    or any(
                        candidate.run_id in {event.run_id, control_run_id}
                        and candidate.event_type in {"TrialStarted", "TrialTerminated", "EvaluationRecorded"}
                        and event_order[candidate.event_hash] < event_order[event.event_hash]
                        for candidate in events
                    )
                ):
                    return False
                retriever_v7_ids.add(decision_id)
                retriever_v7_events[event.event_hash] = event
            elif event.event_type == "DerivationRecorded":
                terminal = terminal_payloads.get(str(payload["trial_terminal_event_hash"]))
                definition = definitions.get(str(payload["child_factor_spec_id"]))
                if terminal is None or definition is None:
                    return False
                trial_id = str(definition.get("metadata", {}).get("originating_trial_id", ""))
                evaluation = evaluation_payloads.get(str(terminal["evaluation_event_hash"]))
                if (
                    not trial_id
                    or terminal["trial_id"] != trial_id
                    or terminal["status"] not in {"success", "reject"}
                    or evaluation is None
                    or evaluation["trial_id"] != trial_id
                    or evaluation["factor_spec_id"] != payload["child_factor_spec_id"]
                    or evaluation["data_scope"] not in {"valid", "train_valid"}
                ):
                    return False
            elif event.event_type == "FalsificationContractRegistered":
                contracts[str(payload["contract_id"])] = payload
            elif event.event_type == "OutcomeDataAccessed":
                accessed_factors.add(str(payload["factor_spec_id"]))
            elif event.event_type == "SequentialProtocolRegistered":
                contract_id = str(payload["contract_id"])
                protocol_id = str(payload["protocol_id"])
                contract = contracts.get(contract_id)
                if contract is None:
                    return False
                if (
                    contract["contract_hash"] != payload["contract_hash"]
                    or contract["factor_spec_id"] != payload["factor_spec_id"]
                    or contract["policy_hash"] != payload["policy_hash"]
                    or payload["factor_spec_id"] in accessed_factors
                    or contract_id in result_contracts
                    or contract_id in protocol_by_contract
                    or protocol_id in protocols
                ):
                    return False
                protocols[protocol_id] = payload
                protocol_by_contract[contract_id] = protocol_id
                looks_by_protocol[protocol_id] = []
            elif event.event_type == "SequentialLookRecorded":
                protocol_id = str(payload["protocol_id"])
                protocol = protocols.get(protocol_id)
                if protocol is None:
                    return False
                if any(
                    payload[field] != protocol[field]
                    for field in (
                        "protocol_hash",
                        "factor_spec_id",
                        "support_log_boundary",
                        "contradiction_log_boundary",
                    )
                ):
                    return False
                try:
                    ResearchEventStore._validate_sequential_mixture_evidence(protocol, payload)
                except EventTransitionError:
                    return False
                prior = looks_by_protocol[protocol_id]
                if any(look["look_id"] == payload["look_id"] for _, look in prior):
                    return False
                maximum_looks = int(protocol["maximum_looks"])
                look_index = int(payload["look_index"])
                if look_index > maximum_looks:
                    return False
                if any(
                    look["block_id"] == payload["block_id"] or look["block_hash"] == payload["block_hash"]
                    for _, look in prior
                ):
                    return False
                used_units = {str(unit_hash) for _, look in prior for unit_hash in look["unit_hashes"]}
                if used_units.intersection(str(item) for item in payload["unit_hashes"]):
                    return False
                if not prior:
                    if (
                        look_index != 1
                        or payload["previous_look_event_hash"] is not None
                        or payload["information_time"] != payload["incremental_information"]
                    ):
                        return False
                else:
                    previous_hash, previous = prior[-1]
                    if (
                        previous["status"] != "continue"
                        or look_index != int(previous["look_index"]) + 1
                        or payload["previous_look_event_hash"] != previous_hash
                        or payload["information_time"]
                        != int(previous["information_time"]) + int(payload["incremental_information"])
                    ):
                        return False
                if payload["status"] == "continue" and look_index == maximum_looks:
                    return False
                if payload["status"] == "max_looks_reached" and look_index != maximum_looks:
                    return False
                prior.append((event.event_hash, payload))
            elif event.event_type == "FalsificationResultRecorded":
                contract_id = str(payload["contract_id"])
                contract = contracts.get(contract_id)
                if contract is None or contract["contract_hash"] != payload["contract_hash"]:
                    return False
                sequential_protocol_id = protocol_by_contract.get(contract_id)
                if sequential_protocol_id is not None:
                    looks = looks_by_protocol[sequential_protocol_id]
                    if not looks or looks[-1][1]["status"] == "continue":
                        return False
                result_factors[event.event_hash] = str(contract["factor_spec_id"])
                result_policies[event.event_hash] = str(contract["policy_hash"])
                result_outcomes[event.event_hash] = str(payload["outcome"])
                result_contracts.add(contract_id)
                result_artifact_hashes[event.event_hash] = {
                    str(reference["artifact_hash"]) for reference in payload["artifact_refs"]
                }
            elif event.event_type == "MechanismEvidenceIndexRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                source_events = tuple(str(item) for item in payload["source_event_hashes"])
                if any(result_factors.get(source) != factor_spec_id for source in source_events):
                    return False
                if any(result_policies.get(source) != payload["policy_hash"] for source in source_events):
                    return False
                available_hashes = {
                    artifact_hash
                    for source in source_events
                    for artifact_hash in result_artifact_hashes.get(source, set())
                }
                if not set(str(item) for item in payload["source_result_hashes"]).issubset(available_hashes):
                    return False
                source_outcomes = {
                    source: result_outcomes[source] for source in source_events if source in result_outcomes
                }
                decisive = {str(item) for item in payload["decisive_event_hashes"]}
                if (
                    ResearchEventStore._mechanism_ordinal_state(source_outcomes, decisive) != payload["ordinal_state"]
                    or canonical_json_hash(ResearchEventStore._mechanism_index_content(payload)) != payload["mei_hash"]
                ):
                    return False
            elif event.event_type == "ComplementEvidenceRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                evaluation = evaluation_payloads.get(str(payload["source_evaluation_event_hash"]))
                terminal = terminal_payloads.get(str(payload["source_terminal_event_hash"]))
                if factor_spec_id not in definitions or evaluation is None or terminal is None:
                    return False
                if (
                    evaluation["factor_spec_id"] != factor_spec_id
                    or evaluation["data_scope"] != payload["data_scope"]
                    or evaluation["data_scope"] not in {"valid", "train_valid"}
                    or terminal["trial_id"] != evaluation["trial_id"]
                    or terminal["status"] not in {"success", "reject"}
                ):
                    return False
                if terminal["status"] == "success" and (
                    terminal["evaluation_event_hash"] != payload["source_evaluation_event_hash"]
                ):
                    return False
            elif event.event_type == "ActivationPlanRegistered":
                plan_hash = str(payload["plan_hash"])
                experiment_id = str(payload["experiment_id"])
                if plan_hash in activation_plans or experiment_id in activation_experiments:
                    return False
                activation_plans[plan_hash] = payload
                activation_experiments.add(experiment_id)
                activation_runs[plan_hash] = []
            elif event.event_type == "PreArmFlatScheduleFrozen":
                plan_hash = str(payload["plan_hash"])
                schedule_id = str(payload["schedule_id"])
                schedule_hash = str(payload["schedule_hash"])
                if (
                    plan_hash not in activation_plans
                    or event.run_id != payload["run_group_id"]
                    or schedule_id in prearm_schedule_ids
                    or schedule_hash in prearm_schedule_hashes
                    or any(run["pair_id"] == payload["pair_id"] for run in activation_runs.get(plan_hash, []))
                ):
                    return False
                prearm_schedule_ids.add(schedule_id)
                prearm_schedule_hashes.add(schedule_hash)
                prearm_schedule_events[event.event_hash] = event
            elif event.event_type == "ActivationRunRecorded":
                plan_hash = str(payload["plan_hash"])
                manifest_id = str(payload["manifest_id"])
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or manifest_id in activation_manifest_ids
                ):
                    return False
                activation_manifest_ids.add(manifest_id)
                activation_manifest_hashes.add(str(payload["manifest_hash"]))
                activation_runs[plan_hash].append(payload)
            elif event.event_type == "ActivationRunSourceAudited":
                plan_hash = str(payload["plan_hash"])
                audit_id = str(payload["audit_id"])
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or str(payload["summary_manifest_hash"]) not in activation_manifest_hashes
                    or audit_id in activation_source_audit_ids
                ):
                    return False
                activation_source_audit_ids.add(audit_id)
            elif event.event_type in {"ActivationResourceMeasured", "ActivationResourceMeasuredV2"}:
                plan_hash = str(payload["plan_hash"])
                resource_id = str(payload["resource_id"])
                evidence_hash = str(payload["evidence_hash"])
                manifest_hash = str(payload["manifest_hash"])
                matching_runs = [
                    run
                    for run in activation_runs.get(plan_hash, [])
                    if str(run["manifest_hash"]) == manifest_hash
                    and run["run_group_id"] == payload["run_group_id"]
                    and run["pair_id"] == payload["pair_id"]
                    and run["arm"] == payload["arm"]
                ]
                if event.event_type == "ActivationResourceMeasuredV2":
                    schedule = pair_schedule_events.get(str(payload["pair_schedule_event_hash"]))
                    claim = pair_claim_events.get(str(payload["execution_claim_event_hash"]))
                    if (
                        schedule is None
                        or claim is None
                        or schedule.payload["schedule_hash"] != payload["pair_schedule_hash"]
                        or claim.payload["schedule_event_hash"] != schedule.event_hash
                        or claim.payload["schedule_hash"] != payload["pair_schedule_hash"]
                    ):
                        return False
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or event.run_id != payload["run_group_id"]
                    or len(matching_runs) != 1
                    or resource_id in activation_resource_ids
                    or evidence_hash in activation_resource_evidence_hashes
                    or manifest_hash in activation_resource_manifest_hashes
                ):
                    return False
                activation_resource_ids.add(resource_id)
                activation_resource_evidence_hashes.add(evidence_hash)
                activation_resource_manifest_hashes.add(manifest_hash)
            elif event.event_type == "ActivationGenerationConsumptionRecorded":
                plan_hash = str(payload["plan_hash"])
                generation_id = str(payload["generation_id"])
                evidence_hash = str(payload["evidence_hash"])
                retriever_source = retriever_v4_events.get(str(payload["retriever_decision_event_hash"]))
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or event.run_id != payload["execution_run_id"]
                    or retriever_source is None
                    or retriever_source.run_id != event.run_id
                    or generation_id in activation_generation_ids
                    or evidence_hash in activation_generation_hashes
                ):
                    return False
                activation_generation_ids.add(generation_id)
                activation_generation_hashes.add(evidence_hash)
            elif event.event_type == "ActivationGenerationConsumptionV2Recorded":
                plan_hash = str(payload["plan_hash"])
                generation_id = str(payload["generation_id"])
                evidence_hash = str(payload["evidence_hash"])
                retriever_source = retriever_v5_events.get(str(payload["retriever_decision_event_hash"]))
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or event.run_id != payload["execution_run_id"]
                    or retriever_source is None
                    or retriever_source.run_id != event.run_id
                    or retriever_source.payload["decision_hash"] != payload["retriever_decision_hash"]
                    or retriever_source.payload["selected_action_ids"] != payload["selected_action_ids"]
                    or generation_id in activation_generation_ids
                    or evidence_hash in activation_generation_hashes
                ):
                    return False
                activation_generation_ids.add(generation_id)
                activation_generation_hashes.add(evidence_hash)
            elif event.event_type == "ActivationGenerationConsumptionV3Recorded":
                plan_hash = str(payload["plan_hash"])
                generation_id = str(payload["generation_id"])
                evidence_hash = str(payload["evidence_hash"])
                retriever_source = retriever_v6_events.get(str(payload["retriever_decision_event_hash"]))
                feature_source = retriever_feature_source_events.get(str(payload["feature_source_event_hash"]))
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or event.run_id != payload["execution_run_id"]
                    or retriever_source is None
                    or feature_source is None
                    or retriever_source.run_id != event.run_id
                    or feature_source.run_id != event.run_id
                    or retriever_source.payload["decision_hash"] != payload["retriever_decision_hash"]
                    or retriever_source.payload["input_bundle_hash"] != payload["retriever_input_bundle_hash"]
                    or retriever_source.payload["feature_source_event_hash"] != feature_source.event_hash
                    or retriever_source.payload["selected_action_ids"] != payload["selected_action_ids"]
                    or feature_source.payload["source_hash"] != payload["feature_source_hash"]
                    or generation_id in activation_generation_ids
                    or evidence_hash in activation_generation_hashes
                ):
                    return False
                activation_generation_ids.add(generation_id)
                activation_generation_hashes.add(evidence_hash)
            elif event.event_type == "ActivationGenerationConsumptionV4Recorded":
                plan_hash = str(payload["plan_hash"])
                generation_id = str(payload["generation_id"])
                evidence_hash = str(payload["evidence_hash"])
                retriever = retriever_v7_events.get(str(payload["retriever_decision_event_hash"]))
                schedule = prearm_schedule_events.get(str(payload["schedule_event_hash"]))
                source = retriever_feature_source_events.get(str(payload["feature_source_event_hash"]))
                if (
                    plan_hash not in activation_plans
                    or plan_hash in activation_result_plans
                    or event.run_id != payload["execution_run_id"]
                    or retriever is None
                    or schedule is None
                    or source is None
                    or retriever.run_id != event.run_id
                    or source.run_id != event.run_id
                    or retriever.payload["decision_hash"] != payload["retriever_decision_hash"]
                    or retriever.payload["input_bundle_hash"] != payload["retriever_input_bundle_hash"]
                    or retriever.payload["schedule_event_hash"] != schedule.event_hash
                    or retriever.payload["selected_action_ids"] != payload["selected_action_ids"]
                    or schedule.payload["schedule_hash"] != payload["schedule_hash"]
                    or source.payload["source_hash"] != payload["feature_source_hash"]
                    or generation_id in activation_generation_ids
                    or evidence_hash in activation_generation_hashes
                ):
                    return False
                activation_generation_ids.add(generation_id)
                activation_generation_hashes.add(evidence_hash)
            elif event.event_type == "ActivationResultRecorded":
                plan_hash = str(payload["plan_hash"])
                result_hash = str(payload["result_hash"])
                if plan_hash not in activation_plans or plan_hash in activation_result_plans:
                    return False
                runs = activation_runs[plan_hash]
                if not runs:
                    if payload["replayable"] or not payload["invalidation_reasons"]:
                        return False
                else:
                    arms: dict[str, set[str]] = {}
                    for run in runs:
                        arms.setdefault(str(run["pair_id"]), set()).add(str(run["arm"]))
                    if int(payload["complete_pairs"]) != sum(
                        1 for pair_arms in arms.values() if pair_arms == {"control", "treatment"}
                    ):
                        return False
                activation_results[result_hash] = payload
                activation_result_plans.add(plan_hash)
            elif event.event_type == "RetrieverActivationDecisionRecorded":
                plan_hash = str(payload["plan_hash"])
                result = activation_results.get(str(payload["result_hash"]))
                plan = activation_plans.get(plan_hash)
                if (
                    result is None
                    or plan is None
                    or result["plan_hash"] != plan_hash
                    or plan_hash in activation_decision_plans
                ):
                    return False
                if payload["verdict"] == "approved" and (
                    plan["phase"] != "confirmatory" or not result["replayable"] or result["invalidation_reasons"]
                ):
                    return False
                activation_decision_plans.add(plan_hash)
            elif event.event_type == "DecisionEvidenceV3Recorded":
                factor_spec_id = str(payload["factor_spec_id"])
                key = (
                    event.run_id,
                    factor_spec_id,
                    str(payload["evidence_kind"]),
                )
                if (
                    factor_spec_id not in definitions
                    or event.run_id != payload["evidence_run_id"]
                    or key in decision_evidence_keys
                    or event.previous_event_hash != payload["ledger_watermark_event_hash"]
                    or any(
                        event_order.get(str(source_hash), len(events)) >= event_order[event.event_hash]
                        for source_hash in payload["source_event_hashes"]
                    )
                ):
                    return False
                decision_evidence_keys.add(key)
            elif event.event_type == "ScorecardDecisionEvidenceV3Recorded":
                factor_spec_id = str(payload["factor_spec_id"])
                trial_id = str(payload["trial_id"])
                key = (event.run_id, factor_spec_id, trial_id)
                policy_event = events_by_hash.get(str(payload["evaluation_policy_event_hash"]))
                snapshot_event = events_by_hash.get(str(payload["snapshot_event_hash"]))
                if (
                    factor_spec_id not in definitions
                    or definition_event_hashes.get(factor_spec_id) != payload["factor_definition_event_hash"]
                    or started.get(trial_id) != event.run_id
                    or trial_id in terminated
                    or event.run_id != payload["evidence_run_id"]
                    or key in scorecard_evidence_keys
                    or policy_event is None
                    or policy_event.event_type != "EvaluationPolicyRegistered"
                    or policy_event.run_id != event.run_id
                    or snapshot_event is None
                    or snapshot_event.event_type != "TrainValidDataSnapshotFrozen"
                    or event.previous_event_hash != payload["source_watermark_event_hash"]
                    or any(
                        event_order.get(str(source_hash), len(events)) >= event_order[event.event_hash]
                        for source_hash in payload["source_event_hashes"]
                    )
                ):
                    return False
                scorecard_evidence_keys.add(key)
            elif event.event_type == "SnapshotDecisionEvidenceV3Recorded":
                factor_spec_id = str(payload["factor_spec_id"])
                snapshot_key = (event.run_id, factor_spec_id)
                policy_event = events_by_hash.get(str(payload["evaluation_policy_event_hash"]))
                snapshot_event = events_by_hash.get(str(payload["snapshot_event_hash"]))
                if (
                    factor_spec_id not in definitions
                    or definition_event_hashes.get(factor_spec_id) != payload["factor_definition_event_hash"]
                    or event.run_id != payload["evidence_run_id"]
                    or snapshot_key in snapshot_evidence_keys
                    or policy_event is None
                    or policy_event.event_type != "EvaluationPolicyRegistered"
                    or policy_event.run_id != event.run_id
                    or snapshot_event is None
                    or snapshot_event.event_type != "TrainValidDataSnapshotFrozen"
                    or event.previous_event_hash != payload["source_watermark_event_hash"]
                    or any(
                        event_order.get(str(source_hash), len(events)) >= event_order[event.event_hash]
                        for source_hash in payload["source_event_hashes"]
                    )
                ):
                    return False
                snapshot_evidence_keys.add(snapshot_key)
            elif event.event_type in {"QualityDecisionV2Recorded", "QualityDecisionV3Recorded"}:
                if str(payload["factor_spec_id"]) not in definitions:
                    return False
            elif event.event_type == "FinalCandidateFrozen":
                factor_spec_id = str(payload["factor_spec_id"])
                candidate_hash = str(payload["candidate_hash"])
                if (
                    factor_spec_id not in definitions
                    or definition_event_hashes.get(factor_spec_id) != payload["definition_hash"]
                    or candidate_hash in final_candidates
                ):
                    return False
                final_candidates[candidate_hash] = payload
            elif event.event_type == "FinalTestCapabilityIssued":
                candidate_hash = str(payload["candidate_hash"])
                token_hash = str(payload["capability_fingerprint"])
                candidate = final_candidates.get(candidate_hash)
                if (
                    candidate is None
                    or token_hash in final_capabilities
                    or any(item["candidate_hash"] == candidate_hash for item in final_capabilities.values())
                    or candidate["factor_spec_id"] != payload["factor_spec_id"]
                    or candidate["data_snapshot_hash"] != payload["data_snapshot_hash"]
                ):
                    return False
                final_capabilities[token_hash] = payload
            elif event.event_type == "FinalTestAccessRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                token_hash = str(payload["capability_fingerprint"])
                if payload["outcome"] == "allowed" and factor_spec_id not in definitions:
                    return False
                if payload["outcome"] == "allowed":
                    capability = final_capabilities.get(token_hash)
                    if (
                        capability is None
                        or token_hash in allowed_final_tokens
                        or any(
                            capability[field] != payload[field]
                            for field in (
                                "candidate_hash",
                                "factor_spec_id",
                                "declared_run_id",
                            )
                        )
                    ):
                        return False
                    allowed_final_tokens.add(token_hash)
                final_accesses[event.event_hash] = payload
            elif event.event_type == "FinalTestArtifactRecorded":
                access = final_accesses.get(str(payload["access_event_hash"]))
                candidate_hash = str(payload["candidate_hash"])
                if (
                    access is None
                    or access["outcome"] != "allowed"
                    or access["candidate_hash"] != candidate_hash
                    or access["factor_spec_id"] != payload["factor_spec_id"]
                    or candidate_hash in final_artifacts
                ):
                    return False
                candidate = final_candidates.get(candidate_hash)
                if candidate is None or any(
                    candidate[field] != payload[field]
                    for field in (
                        "definition_hash",
                        "transform_pipeline_hash",
                        "cost_model_hash",
                        "regime_config_hash",
                        "policy_hash",
                        "data_snapshot_hash",
                    )
                ):
                    return False
                final_artifacts[candidate_hash] = payload
            elif event.event_type == "ForwardPlanV2Recorded":
                artifact = next(
                    (
                        item
                        for item in final_artifacts.values()
                        if item["artifact_hash"] == payload["final_test_artifact_hash"]
                    ),
                    None,
                )
                plan_id = str(payload["plan_id"])
                if (
                    artifact is None
                    or artifact["factor_spec_id"] != payload["factor_spec_id"]
                    or not artifact["quality_passed"]
                    or artifact["contaminated"]
                    or any(
                        artifact[field] != payload[field]
                        for field in (
                            "definition_hash",
                            "transform_pipeline_hash",
                            "cost_model_hash",
                            "regime_config_hash",
                            "policy_hash",
                        )
                    )
                    or plan_id in forward_v2_plans
                ):
                    return False
                forward_v2_plans[plan_id] = payload
                forward_v2_observations[plan_id] = []
            elif event.event_type == "ForwardObservationV2Recorded":
                plan_id = str(payload["plan_id"])
                plan = forward_v2_plans.get(plan_id)
                if plan is None or plan["plan_hash"] != payload["plan_hash"]:
                    return False
                forward_prior = forward_v2_observations[plan_id]
                if not forward_prior:
                    if payload["previous_observation_hash"] is not None:
                        return False
                else:
                    previous = forward_prior[-1]
                    if (
                        payload["period_start"] <= previous["period_end"]
                        or payload["previous_observation_hash"] != previous["observation_hash"]
                    ):
                        return False
                forward_prior.append(payload)
            elif event.event_type == "ForwardPlanRecorded":
                plans.add(str(payload["plan_id"]))
            elif event.event_type == "ForwardObservationRecorded":
                if str(payload["plan_id"]) not in plans:
                    return False
        return True

    @staticmethod
    def _graph_has_path(graph: Mapping[str, set[str]], start: str, target: str) -> bool:
        pending = [start]
        seen: set[str] = set()
        while pending:
            current = pending.pop()
            if current == target:
                return True
            if current in seen:
                continue
            seen.add(current)
            pending.extend(graph.get(current, ()))
        return False

    def replay(self) -> ReplayState:
        try:
            events = self.query_events()
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise ResearchEventAppendError("replay verification failed: unreadable event") from exc
        if not self._verify_events(events):
            raise ResearchEventAppendError("replay verification failed: invalid event chain")
        return build_replay_state(events)

    def lifecycle_summary(self) -> LifecycleSummary:
        events = self.query_events()
        started = {str(event.payload["trial_id"]) for event in events if event.event_type == "TrialStarted"}
        terminal_by_trial = {
            str(event.payload["trial_id"]): str(event.payload["status"])
            for event in events
            if event.event_type == "TrialTerminated"
        }
        return LifecycleSummary(
            started_count=len(started),
            terminal_count=len(terminal_by_trial),
            open_trial_ids=tuple(sorted(started - set(terminal_by_trial))),
            terminal_status_counts=tuple(sorted(Counter(terminal_by_trial.values()).items())),
        )

    def _verified_subsequence(
        self,
        selected: list[ResearchEventEnvelope] | tuple[ResearchEventEnvelope, ...],
    ) -> VerifiedEventSubsequence:
        """Bind an exact ordered subset to a verified full event chain."""

        full = self.query_events()
        if not full or not self._verify_events(full):
            raise ResearchEventAppendError("cannot issue an event subsequence from an empty or invalid chain")
        by_hash = {event.event_hash: (index, event) for index, event in enumerate(full)}
        prior_index = -1
        normalized: list[ResearchEventEnvelope] = []
        seen: set[str] = set()
        for event in selected:
            source = by_hash.get(event.event_hash)
            if source is None or source[0] <= prior_index or event.event_hash in seen:
                raise EventValidationError("selected events are not a unique ordered full-chain subsequence")
            if source[1].to_dict() != event.to_dict():
                raise EventValidationError("selected event differs from its full-chain source")
            prior_index = source[0]
            seen.add(event.event_hash)
            normalized.append(source[1])
        replay = build_replay_state(full)
        return _issue_verified_event_subsequence(
            events=tuple(normalized),
            full_event_count=len(full),
            full_chain_head=full[-1].event_hash,
            full_replay_hash=replay.projection_hash,
        )

    def _events_through_watermark(
        self,
        watermark_event_hash: str,
    ) -> tuple[ResearchEventEnvelope, ...]:
        full = self.query_events()
        matches = [index for index, event in enumerate(full) if event.event_hash == watermark_event_hash]
        if len(matches) != 1:
            raise EventValidationError("historical discovery watermark is unknown")
        return tuple(full[: matches[0] + 1])

    def _verified_subsequence_at_watermark(
        self,
        selected: list[ResearchEventEnvelope] | tuple[ResearchEventEnvelope, ...],
        *,
        watermark_event_hash: str,
    ) -> VerifiedEventSubsequence:
        """Bind an exact subset to a verified historical chain prefix."""

        prefix = self._events_through_watermark(watermark_event_hash)
        if not prefix or not self._verify_events(list(prefix)):
            raise ResearchEventAppendError("cannot issue an event subsequence from an invalid historical prefix")
        by_hash = {event.event_hash: (index, event) for index, event in enumerate(prefix)}
        prior_index = -1
        normalized: list[ResearchEventEnvelope] = []
        seen: set[str] = set()
        for event in selected:
            source = by_hash.get(event.event_hash)
            if source is None or source[0] <= prior_index or event.event_hash in seen:
                raise EventValidationError("selected events are not an ordered historical-prefix subsequence")
            if source[1].to_dict() != event.to_dict():
                raise EventValidationError("selected historical event differs from its prefix source")
            prior_index = source[0]
            seen.add(event.event_hash)
            normalized.append(source[1])
        replay = build_replay_state(prefix)
        return _issue_verified_event_subsequence(
            events=tuple(normalized),
            full_event_count=len(prefix),
            full_chain_head=prefix[-1].event_hash,
            full_replay_hash=replay.projection_hash,
        )

    def update(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        raise EventMutationError("research events are append-only")

    def delete(self, *args: Any, **kwargs: Any) -> None:  # noqa: ARG002
        raise EventMutationError("research events are append-only")


__all__ = ["DurabilityProfile", "ResearchEventStore"]
