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
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
    ),
    "ProcessActionFrozenV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
    ),
    "ProcessOutcomeRecordedV2": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY",
    ),
    "RetrieverDecisionV2Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverActionTemplateFrozen": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV3Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV4Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverDecisionV5Recorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "OfficialSearchControlRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_RESEARCH_EVENTS",
    ),
    "ActivationPlanRegistered": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationRunRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationRunSourceAudited": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationResourceMeasured": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationGenerationConsumptionRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "ActivationResultRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "RetrieverActivationDecisionRecorded": (
        "VIBE_TRADING_ALPHA_FOUNDRY", "VIBE_TRADING_FACTOR_DAG",
        "VIBE_TRADING_PROCESS_MEMORY", "VIBE_TRADING_TOPOLOGY_RETRIEVER",
    ),
    "FalsificationContractRegistered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "SequentialProtocolRegistered": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "SequentialLookRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "OutcomeDataAccessed": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "FalsificationResultRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "MechanismEvidenceIndexRecorded": ("VIBE_TRADING_FALSIFICATION_CONTRACT",),
    "ComplementEvidenceRecorded": ("VIBE_TRADING_COMPLEMENT_V2",),
    "QualityDecisionRecorded": ("VIBE_TRADING_ADMISSION_GATE",),
    "QualityDecisionV2Recorded": ("VIBE_TRADING_DECISION_V2",),
    "QualityDecisionV3Recorded": ("VIBE_TRADING_DECISION_V2",),
    "FinalCandidateFrozen": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestCapabilityIssued": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestAccessRecorded": ("VIBE_TRADING_DECISION_V2",),
    "FinalTestArtifactRecorded": ("VIBE_TRADING_DECISION_V2",),
    "ForwardPlanV2Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardObservationV2Recorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardPlanRecorded": ("VIBE_TRADING_FORWARD_TRACKING",),
    "ForwardObservationRecorded": ("VIBE_TRADING_FORWARD_TRACKING",),
}


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
                    raise ResearchEventAppendError(
                        f"research-event schema initialization failed: {exc}"
                    ) from exc
                self._retry_delay(attempt)
        raise ResearchEventAppendError(
            f"research-event schema initialization failed after retries: {last_error}"
        )

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
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(research_events)").fetchall()
            }
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
                raise ResearchEventAppendError(
                    "incompatible research_events schema; additive migration required"
                )

    def append_event(self, draft: EventDraft) -> ResearchEventEnvelope:
        self._validate_event_capability(draft.event_type)
        self._validate_draft_identity(draft)
        payload = validate_and_redact_payload(
            draft.event_type,
            draft.payload_schema_version,
            draft.payload,
        )
        self._validate_payload_entity(draft, payload)
        self._validate_artifacts(payload)
        self._validate_external_process_evidence(draft.event_type, payload)
        self._validate_external_retriever_action_template(
            draft.event_type, payload
        )
        self._validate_external_retriever_evidence(draft.event_type, payload)
        self._validate_external_retriever_v4_evidence(draft.event_type, payload)
        self._validate_external_retriever_v5_evidence(draft.event_type, payload)
        self._validate_external_quality_decision_evidence(draft.event_type, payload)
        self._validate_external_activation_source_audit(draft.event_type, payload)
        self._validate_external_official_control_evidence(draft.event_type, payload)
        self._validate_external_activation_resource(draft.event_type, payload)
        self._validate_external_generation_consumption(draft.event_type, payload)
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
        missing = [
            name for name in _EVENT_CAPABILITY_REQUIREMENTS.get(event_type, ())
            if not self.flags.enabled(name)
        ]
        if missing:
            raise EventValidationError(
                f"event capability is disabled for {event_type}: {sorted(missing)}"
            )

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
            raise EventIdempotencyConflict(
                f"idempotency key conflicts with existing event: {draft.idempotency_key}"
            )
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
            "RetrieverDecisionV2Recorded": "decision_id",
            "RetrieverDecisionV3Recorded": "decision_id",
            "RetrieverDecisionV4Recorded": "decision_id",
            "RetrieverDecisionV5Recorded": "decision_id",
            "OfficialSearchControlRecorded": "control_id",
            "ActivationPlanRegistered": "experiment_id",
            "ActivationRunRecorded": "manifest_id",
            "ActivationRunSourceAudited": "audit_id",
            "ActivationResourceMeasured": "resource_id",
            "ActivationGenerationConsumptionRecorded": "generation_id",
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
            "QualityDecisionV2Recorded": "decision_id",
            "QualityDecisionV3Recorded": "decision_id",
            "FinalCandidateFrozen": "freeze_id",
            "FinalTestCapabilityIssued": "capability_id",
            "FinalTestAccessRecorded": "access_id",
            "FinalTestArtifactRecorded": "artifact_id",
            "ForwardPlanV2Recorded": "plan_id",
            "ForwardObservationV2Recorded": "observation_id",
            "ForwardPlanRecorded": "plan_id",
            "ForwardObservationRecorded": "observation_id",
        }
        field = identity_fields[draft.event_type]
        if draft.entity_id != payload[field]:
            raise EventValidationError(
                f"entity_id must equal payload {field} for {draft.event_type}"
            )

    def _validate_artifacts(self, payload: dict[str, Any]) -> None:
        references = payload.get("artifact_refs")
        if references is None:
            return
        payload["artifact_refs"] = validate_artifact_references(
            self.artifact_root,
            references,
        )

    def _validate_external_process_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild utility before the write transaction; never do file IO under lock."""
        if event_type != "ProcessOutcomeRecordedV2":
            return
        actions = self.query_events(
            event_type="ProcessActionFrozenV2", entity_id=str(payload["action_id"])
        )
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
            raise EventValidationError(
                "Retriever action lacks its authoritative parent definition"
            )
        try:
            from src.alpha_foundry.retrieval.action_template_v1 import (
                FrozenRetrieverActionTemplateV1,
            )

            rebuilt = FrozenRetrieverActionTemplateV1.build(
                execution_run_id=str(payload["execution_run_id"]),
                parent_definition=definitions[0].payload,
                parent_definition_event_hash=str(
                    payload["parent_definition_event_hash"]
                ),
                eligible_event_watermark=str(payload["eligible_event_watermark"]),
                data_snapshot_hash=str(payload["data_snapshot_hash"]),
                retrieval_policy_hash=str(payload["retrieval_policy_hash"]),
                template_id=str(payload["template_id"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EventValidationError(
                "Retriever action template cannot be deterministically rebuilt"
            ) from exc
        if rebuilt.to_dict() != dict(payload):
            raise EventValidationError(
                "Retriever action differs from its deterministic template rebuild"
            )

    def _validate_external_retriever_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild v3 components, propensities and selection before locking."""
        if event_type != "RetrieverDecisionV3Recorded":
            return
        references = [
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.retriever-input-bundle-v3+json"
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
            raise EventValidationError(
                "retriever v3 source evidence cannot rebuild the decision"
            ) from exc
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
            raise EventValidationError(
                "retriever v3 event differs from its deterministically rebuilt decision"
            )

    def _validate_external_retriever_v4_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "RetrieverDecisionV4Recorded":
            return
        references = [
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.retriever-input-bundle-v4+json"
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
                event for event in self.query_events(
                    event_type="OfficialSearchControlRecorded"
                )
                if event.event_hash == bundle.control_evidence_event_hash
            ]
            if len(controls) != 1:
                raise ValueError("retriever v4 control event is missing")
            control_event = controls[0]
            control_refs = [
                reference for reference in control_event.payload["artifact_refs"]
                if reference["media_type"]
                == OfficialSearchControlArtifactStoreV1.media_type
            ]
            if len(control_refs) != 1:
                raise ValueError("retriever v4 control artifact is missing")
            control = OfficialSearchControlArtifactStoreV1(self.artifact_root).read(
                str(control_refs[0]["relative_path"]),
                bundle.control_evidence_hash,
            )
            all_events = self.query_events()
            event_order = {
                event.event_hash: index for index, event in enumerate(all_events)
            }
            watermark_order = event_order.get(source.eligible_event_watermark, -1)
            if (
                watermark_order < 0
                or event_order.get(control_event.event_hash, -1) <= watermark_order
                or any(
                    event_order.get(event_hash, -1) <= watermark_order
                    for event_hash in control.terminal_event_hashes
                )
            ):
                raise ValueError(
                    "retriever v4 discovery watermark includes control evidence"
                )
            official_ids = tuple(str(item["candidate_id"]) for item in control.candidates)
            if (
                official_ids != source.official_candidate_ids
                or control_event.payload["evidence_hash"]
                != bundle.control_evidence_hash
                or control_event.payload["policy_hash"]
                != control.policy.policy_hash
                or control_event.payload["output_hash"] != control.output_hash
                or control.output_hash != payload["official_output_hash"]
                or control.policy.policy_hash != payload["control_policy_hash"]
                or control.data_snapshot_hash != source.data_snapshot_hash
                or control_event.run_id != control.run_id
            ):
                raise ValueError("retriever v4 control binding differs")
            discovery = DiscoveryEvidenceProjector(
                flags=self.flags
            ).project_at_watermark(
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
            raise EventValidationError(
                "retriever v4 source evidence cannot rebuild the decision"
            ) from exc
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
            if reference["media_type"]
            == "application/vnd.vibe.retriever-input-bundle-v5+json"
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
                for event in self.query_events(
                    event_type="OfficialSearchControlRecorded"
                )
                if event.event_hash == bundle.control_evidence_event_hash
            ]
            if len(controls) != 1:
                raise ValueError("retriever v5 control event is missing")
            control_event = controls[0]
            control_refs = [
                reference
                for reference in control_event.payload["artifact_refs"]
                if reference["media_type"]
                == OfficialSearchControlArtifactStoreV1.media_type
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
            action_events = [
                by_hash.get(event_hash)
                for event_hash in bundle.action_template_event_hashes
            ]
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
                or any(
                    order.get(event_hash, -1) <= watermark_order
                    for event_hash in control.terminal_event_hashes
                )
            ):
                raise ValueError("retriever v5 source ordering is invalid")
            actions = tuple(
                FrozenRetrieverActionTemplateV1.from_dict(event.payload)
                for event in action_events
                if event is not None
            )
            policy = ActivationRetrieverPolicy(**dict(source.policy_config))
            if any(
                action.eligible_event_watermark != source.eligible_event_watermark
                or action.data_snapshot_hash != source.data_snapshot_hash
                or action.retrieval_policy_hash != policy.policy_hash
                for action in actions
            ):
                raise ValueError("retriever v5 action scope or policy differs")
            official_ids = tuple(
                str(item["candidate_id"]) for item in control.candidates
            )
            if (
                official_ids != source.official_candidate_ids
                or control_event.payload["evidence_hash"]
                != bundle.control_evidence_hash
                or control_event.payload["policy_hash"]
                != control.policy.policy_hash
                or control_event.payload["output_hash"] != control.output_hash
                or control.output_hash != payload["official_output_hash"]
                or control.policy.policy_hash != payload["control_policy_hash"]
                or control.data_snapshot_hash != source.data_snapshot_hash
                or control_event.run_id != control.run_id
            ):
                raise ValueError("retriever v5 control binding differs")
            discovery = DiscoveryEvidenceProjector(
                flags=self.flags
            ).project_at_watermark(
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
            raise EventValidationError(
                "retriever v5 source evidence cannot rebuild the action decision"
            ) from exc
        expected = {
            "shadow_decision_hash": decision.decision_hash,
            "input_bundle_hash": bundle.bundle_hash,
            "control_evidence_event_hash": bundle.control_evidence_event_hash,
            "control_evidence_hash": bundle.control_evidence_hash,
            "control_policy_hash": control.policy.policy_hash,
            "selected_action_ids": list(decision.selected_action_ids),
            "selected_parent_factor_spec_ids": list(
                decision.selected_parent_factor_spec_ids
            ),
            "action_template_event_hashes": list(
                decision.action_template_event_hashes
            ),
            "seed": decision.seed,
            "policy_version": decision.policy_version,
            "policy_hash": decision.policy_hash,
            "policy_config": dict(decision.policy_config),
            "eligible_event_watermark": decision.eligible_event_watermark,
            "data_snapshot_hash": decision.data_snapshot_hash,
            "candidate_budget": decision.candidate_budget,
            "official_output_hash": decision.official_output_hash,
            "propensity_semantics": decision.propensity_semantics,
            "components": [
                component.to_dict() for component in decision.components
            ],
            "shadow_only": decision.shadow_only,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("retriever v5 differs from deterministic rebuild")

    def _validate_external_quality_decision_evidence(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        """Rebuild a source-bound quality decision before locking or replaying."""
        if event_type != "QualityDecisionV3Recorded":
            return
        references = [
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.quality-decision-input-v3+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Decision v3 requires one source input bundle")
        reference = references[0]
        try:
            from src.alpha_quality.decision_v2.runner import QualityDecisionV2Runner
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
            decision = QualityDecisionV2Runner(
                flags=self.flags,
                policy=policy,
                repository=FrozenDecisionEvidenceRepository(bundle.evidence_records),
            ).run(bundle.evidence_refs)
            content = source_bound_quality_decision_content(
                decision,
                input_bundle_hash=bundle.bundle_hash,
            )
        except (KeyError, OSError, TypeError, ValueError, RuntimeError) as exc:
            raise EventValidationError(
                "Decision v3 source evidence cannot rebuild the decision"
            ) from exc
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
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.activation-run-source-v2+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation source audit requires one v2 artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.run_source_v2 import (
                ActivationRunSourceAuditV2,
            )

            raw = ActivationArtifactStore(self.artifact_root).get(
                "run_source", str(payload["audit_hash"])
            )
            expected_relative = ActivationArtifactStore.relative_path(
                "run_source", str(payload["audit_hash"])
            )
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
            "retriever_decision_event_hashes": list(
                audit.retriever_decision_event_hashes
            ),
            "terminal_event_hashes": list(audit.terminal_event_hashes),
            "evaluation_event_hashes": list(audit.evaluation_event_hashes),
            "quality_decision_event_hashes": list(
                audit.quality_decision_event_hashes
            ),
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
            indexes.get(event_hash, watermark_index + 1) > watermark_index
            for event_hash in source_hashes
        ):
            raise EventValidationError("Activation source audit cites an invalid watermark")
        summaries = [
            event for event in events
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
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.official-search-control-v1+json"
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
                event for event in events
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
            expected_trial_id = (
                "trial-search-" + expected_trial_hash.removeprefix("sha256:")[:24]
            )
            trial_id = str(terminal.payload["trial_id"])
            start = starts.get(trial_id)
            if (
                trial_id != expected_trial_id
                or start is None
                or start.payload["candidate_id"] != candidate["candidate_id"]
            ):
                raise EventValidationError(
                    "official control trial order or snapshot binding is invalid"
                )

    def _validate_external_activation_resource(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationResourceMeasured":
            return
        references = [
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.activation-resource-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError("Activation resource requires one evidence artifact")
        try:
            from src.alpha_foundry.activation.artifacts import ActivationArtifactStore
            from src.alpha_foundry.activation.resource_v1 import (
                validate_resource_evidence_mapping,
            )

            artifacts = ActivationArtifactStore(self.artifact_root)
            expected_relative = artifacts.relative_path(
                "resource", str(payload["evidence_hash"])
            )
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("Activation resource artifact path is not canonical")
            raw = artifacts.get("resource", str(payload["evidence_hash"]))
            evidence = validate_resource_evidence_mapping(raw)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise EventValidationError("Activation resource artifact is invalid") from exc
        expected = {
            key: value for key, value in evidence.items()
            if key != "schema_version"
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError("Activation resource event differs from artifact")

    def _validate_external_generation_consumption(
        self,
        event_type: str,
        payload: Mapping[str, Any],
    ) -> None:
        if event_type != "ActivationGenerationConsumptionRecorded":
            return
        references = [
            reference for reference in payload["artifact_refs"]
            if reference["media_type"]
            == "application/vnd.vibe.activation-generation-consumption-v1+json"
        ]
        if len(references) != 1:
            raise EventValidationError(
                "Activation generation requires one source artifact"
            )
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
            expected_relative = artifacts.relative_path(
                "generation_consumption", str(payload["evidence_hash"])
            )
            if str(references[0]["relative_path"]).replace("\\", "/") != expected_relative:
                raise ValueError("generation-consumption artifact path is not canonical")
            evidence = ActivationGenerationConsumptionV1.from_dict(
                artifacts.get(
                    "generation_consumption", str(payload["evidence_hash"])
                )
            )
            plan_matches = [
                event for event in self.query_events(
                    event_type="ActivationPlanRegistered"
                )
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
                event for event in self.query_events(
                    event_type="RetrieverDecisionV4Recorded"
                )
                if event.event_hash == evidence.retriever_decision_event_hash
            ]
            if len(retriever_matches) != 1:
                raise ValueError("generation-consumption retriever source is missing")
            retriever_event = retriever_matches[0]
            if (
                retriever_event.run_id != evidence.execution_run_id
                or retriever_event.payload["decision_hash"]
                != evidence.retriever_decision_hash
                or tuple(retriever_event.payload["selected_factor_spec_ids"])
                != evidence.selected_parent_factor_spec_ids
                or retriever_event.payload["control_evidence_event_hash"]
                != evidence.control_evidence_event_hash
                or provenance.get("treatment_policy_hash")
                != retriever_event.payload["policy_hash"]
                or provenance.get("train_snapshot_hash")
                != retriever_event.payload["data_snapshot_hash"]
            ):
                raise ValueError("generation-consumption retriever binding differs")
            bundle_refs = [
                reference for reference in retriever_event.payload["artifact_refs"]
                if reference["media_type"]
                == "application/vnd.vibe.retriever-input-bundle-v4+json"
            ]
            if len(bundle_refs) != 1:
                raise ValueError("generation-consumption retriever bundle is missing")
            bundle = RetrieverInputArtifactStoreV4(self.artifact_root).read(
                str(bundle_refs[0]["relative_path"]),
                str(retriever_event.payload["input_bundle_hash"]),
            )
            candidate_inputs = {
                item.factor_spec_id: item
                for item in bundle.retriever_input.candidates
            }
            for parent in evidence.selected_parents:
                source = candidate_inputs.get(str(parent["factor_spec_id"]))
                if source is None or (
                    build_expression_identity(str(parent["formula"])).canonical_ast
                    != source.canonical_ast
                ):
                    raise ValueError("generation-consumption parent AST differs")
            control_matches = [
                event for event in self.query_events(
                    event_type="OfficialSearchControlRecorded"
                )
                if event.event_hash == evidence.control_evidence_event_hash
            ]
            if len(control_matches) != 1:
                raise ValueError("generation-consumption control source is missing")
            from src.alpha_foundry.control_evidence import (
                OfficialSearchControlArtifactStoreV1,
            )

            control_event = control_matches[0]
            control_refs = [
                reference for reference in control_event.payload["artifact_refs"]
                if reference["media_type"]
                == OfficialSearchControlArtifactStoreV1.media_type
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
                seed_bank=SeedBank([
                    AlphaSeed(
                        seed_id=str(parent["factor_spec_id"]),
                        formula=str(parent["formula"]),
                        source="content-addressed:" + str(parent["source_hash"]),
                    )
                    for parent in evidence.selected_parents
                ]),
                mutator=SeedMutator(
                    max_candidates_per_seed=policy.max_candidates_per_seed
                ),
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
                str(event.payload["trial_id"]): event
                for event in all_events
                if event.event_type == "TrialStarted"
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
            raise EventValidationError(
                "Activation generation-consumption evidence is invalid"
            ) from exc
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
            "selected_parent_factor_spec_ids": list(
                evidence.selected_parent_factor_spec_ids
            ),
            "consumed_parent_factor_spec_ids": list(
                evidence.consumed_parent_factor_spec_ids
            ),
            "generated_candidate_count": len(evidence.generated_candidates),
            "candidate_budget": evidence.candidate_budget,
            "compute_budget": evidence.compute_budget,
            "source_failure_codes": list(evidence.source_failure_codes),
            "source_complete": evidence.source_complete,
            "evidence_hash": evidence.evidence_hash,
        }
        if any(payload[name] != value for name, value in expected.items()):
            raise EventValidationError(
                "Activation generation event differs from its source artifact"
            )

    @staticmethod
    def _validate_factor_definition_identity(
        event_type: str, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_registry_bootstrap_identity(
        event_type: str, payload: Mapping[str, Any]
    ) -> None:
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
        if event_type == "FactorDefinitionRecorded" and payload.get("metadata", {}).get(
            "identity_schema_version"
        ) == "factor_spec.v1":
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
            originating_trial_id = str(
                definition.get("metadata", {}).get("originating_trial_id", "")
            )
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
            self._validate_retriever_action_template_transition(
                conn, draft, payload
            )
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
                raise EventTransitionError(
                    "retriever v4 decision identity already exists"
                )
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
                raise EventTransitionError(
                    "retriever v4 discovery watermark must precede its control evidence"
                )
            control_terminal_rows = conn.execute(
                """
                SELECT seq, event_hash FROM research_events
                WHERE event_type = 'TrialTerminated'
                """
            ).fetchall()
            terminal_seq_by_hash = {
                str(row["event_hash"]): int(row["seq"])
                for row in control_terminal_rows
            }
            cited_control_terminals = tuple(
                str(item) for item in control_payload["terminal_event_hashes"]
            )
            if (
                len(cited_control_terminals) != len(set(cited_control_terminals))
                or any(
                    terminal_seq_by_hash.get(event_hash, -1)
                    <= int(watermark["seq"])
                    for event_hash in cited_control_terminals
                )
            ):
                raise EventTransitionError(
                    "retriever v4 discovery watermark includes control-arm outcomes"
                )
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
                raise EventTransitionError(
                    "retriever v5 decision identity already exists"
                )
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
                control is None
                or watermark is None
                or int(watermark["seq"]) >= int(control["seq"])
            ):
                raise EventTransitionError(
                    "retriever v5 watermark must precede official control"
                )
            control_payload = json.loads(str(control["payload"]))
            if (
                control_payload["evidence_hash"] != payload["control_evidence_hash"]
                or control_payload["policy_hash"] != payload["control_policy_hash"]
                or control_payload["output_hash"] != payload["official_output_hash"]
                or control_payload["data_snapshot_hash"]
                != payload["data_snapshot_hash"]
            ):
                raise EventTransitionError(
                    "retriever v5 official control binding differs"
                )
            action_hashes = tuple(payload["action_template_event_hashes"])
            components = tuple(payload["components"])
            if len(action_hashes) != len(components):
                raise EventTransitionError("retriever v5 actions and components differ")
            for action_event_hash, component in zip(
                action_hashes, components, strict=True
            ):
                row = conn.execute(
                    """
                    SELECT seq, run_id, payload FROM research_events
                    WHERE event_type = 'RetrieverActionTemplateFrozen'
                      AND event_hash = ?
                    """,
                    (action_event_hash,),
                ).fetchone()
                if row is None:
                    raise EventTransitionError(
                        "retriever v5 lacks a frozen action event"
                    )
                action_payload = json.loads(str(row["payload"]))
                if (
                    str(row["run_id"]) != draft.run_id
                    or not int(watermark["seq"]) < int(row["seq"]) < int(control["seq"])
                    or action_payload["execution_run_id"] != draft.run_id
                    or action_payload["eligible_event_watermark"]
                    != payload["eligible_event_watermark"]
                    or action_payload["data_snapshot_hash"]
                    != payload["data_snapshot_hash"]
                    or action_payload["retrieval_policy_hash"]
                    != payload["policy_hash"]
                    or action_payload["action_id"] != component["action_id"]
                    or action_payload["parent_factor_spec_id"]
                    != component["factor_spec_id"]
                    or action_payload["expected_motif"] != component["motif"]
                    or action_payload["identity_action"]
                ):
                    raise EventTransitionError(
                        "retriever v5 frozen action binding differs"
                    )
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
                raise EventTransitionError(
                    "Activation resource event run does not match its run group"
                )
            self._validate_activation_resource_transition(conn, payload)
            return
        if event_type == "ActivationGenerationConsumptionRecorded":
            if draft.run_id != payload["execution_run_id"]:
                raise EventTransitionError(
                    "Activation generation event run differs from execution run"
                )
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
                raise EventTransitionError(
                    "Activation generation lacks its retriever decision"
                )
            retriever_payload = json.loads(str(retriever["payload"]))
            if (
                str(retriever["run_id"]) != payload["execution_run_id"]
                or retriever_payload["decision_hash"]
                != payload["retriever_decision_hash"]
                or retriever_payload["control_evidence_event_hash"]
                != payload["control_evidence_event_hash"]
            ):
                raise EventTransitionError(
                    "Activation generation retriever binding differs"
                )
            prior = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationGenerationConsumptionRecorded'
                  AND (entity_id = ? OR json_extract(payload, '$.evidence_hash') = ?)
                """,
                (payload["generation_id"], payload["evidence_hash"]),
            ).fetchone()
            if prior is not None:
                raise EventTransitionError(
                    "Activation generation-consumption evidence already exists"
                )
            result = conn.execute(
                """
                SELECT 1 FROM research_events
                WHERE event_type = 'ActivationResultRecorded'
                  AND json_extract(payload, '$.plan_hash') = ?
                """,
                (payload["plan_hash"],),
            ).fetchone()
            if result is not None:
                raise EventTransitionError(
                    "Activation generation cannot append after result"
                )
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
                "SELECT 1 FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
                (trial_id,),
            ).fetchone()
            terminal = conn.execute(
                "SELECT 1 FROM research_events WHERE event_type = 'TrialTerminated' AND entity_id = ?",
                (trial_id,),
            ).fetchone()
            if started is None or terminal is not None:
                raise EventTransitionError(
                    "generation failure requires a prior active trial"
                )
            return
        if event_type not in {"TrialStarted", "EvaluationRecorded", "TrialTerminated"}:
            return
        trial_id = str(payload["trial_id"])
        if event_type in {"TrialStarted", "TrialTerminated"} and draft.entity_id != trial_id:
            raise EventValidationError("trial entity_id must equal payload trial_id")
        started = conn.execute(
            "SELECT event_hash FROM research_events WHERE event_type = 'TrialStarted' AND entity_id = ?",
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
        if event_type == "TrialTerminated" and payload["status"] == "success":
            evaluation_hash = payload["evaluation_event_hash"]
            if evaluation_hash is None:
                raise EventTransitionError("successful trial requires terminal evaluation evidence")
            evaluation = conn.execute(
                """
                SELECT payload FROM research_events
                WHERE event_type = 'EvaluationRecorded' AND event_hash = ?
                """,
                (evaluation_hash,),
            ).fetchone()
            if evaluation is None or json.loads(evaluation["payload"])["trial_id"] != trial_id:
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
            str(json.loads(str(row["payload"]))["metadata"].get("originating_trial_id", ""))
            == payload["trial_id"]
            for row in definitions
        ):
            raise EventTransitionError("process action v2 must be frozen before child generation")
        prior_actions = conn.execute(
            "SELECT payload FROM research_events WHERE event_type = 'ProcessActionFrozenV2' ORDER BY seq ASC"
        ).fetchall()
        if any(
            json.loads(str(row["payload"]))["trial_id"] == payload["trial_id"]
            for row in prior_actions
        ):
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
        if any(
            json.loads(str(row["payload"]))["action_id"] == payload["action_id"]
            for row in prior_outcomes
        ):
            raise EventTransitionError("process action v2 already has a terminal outcome")
        action = json.loads(str(action_row["payload"]))
        matching_fields = (
            "trial_id", "policy_hash", "utility_policy_hash", "data_snapshot_hash",
            "regime_config_hash", "run_group_id",
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
            raise EventTransitionError(
                "Retriever action envelope run differs from its execution run"
            )
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
        if (
            definition is None
            or str(definition["event_hash"])
            != payload["parent_definition_event_hash"]
        ):
            raise EventTransitionError(
                "Retriever action parent definition hash differs"
            )
        ResearchEventStore._validate_retriever_v2_transition(
            conn,
            {
                "eligible_event_watermark": payload["eligible_event_watermark"],
                "components": [
                    {"factor_spec_id": payload["parent_factor_spec_id"]}
                ],
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
            (int(row["seq"]), str(row["event_hash"]), json.loads(str(row["payload"])))
            for row in evaluations
        ]
        terminal_payloads = [
            (int(row["seq"]), str(row["event_hash"]), json.loads(str(row["payload"])))
            for row in terminals
        ]
        outcome_payloads = [
            (int(row["seq"]), json.loads(str(row["payload"]))) for row in outcomes
        ]
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
                if evaluation["factor_spec_id"] != factor_id or evaluation["data_scope"] not in {"valid", "train_valid"}:
                    continue
                for terminal_seq, terminal_hash, terminal in terminal_payloads:
                    if terminal_seq > watermark_seq:
                        continue
                    if terminal["trial_id"] != evaluation["trial_id"] or terminal["status"] not in {"success", "reject"}:
                        continue
                    directly_cited = terminal["evaluation_event_hash"] == evaluation_hash
                    outcome_cited = any(
                        outcome_seq <= watermark_seq
                        and
                        outcome["terminal_event_hash"] == terminal_hash
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
            WHERE event_type = 'ActivationResourceMeasured'
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
            raise EventTransitionError(
                "Activation run already has resource evidence"
            )
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
                raise EventTransitionError(
                    "outcome-free activation result must explicitly invalidate preflight"
                )
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
    def _validate_final_candidate_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_final_capability_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_final_access_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_final_artifact_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_forward_plan_v2_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
    def _validate_forward_observation_v2_transition(
        conn: sqlite3.Connection, payload: Mapping[str, Any]
    ) -> None:
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
        used_unit_hashes = {
            str(unit_hash)
            for _, look in prior
            for unit_hash in look["unit_hashes"]
        }
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
            expected_information = int(previous["information_time"]) + int(
                payload["incremental_information"]
            )
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
        referenced_result_hashes = {
            str(item) for item in payload["source_result_hashes"]
        }
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
            available_result_hashes.update(
                str(reference["artifact_hash"])
                for reference in result["artifact_refs"]
            )
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
    def _validate_sequential_mixture_evidence(
        protocol: Mapping[str, Any], payload: Mapping[str, Any]
    ) -> None:
        weights = tuple(float(value) for value in protocol["mixture_weights"])
        support = tuple(float(value) for value in payload["support_component_log_capitals"])
        contradiction = tuple(
            float(value) for value in payload["contradiction_component_log_capitals"]
        )
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
    def _mechanism_ordinal_state(
        source_outcomes: Mapping[str, str], decisive_event_hashes: set[str]
    ) -> str:
        decisive_outcomes = [
            outcome
            for event_hash, outcome in source_outcomes.items()
            if event_hash in decisive_event_hashes
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
        row = conn.execute(
            "SELECT event_hash FROM research_events ORDER BY seq DESC LIMIT 1"
        ).fetchone()
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
            idempotency_key=(
                None if row["idempotency_key"] is None else str(row["idempotency_key"])
            ),
            previous_event_hash=(
                None if row["previous_event_hash"] is None else str(row["previous_event_hash"])
            ),
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
                self._validate_external_quality_decision_evidence(
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
                self._validate_external_activation_resource(
                    event.event_type,
                    validated_payload,
                )
                self._validate_external_generation_consumption(
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
        started: set[str] = set()
        terminated: set[str] = set()
        evaluations: dict[str, str] = {}
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
        retriever_v5_ids: set[str] = set()
        process_outcome_links: list[tuple[str, str, str]] = []
        retriever_v4_events: dict[str, ResearchEventEnvelope] = {}
        activation_results: dict[str, Mapping[str, Any]] = {}
        activation_result_plans: set[str] = set()
        activation_decision_plans: set[str] = set()
        event_order = {
            event.event_hash: index for index, event in enumerate(events)
        }
        for event in events:
            payload = event.payload
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
                started.add(trial_id)
            elif event.event_type == "OfficialSearchControlRecorded":
                control_id = str(payload["control_id"])
                if control_id in official_control_ids:
                    return False
                official_control_ids.add(control_id)
            elif event.event_type == "GenerationFailureRecorded":
                trial_id = str(payload["trial_id"])
                if trial_id not in started or trial_id in terminated:
                    return False
            elif event.event_type == "EvaluationRecorded":
                trial_id = str(payload["trial_id"])
                if trial_id not in started or trial_id in terminated:
                    return False
                evaluations[event.event_hash] = trial_id
                evaluation_payloads[event.event_hash] = payload
            elif event.event_type == "TrialTerminated":
                trial_id = str(payload["trial_id"])
                if trial_id not in started or trial_id in terminated:
                    return False
                if payload["status"] == "success":
                    evaluation_hash = payload["evaluation_event_hash"]
                    if evaluations.get(str(evaluation_hash)) != trial_id:
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
                    or event_order.get(str(definition_hash), current_index)
                    > watermark_index
                ):
                    return False
                eligible = False
                for evaluation_hash, candidate_evaluation in evaluation_payloads.items():
                    if (
                        event_order[evaluation_hash] > watermark_index
                        or candidate_evaluation["factor_spec_id"] != factor_id
                        or candidate_evaluation["data_scope"]
                        not in {"valid", "train_valid"}
                    ):
                        continue
                    for terminal_hash, candidate_terminal in terminal_payloads.items():
                        if (
                            event_order[terminal_hash] > watermark_index
                            or candidate_terminal["trial_id"]
                            != candidate_evaluation["trial_id"]
                            or candidate_terminal["status"]
                            not in {"success", "reject"}
                        ):
                            continue
                        directly_cited = (
                            candidate_terminal["evaluation_event_hash"]
                            == evaluation_hash
                        )
                        outcome_cited = any(
                            event_order[outcome_hash] <= watermark_index
                            and cited_terminal == terminal_hash
                            and cited_evaluation == evaluation_hash
                            for outcome_hash, cited_terminal, cited_evaluation
                            in process_outcome_links
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
                action_hashes = tuple(
                    str(item) for item in payload["action_template_event_hashes"]
                )
                components = tuple(payload["components"])
                action_events = tuple(
                    retriever_action_events.get(action_hash)
                    for action_hash in action_hashes
                )
                if (
                    decision_id in retriever_v5_ids
                    or control_event is None
                    or len(action_hashes) != len(components)
                    or any(action is None for action in action_events)
                ):
                    return False
                watermark_index = event_order.get(
                    str(payload["eligible_event_watermark"]), -1
                )
                control_index = event_order[control_event.event_hash]
                for action_event, component in zip(
                    action_events, components, strict=True
                ):
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
                        or action_payload["parent_factor_spec_id"]
                        != component["factor_spec_id"]
                        or action_payload["expected_motif"] != component["motif"]
                        or action_payload["identity_action"]
                    ):
                        return False
                retriever_v5_ids.add(decision_id)
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
                    ResearchEventStore._validate_sequential_mixture_evidence(
                        protocol, payload
                    )
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
                    look["block_id"] == payload["block_id"]
                    or look["block_hash"] == payload["block_hash"]
                    for _, look in prior
                ):
                    return False
                used_units = {
                    str(unit_hash)
                    for _, look in prior
                    for unit_hash in look["unit_hashes"]
                }
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
                        != int(previous["information_time"])
                        + int(payload["incremental_information"])
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
                    str(reference["artifact_hash"])
                    for reference in payload["artifact_refs"]
                }
            elif event.event_type == "MechanismEvidenceIndexRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                source_events = tuple(str(item) for item in payload["source_event_hashes"])
                if any(result_factors.get(source) != factor_spec_id for source in source_events):
                    return False
                if any(
                    result_policies.get(source) != payload["policy_hash"]
                    for source in source_events
                ):
                    return False
                available_hashes = {
                    artifact_hash
                    for source in source_events
                    for artifact_hash in result_artifact_hashes.get(source, set())
                }
                if not set(str(item) for item in payload["source_result_hashes"]).issubset(
                    available_hashes
                ):
                    return False
                source_outcomes = {
                    source: result_outcomes[source]
                    for source in source_events
                    if source in result_outcomes
                }
                decisive = {str(item) for item in payload["decisive_event_hashes"]}
                if (
                    ResearchEventStore._mechanism_ordinal_state(source_outcomes, decisive)
                    != payload["ordinal_state"]
                    or canonical_json_hash(
                        ResearchEventStore._mechanism_index_content(payload)
                    )
                    != payload["mei_hash"]
                ):
                    return False
            elif event.event_type == "ComplementEvidenceRecorded":
                factor_spec_id = str(payload["factor_spec_id"])
                evaluation = evaluation_payloads.get(
                    str(payload["source_evaluation_event_hash"])
                )
                terminal = terminal_payloads.get(
                    str(payload["source_terminal_event_hash"])
                )
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
                    terminal["evaluation_event_hash"]
                    != payload["source_evaluation_event_hash"]
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
                    or str(payload["summary_manifest_hash"])
                    not in activation_manifest_hashes
                    or audit_id in activation_source_audit_ids
                ):
                    return False
                activation_source_audit_ids.add(audit_id)
            elif event.event_type == "ActivationResourceMeasured":
                plan_hash = str(payload["plan_hash"])
                resource_id = str(payload["resource_id"])
                evidence_hash = str(payload["evidence_hash"])
                manifest_hash = str(payload["manifest_hash"])
                matching_runs = [
                    run for run in activation_runs.get(plan_hash, [])
                    if str(run["manifest_hash"]) == manifest_hash
                    and run["run_group_id"] == payload["run_group_id"]
                    and run["pair_id"] == payload["pair_id"]
                    and run["arm"] == payload["arm"]
                ]
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
                retriever_source = retriever_v4_events.get(
                    str(payload["retriever_decision_event_hash"])
                )
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
                        1 for pair_arms in arms.values()
                        if pair_arms == {"control", "treatment"}
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
                    plan["phase"] != "confirmatory"
                    or not result["replayable"]
                    or result["invalidation_reasons"]
                ):
                    return False
                activation_decision_plans.add(plan_hash)
            elif event.event_type in {"QualityDecisionV2Recorded", "QualityDecisionV3Recorded"}:
                if str(payload["factor_spec_id"]) not in definitions:
                    return False
            elif event.event_type == "FinalCandidateFrozen":
                factor_spec_id = str(payload["factor_spec_id"])
                candidate_hash = str(payload["candidate_hash"])
                if (
                    factor_spec_id not in definitions
                    or definition_event_hashes.get(factor_spec_id)
                    != payload["definition_hash"]
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
                    or any(
                        item["candidate_hash"] == candidate_hash
                        for item in final_capabilities.values()
                    )
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
                        or payload["previous_observation_hash"]
                        != previous["observation_hash"]
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
        started = {
            str(event.payload["trial_id"])
            for event in events
            if event.event_type == "TrialStarted"
        }
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
            raise ResearchEventAppendError(
                "cannot issue an event subsequence from an empty or invalid chain"
            )
        by_hash = {event.event_hash: (index, event) for index, event in enumerate(full)}
        prior_index = -1
        normalized: list[ResearchEventEnvelope] = []
        seen: set[str] = set()
        for event in selected:
            source = by_hash.get(event.event_hash)
            if source is None or source[0] <= prior_index or event.event_hash in seen:
                raise EventValidationError(
                    "selected events are not a unique ordered full-chain subsequence"
                )
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
        matches = [
            index for index, event in enumerate(full)
            if event.event_hash == watermark_event_hash
        ]
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
            raise ResearchEventAppendError(
                "cannot issue an event subsequence from an invalid historical prefix"
            )
        by_hash = {event.event_hash: (index, event) for index, event in enumerate(prefix)}
        prior_index = -1
        normalized: list[ResearchEventEnvelope] = []
        seen: set[str] = set()
        for event in selected:
            source = by_hash.get(event.event_hash)
            if source is None or source[0] <= prior_index or event.event_hash in seen:
                raise EventValidationError(
                    "selected events are not an ordered historical-prefix subsequence"
                )
            if source[1].to_dict() != event.to_dict():
                raise EventValidationError(
                    "selected historical event differs from its prefix source"
                )
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
