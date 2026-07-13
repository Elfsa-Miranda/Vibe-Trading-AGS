"""Closed producer authority for fixed-horizon falsification evidence."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, ClassVar, Literal, Mapping, Sequence, cast

import numpy as np
from scipy.stats import norm

from src.alpha_quality.falsification.equivalence import tost_from_summary
from src.alpha_quality.falsification.multiplicity import MultiplicityMethod, adjust
from src.research_ledger.events import EventDraft, ResearchEventStore
from src.research_ledger.events.artifacts import (
    AtomicContentAddressedArtifactWriter,
    validate_artifact_references,
)
from src.research_ledger.events.model import ResearchEventEnvelope
from src.research_ledger.hash_utils import canonical_json_hash, utc_now_iso

PRODUCER_SCHEMA_VERSION = "falsification_authority.v2"
PRODUCER_POLICY_HASH = canonical_json_hash(
    {
        "schema_version": "falsification_authority_policy.v2",
        "contract": "closed_sorted_unique_test_specs",
        "source": "producer_dated_raw_observations",
        "statistics": "executor_recomputed",
        "equivalence": "tost_confidence_interval",
        "fixed_sequential_isolation": True,
        "legacy_v1": "read_only_inconclusive_research_only",
    }
)
SOURCE_MEDIA_TYPE = "application/vnd.vibe.falsification-source-v2+json"
RESULT_MEDIA_TYPE = "application/vnd.vibe.falsification-result-v2+json"

Prediction = Literal["positive", "negative", "equivalent"]
DependenceMethod = Literal["iid", "hac"]
FamilyOutcome = Literal["falsified", "inconclusive", "partial_support", "supported"]


def _require_hash(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise ValueError(f"{field} must be a canonical sha256 hash")
    int(value[7:], 16)


@dataclass(frozen=True)
class TestCapabilityV2:
    __test__: ClassVar[bool] = False
    capability_id: str
    capability_version: str
    predictions: tuple[Prediction, ...]
    statistics: tuple[Literal["mean"], ...]
    dependence_methods: tuple[DependenceMethod, ...]
    maximum_observations: int

    def __post_init__(self) -> None:
        if (
            not self.capability_id
            or not self.capability_version
            or not self.predictions
            or not self.statistics
            or not self.dependence_methods
            or self.maximum_observations < 2
        ):
            raise ValueError("invalid falsification capability")

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "capability_version": self.capability_version,
            "predictions": list(self.predictions),
            "statistics": list(self.statistics),
            "dependence_methods": list(self.dependence_methods),
            "maximum_observations": self.maximum_observations,
        }


@dataclass(frozen=True)
class FalsificationTestCatalogV2:
    catalog_version: str
    capabilities: tuple[TestCapabilityV2, ...]

    def __post_init__(self) -> None:
        ids = [item.capability_id for item in self.capabilities]
        if not self.catalog_version or not ids or ids != sorted(ids) or len(ids) != len(set(ids)):
            raise ValueError("catalog capabilities must be non-empty, sorted, and unique")

    @property
    def catalog_hash(self) -> str:
        return str(canonical_json_hash(self.to_dict()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "falsification_test_catalog.v2",
            "catalog_version": self.catalog_version,
            "capabilities": [item.to_dict() for item in self.capabilities],
        }

    def capability(self, capability_id: str) -> TestCapabilityV2:
        try:
            return next(item for item in self.capabilities if item.capability_id == capability_id)
        except StopIteration as exc:
            raise ValueError("test capability is not registered") from exc


@dataclass(frozen=True)
class TestSpecV2:
    __test__: ClassVar[bool] = False
    test_id: str
    capability_id: str
    estimand: str
    prediction: Prediction
    sesoi: float
    units: str
    sample_unit: str
    minimum_effective_n: int
    statistic: Literal["mean"]
    dependence_method: DependenceMethod
    dependence_lag: int
    negative_control_test_id: str | None
    decisive: bool
    stopping_rule: Literal["fixed"] = "fixed"
    maximum_looks: Literal[1] = 1

    def __post_init__(self) -> None:
        if (
            not self.test_id
            or not self.capability_id
            or not self.estimand
            or not self.units
            or not self.sample_unit
            or not math.isfinite(self.sesoi)
            or self.sesoi <= 0
            or self.minimum_effective_n < 2
            or self.dependence_lag < 0
            or self.maximum_looks != 1
            or self.stopping_rule != "fixed"
        ):
            raise ValueError("invalid closed fixed-horizon test specification")
        if self.prediction == "equivalent" and self.sesoi <= 0:
            raise ValueError("equivalence requires a positive frozen margin")

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_id": self.test_id,
            "capability_id": self.capability_id,
            "estimand": self.estimand,
            "prediction": self.prediction,
            "sesoi": self.sesoi,
            "units": self.units,
            "sample_unit": self.sample_unit,
            "minimum_effective_n": self.minimum_effective_n,
            "statistic": self.statistic,
            "dependence_method": self.dependence_method,
            "dependence_lag": self.dependence_lag,
            "negative_control_test_id": self.negative_control_test_id,
            "decisive": self.decisive,
            "stopping_rule": self.stopping_rule,
            "maximum_looks": self.maximum_looks,
        }


@dataclass(frozen=True)
class FalsificationContractV2:
    factor_spec_id: str
    mechanism_claim: str
    null_hypothesis: str
    alternative_hypothesis: str
    observable_implication: str
    family_id: str
    multiplicity_method: MultiplicityMethod
    error_target: float
    conditioning_hash: str
    regime_hash: str
    data_scope_hash: str
    catalog_hash: str
    policy_hash: str
    data_access_cutoff: str
    tests: tuple[TestSpecV2, ...]

    def __post_init__(self) -> None:
        ids = [item.test_id for item in self.tests]
        if (
            not self.factor_spec_id
            or not self.mechanism_claim
            or not self.null_hypothesis
            or not self.alternative_hypothesis
            or not self.observable_implication
            or not self.family_id
            or not 0 < self.error_target < 0.5
            or not ids
            or ids != sorted(ids)
            or len(ids) != len(set(ids))
        ):
            raise ValueError("contract tests must be non-empty, sorted, and unique")
        for field, value in (
            ("conditioning_hash", self.conditioning_hash),
            ("regime_hash", self.regime_hash),
            ("data_scope_hash", self.data_scope_hash),
            ("catalog_hash", self.catalog_hash),
            ("policy_hash", self.policy_hash),
        ):
            _require_hash(value, field)
        controls = {item.negative_control_test_id for item in self.tests if item.negative_control_test_id is not None}
        if not controls.issubset(set(ids)) or any(item.test_id == item.negative_control_test_id for item in self.tests):
            raise ValueError("negative controls must reference another frozen test")

    @property
    def contract_hash(self) -> str:
        return str(canonical_json_hash(self.to_dict()))

    @property
    def contract_id(self) -> str:
        return "falsification-v2-contract-" + self.contract_hash[7:31]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "falsification_contract.v2",
            "factor_spec_id": self.factor_spec_id,
            "mechanism_claim": self.mechanism_claim,
            "null_hypothesis": self.null_hypothesis,
            "alternative_hypothesis": self.alternative_hypothesis,
            "observable_implication": self.observable_implication,
            "family_id": self.family_id,
            "multiplicity_method": self.multiplicity_method,
            "error_target": self.error_target,
            "conditioning_hash": self.conditioning_hash,
            "regime_hash": self.regime_hash,
            "data_scope_hash": self.data_scope_hash,
            "catalog_hash": self.catalog_hash,
            "policy_hash": self.policy_hash,
            "data_access_cutoff": self.data_access_cutoff,
            "tests": [item.to_dict() for item in self.tests],
        }


def validate_contract_v2(contract: FalsificationContractV2, catalog: FalsificationTestCatalogV2) -> None:
    if contract.catalog_hash != catalog.catalog_hash:
        raise ValueError("contract catalog snapshot differs")
    for spec in contract.tests:
        capability = catalog.capability(spec.capability_id)
        if (
            spec.prediction not in capability.predictions
            or spec.statistic not in capability.statistics
            or spec.dependence_method not in capability.dependence_methods
            or spec.minimum_effective_n > capability.maximum_observations
        ):
            raise ValueError(f"test spec exceeds capability: {spec.test_id}")
    if contract.multiplicity_method == "bh":
        raise ValueError("BH requires an explicit PRDS justification not present in v2")


@dataclass(frozen=True)
class DatedObservationV2:
    observed_on: str
    value: float

    def __post_init__(self) -> None:
        date.fromisoformat(self.observed_on)
        if not math.isfinite(self.value):
            raise ValueError("falsification observations must be finite")

    def to_dict(self) -> dict[str, Any]:
        return {"observed_on": self.observed_on, "value": self.value}


class FalsificationContractAuthorityV2:
    def __init__(self, *, store: ResearchEventStore, catalog: FalsificationTestCatalogV2) -> None:
        if not store.flags.enabled("VIBE_TRADING_FALSIFICATION_CONTRACT"):
            raise RuntimeError("falsification authority v2 is disabled")
        self.store = store
        self.catalog = catalog

    def register_catalog(self, *, run_id: str) -> ResearchEventEnvelope:
        prior = self.store.query_events(event_type="FalsificationCatalogV2Registered")
        for event in prior:
            if event.payload["catalog_hash"] == self.catalog.catalog_hash:
                return event
        content = self.catalog.to_dict()
        return self.store._append_producer_event(
            EventDraft(
                event_type="FalsificationCatalogV2Registered",
                entity_id=self.catalog.catalog_hash,
                run_id=run_id,
                payload_schema_version="falsification_catalog_registered.v2",
                idempotency_key="falsification-v2-catalog:" + self.catalog.catalog_hash,
                payload={
                    "catalog_hash": self.catalog.catalog_hash,
                    "catalog": content,
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )

    def register(self, contract: FalsificationContractV2, *, run_id: str) -> ResearchEventEnvelope:
        validate_contract_v2(contract, self.catalog)
        catalog_event = self.register_catalog(run_id=run_id)
        if self.store.query_events(
            event_type="FalsificationOutcomeAccessV2Recorded", entity_id=contract.factor_spec_id
        ):
            raise ValueError("outcome data was accessed before contract registration")
        prior = self.store.query_events(event_type="FalsificationContractV2Registered", entity_id=contract.contract_id)
        if prior:
            if prior[-1].payload["contract_hash"] != contract.contract_hash:
                raise ValueError("contract identity conflict")
            return prior[-1]
        return self.store._append_producer_event(
            EventDraft(
                event_type="FalsificationContractV2Registered",
                entity_id=contract.contract_id,
                run_id=run_id,
                payload_schema_version="falsification_contract_registered.v2",
                idempotency_key="falsification-v2-contract:" + contract.contract_hash,
                payload={
                    "contract_id": contract.contract_id,
                    "contract_hash": contract.contract_hash,
                    "factor_spec_id": contract.factor_spec_id,
                    "family_id": contract.family_id,
                    "catalog_event_hash": catalog_event.event_hash,
                    "catalog_hash": self.catalog.catalog_hash,
                    "contract": contract.to_dict(),
                    "registered_at": utc_now_iso(),
                    "source_event_hashes": [catalog_event.event_hash],
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [],
                },
            )
        )


class FalsificationSourceProducerV2:
    _source_keys = frozenset({"schema_version", "contract_hash", "tests", "source_hash"})

    def __init__(self, *, store: ResearchEventStore) -> None:
        if not store.flags.enabled("VIBE_TRADING_FALSIFICATION_CONTRACT"):
            raise RuntimeError("falsification source producer v2 is disabled")
        self.store = store
        self.writer = AtomicContentAddressedArtifactWriter(store.artifact_root, max_bytes=8 * 1024**2)

    def produce(
        self,
        contract_event_hash: str,
        observations: Mapping[str, Sequence[DatedObservationV2]],
        *,
        run_id: str,
    ) -> ResearchEventEnvelope:
        contract_event = self._contract_event(contract_event_hash, run_id)
        contract = cast(Mapping[str, Any], contract_event.payload["contract"])
        expected_ids = [str(item["test_id"]) for item in cast(Sequence[Mapping[str, Any]], contract["tests"])]
        if sorted(observations) != expected_ids:
            raise ValueError("source tests differ from frozen contract")
        normalized: dict[str, list[dict[str, Any]]] = {}
        for test_id in expected_ids:
            rows = list(observations[test_id])
            dates = [row.observed_on for row in rows]
            if not rows or dates != sorted(dates) or len(dates) != len(set(dates)):
                raise ValueError("source observations must be non-empty, date-sorted, and unique")
            normalized[test_id] = [row.to_dict() for row in rows]
        access_content = {
            "contract_event_hash": contract_event_hash,
            "contract_hash": contract_event.payload["contract_hash"],
            "factor_spec_id": contract_event.payload["factor_spec_id"],
            "test_ids": expected_ids,
        }
        access_hash = canonical_json_hash(access_content)
        existing_access = [
            event
            for event in self.store.query_events(event_type="FalsificationOutcomeAccessV2Recorded")
            if event.payload["contract_hash"] == contract_event.payload["contract_hash"]
        ]
        access = (
            existing_access[-1]
            if existing_access
            else self.store._append_producer_event(
                EventDraft(
                    event_type="FalsificationOutcomeAccessV2Recorded",
                    entity_id=str(contract_event.payload["factor_spec_id"]),
                    run_id=run_id,
                    payload_schema_version="falsification_outcome_access_recorded.v2",
                    idempotency_key="falsification-v2-access:" + str(contract_event.payload["contract_hash"]),
                    payload={
                        "access_id": "falsification-v2-access-" + access_hash[7:31],
                        "access_hash": access_hash,
                        **access_content,
                        "accessed_at": utc_now_iso(),
                        "source_event_hashes": [contract_event_hash],
                        "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                        "producer_policy_hash": PRODUCER_POLICY_HASH,
                        "artifact_refs": [],
                    },
                )
            )
        )
        source_content = {
            "schema_version": "falsification_source.v2",
            "contract_hash": contract_event.payload["contract_hash"],
            "tests": normalized,
        }
        source_hash = canonical_json_hash(source_content)
        artifact = self.writer.write_json(
            namespace="falsification_source_v2",
            payload={**source_content, "source_hash": source_hash},
            schema_version="falsification_source.v2",
            semantic_hash_field="source_hash",
            closed_keys=self._source_keys,
            media_type=SOURCE_MEDIA_TYPE,
        )
        existing_source = [
            event
            for event in self.store.query_events(event_type="FalsificationSourceArtifactV2Recorded")
            if event.payload["source_hash"] == source_hash
        ]
        if existing_source:
            return existing_source[-1]
        return self.store._append_producer_event(
            EventDraft(
                event_type="FalsificationSourceArtifactV2Recorded",
                entity_id="falsification-v2-source-" + source_hash[7:31],
                run_id=run_id,
                payload_schema_version="falsification_source_artifact_recorded.v2",
                idempotency_key="falsification-v2-source:" + source_hash,
                payload={
                    "source_id": "falsification-v2-source-" + source_hash[7:31],
                    "source_hash": source_hash,
                    "contract_event_hash": contract_event_hash,
                    "contract_hash": contract_event.payload["contract_hash"],
                    "factor_spec_id": contract_event.payload["factor_spec_id"],
                    "access_event_hash": access.event_hash,
                    "test_ids": expected_ids,
                    "source_event_hashes": sorted([contract_event_hash, access.event_hash]),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )

    def _contract_event(self, event_hash: str, run_id: str) -> ResearchEventEnvelope:
        events = [
            event
            for event in self.store.query_events(event_type="FalsificationContractV2Registered")
            if event.event_hash == event_hash and event.run_id == run_id
        ]
        if len(events) != 1:
            raise ValueError("source producer requires an exact registered contract")
        return events[0]


def _standard_error(values: np.ndarray, method: DependenceMethod, lag: int) -> float:
    count = len(values)
    if count < 2:
        return 0.0
    centered = values - float(values.mean())
    if method == "iid":
        return float(values.std(ddof=1) / math.sqrt(count))
    bounded_lag = min(lag, count - 1)
    long_run = float(np.dot(centered, centered) / count)
    for offset in range(1, bounded_lag + 1):
        covariance = float(np.dot(centered[offset:], centered[:-offset]) / count)
        long_run += 2.0 * (1.0 - offset / (bounded_lag + 1.0)) * covariance
    return math.sqrt(max(long_run, 0.0) / count)


def _directional_p(estimate: float, standard_error: float, prediction: Prediction) -> float:
    if standard_error <= 0:
        if estimate == 0:
            return 1.0
        expected = estimate > 0 if prediction == "positive" else estimate < 0
        return 0.0 if expected else 1.0
    z_value = estimate / standard_error
    return float(norm.sf(z_value) if prediction == "positive" else norm.cdf(z_value))


class FalsificationExecutorV2:
    _result_keys = frozenset(
        {
            "schema_version",
            "contract_hash",
            "source_hash",
            "executor_policy_hash",
            "test_results",
            "family_result",
            "result_hash",
        }
    )

    def __init__(self, *, store: ResearchEventStore) -> None:
        if not store.flags.enabled("VIBE_TRADING_FALSIFICATION_CONTRACT"):
            raise RuntimeError("falsification executor v2 is disabled")
        self.store = store
        self.writer = AtomicContentAddressedArtifactWriter(store.artifact_root, max_bytes=8 * 1024**2)

    def execute(self, contract_event_hash: str, source_event_hash: str, *, run_id: str) -> ResearchEventEnvelope:
        contract_event = self._event("FalsificationContractV2Registered", contract_event_hash, run_id)
        source_event = self._event("FalsificationSourceArtifactV2Recorded", source_event_hash, run_id)
        if source_event.payload["contract_event_hash"] != contract_event_hash:
            raise ValueError("source does not belong to the frozen contract")
        result_content = self.recompute(contract_event_hash, source_event_hash, run_id=run_id)
        test_results = cast(list[dict[str, Any]], result_content["test_results"])
        family_result = cast(dict[str, Any], result_content["family_result"])
        result_hash = canonical_json_hash(result_content)
        artifact = self.writer.write_json(
            namespace="falsification_result_v2",
            payload={**result_content, "result_hash": result_hash},
            schema_version="falsification_result.v2",
            semantic_hash_field="result_hash",
            closed_keys=self._result_keys,
            media_type=RESULT_MEDIA_TYPE,
        )
        result_id = "falsification-v2-result-" + result_hash[7:31]
        return self.store._append_producer_event(
            EventDraft(
                event_type="FalsificationResultV2Recorded",
                entity_id=result_id,
                run_id=run_id,
                payload_schema_version="falsification_result_recorded.v2",
                idempotency_key="falsification-v2-result:" + str(contract_event.payload["contract_hash"]),
                payload={
                    "result_id": result_id,
                    "result_hash": result_hash,
                    "contract_event_hash": contract_event_hash,
                    "contract_hash": contract_event.payload["contract_hash"],
                    "source_event_hash": source_event_hash,
                    "source_hash": source_event.payload["source_hash"],
                    "outcome_access_event_hash": source_event.payload["access_event_hash"],
                    "factor_spec_id": contract_event.payload["factor_spec_id"],
                    "family_id": contract_event.payload["family_id"],
                    "outcome": family_result["outcome"],
                    "test_results": test_results,
                    "family_result": family_result,
                    "legacy_promotion_cap": None,
                    "source_event_hashes": sorted(
                        [contract_event_hash, source_event_hash, str(source_event.payload["access_event_hash"])]
                    ),
                    "producer_schema_version": PRODUCER_SCHEMA_VERSION,
                    "producer_policy_hash": PRODUCER_POLICY_HASH,
                    "artifact_refs": [artifact.reference()],
                },
            )
        )

    def recompute(
        self,
        contract_event_hash: str,
        source_event_hash: str,
        *,
        run_id: str,
    ) -> dict[str, Any]:
        contract_event = self._event("FalsificationContractV2Registered", contract_event_hash, run_id)
        source_event = self._event("FalsificationSourceArtifactV2Recorded", source_event_hash, run_id)
        if source_event.payload["contract_event_hash"] != contract_event_hash:
            raise ValueError("source does not belong to the frozen contract")
        source = self._read_source(source_event)
        contract = cast(Mapping[str, Any], contract_event.payload["contract"])
        tests = self._compute_tests(contract, source)
        family = self._compute_family(contract, tests)
        return {
            "schema_version": "falsification_result.v2",
            "contract_hash": contract_event.payload["contract_hash"],
            "source_hash": source_event.payload["source_hash"],
            "executor_policy_hash": PRODUCER_POLICY_HASH,
            "test_results": tests,
            "family_result": family,
        }

    def replay(self, result_event_hash: str) -> dict[str, Any]:
        result_event = self._event_any_run("FalsificationResultV2Recorded", result_event_hash)
        content = self.recompute(
            str(result_event.payload["contract_event_hash"]),
            str(result_event.payload["source_event_hash"]),
            run_id=result_event.run_id,
        )
        if canonical_json_hash(content) != result_event.payload["result_hash"]:
            raise ValueError("falsification result does not replay")
        return content

    def _compute_tests(self, contract: Mapping[str, Any], source: Mapping[str, Any]) -> list[dict[str, Any]]:
        source_tests = cast(Mapping[str, Sequence[Mapping[str, Any]]], source["tests"])
        raw: list[dict[str, Any]] = []
        directional_p: list[float] = []
        directional_indexes: list[int] = []
        for spec in cast(Sequence[Mapping[str, Any]], contract["tests"]):
            values = np.asarray([float(row["value"]) for row in source_tests[str(spec["test_id"])]], dtype=float)
            estimate = float(values.mean())
            standard_error = _standard_error(
                values,
                cast(DependenceMethod, spec["dependence_method"]),
                int(spec["dependence_lag"]),
            )
            result: dict[str, Any] = {
                "test_id": spec["test_id"],
                "prediction": spec["prediction"],
                "decisive": spec["decisive"],
                "effective_n": len(values),
                "minimum_effective_n": spec["minimum_effective_n"],
                "estimate": estimate,
                "standard_error": standard_error,
                "sesoi": spec["sesoi"],
                "dependence_method": spec["dependence_method"],
                "dependence_lag": spec["dependence_lag"],
                "raw_p_value": None,
                "adjusted_p_value": None,
                "confidence_interval": None,
                "status": "inconclusive",
                "reason_codes": [],
            }
            if len(values) < int(spec["minimum_effective_n"]):
                result["reason_codes"] = ["LOW_POWER"]
            elif spec["prediction"] == "equivalent":
                equivalence = tost_from_summary(
                    estimate=estimate,
                    standard_error=standard_error,
                    margin=float(spec["sesoi"]),
                    alpha=float(contract["error_target"]),
                    effective_n=len(values),
                    minimum_effective_n=int(spec["minimum_effective_n"]),
                )
                result["raw_p_value"] = max(equivalence.lower_p_value, equivalence.upper_p_value)
                result["confidence_interval"] = list(equivalence.confidence_interval)
                result["status"] = "support" if equivalence.equivalent else "inconclusive"
                result["reason_codes"] = list(equivalence.reason_codes)
            else:
                raw_p = _directional_p(estimate, standard_error, cast(Prediction, spec["prediction"]))
                result["raw_p_value"] = raw_p
                directional_indexes.append(len(raw))
                directional_p.append(raw_p)
            raw.append(result)
        adjusted = adjust(directional_p, cast(MultiplicityMethod, contract["multiplicity_method"]))
        alpha = float(contract["error_target"])
        for index, adjusted_p in zip(directional_indexes, adjusted, strict=True):
            result = raw[index]
            result["adjusted_p_value"] = adjusted_p
            expected_sign = 1 if result["prediction"] == "positive" else -1
            observed_sign = 1 if result["estimate"] > 0 else (-1 if result["estimate"] < 0 else 0)
            large = abs(float(result["estimate"])) >= float(result["sesoi"])
            if adjusted_p <= alpha and large and observed_sign == expected_sign:
                result["status"] = "support"
            elif adjusted_p <= alpha and large and observed_sign == -expected_sign:
                result["status"] = "contradiction"
                result["reason_codes"] = ["MECHANISM_CONTRADICTION"]
            elif adjusted_p <= alpha and not large:
                result["reason_codes"] = ["SESOI_NOT_MET"]
            else:
                result["reason_codes"] = ["EVIDENCE_INCONCLUSIVE"]
        return raw

    @staticmethod
    def _compute_family(contract: Mapping[str, Any], tests: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        decisive = [item for item in tests if bool(item["decisive"])]
        contradictions = [item for item in decisive if item["status"] == "contradiction"]
        supports = [item for item in tests if item["status"] == "support"]
        if contradictions:
            outcome: FamilyOutcome = "falsified"
            cap = None
        elif not decisive or any(item["status"] == "inconclusive" for item in decisive):
            outcome = "inconclusive"
            cap = "research_only"
        elif len(supports) == len(tests):
            outcome = "supported"
            cap = None
        elif supports:
            outcome = "partial_support"
            cap = None
        else:
            outcome = "inconclusive"
            cap = "research_only"
        return {
            "family_id": contract["family_id"],
            "outcome": outcome,
            "cap": cap,
            "multiplicity_method": contract["multiplicity_method"],
            "error_target": contract["error_target"],
            "test_count": len(tests),
            "decisive_count": len(decisive),
        }

    def _read_source(self, event: ResearchEventEnvelope) -> Mapping[str, Any]:
        references = validate_artifact_references(self.store.artifact_root, event.payload["artifact_refs"])
        if len(references) != 1 or references[0]["media_type"] != SOURCE_MEDIA_TYPE:
            raise ValueError("source artifact reference is invalid")
        path = Path(self.store.artifact_root, *references[0]["relative_path"].split("/"))
        payload = json.loads(path.read_text(encoding="utf-8"))
        content = {key: value for key, value in payload.items() if key != "source_hash"}
        if (
            set(payload) != FalsificationSourceProducerV2._source_keys
            or canonical_json_hash(content) != payload["source_hash"]
        ):
            raise ValueError("source artifact semantic hash differs")
        if payload["source_hash"] != event.payload["source_hash"]:
            raise ValueError("source event and artifact differ")
        return cast(Mapping[str, Any], payload)

    def _event(self, event_type: str, event_hash: str, run_id: str) -> ResearchEventEnvelope:
        event = self._event_any_run(event_type, event_hash)
        if event.run_id != run_id:
            raise ValueError("falsification authority run differs")
        return event

    def _event_any_run(self, event_type: str, event_hash: str) -> ResearchEventEnvelope:
        matches = [event for event in self.store.query_events(event_type=event_type) if event.event_hash == event_hash]
        if len(matches) != 1:
            raise ValueError(f"exact {event_type} reference is required")
        return matches[0]


def legacy_v1_view(_: ResearchEventEnvelope) -> dict[str, Any]:
    """Legacy results stay auditable but cannot become v2 support evidence."""
    return {
        "schema_version": "legacy_falsification_view.v1",
        "outcome": "inconclusive",
        "promotion_cap": "research_only",
        "reason_codes": ["LEGACY_CALLER_AUTHORED_STATISTICS"],
    }


def render_falsification_narrative_v2(result: ResearchEventEnvelope) -> str:
    if result.event_type != "FalsificationResultV2Recorded":
        raise ValueError("narrative requires a protected v2 result")
    outcome = str(result.payload["outcome"])
    narratives = {
        "falsified": "The preregistered mechanism claim was falsified by decisive evidence.",
        "inconclusive": "Mechanism evidence is inconclusive; no support claim is established.",
        "partial_support": "Some preregistered implications received support, but the mechanism is only partially supported.",
        "supported": "All decisive preregistered mechanism tests received support within the frozen scope.",
    }
    return narratives[outcome]


__all__ = [
    "DatedObservationV2",
    "FalsificationContractAuthorityV2",
    "FalsificationContractV2",
    "FalsificationExecutorV2",
    "FalsificationSourceProducerV2",
    "FalsificationTestCatalogV2",
    "TestCapabilityV2",
    "TestSpecV2",
    "legacy_v1_view",
    "render_falsification_narrative_v2",
    "validate_contract_v2",
]
