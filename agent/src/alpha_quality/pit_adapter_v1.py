"""Registered, source-only adapter boundary for A-share PIT snapshots."""

from __future__ import annotations

import inspect
import importlib
import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Mapping, Protocol, runtime_checkable

import numpy as np
import pandas as pd  # type: ignore[import-untyped]

from src.research_ledger.hash_utils import canonical_json_hash


_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._:-]{0,127}$")
_BUILT_IN_PRODUCTION_ADAPTER_TYPES = frozenset(
    {
        (
            "src.alpha_quality.adapters.tushare_csi300_pit_v1",
            "TushareCSI300PITAdapterV1",
        ),
    }
)


def _require_identifier(value: str, name: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a bounded identifier")


def _require_hash(value: str, name: str) -> None:
    if not isinstance(value, str) or _HASH_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 hash")


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _deep_freeze(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _plain(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


@dataclass(frozen=True)
class AsharePITAdapterDescriptorV1:
    adapter_id: str
    provider: str
    adapter_version: str
    market: Literal["CN_A_SHARE"]
    calendar_id: str
    timezone: Literal["Asia/Shanghai"]
    membership_dataset: str
    security_master_dataset: str
    corporate_action_dataset: str
    trade_state_dataset: str
    price_dataset: str
    availability_semantics: Literal["provider_release_timestamp.v1"]
    adjustment_semantics: Literal["raw_prices_plus_dated_actions.v1"]
    schema_version: Literal["ashare_pit_adapter_descriptor.v1"] = (
        "ashare_pit_adapter_descriptor.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_pit_adapter_descriptor.v1":
            raise ValueError("unsupported A-share PIT adapter descriptor")
        for name in (
            "adapter_id",
            "provider",
            "adapter_version",
            "calendar_id",
            "membership_dataset",
            "security_master_dataset",
            "corporate_action_dataset",
            "trade_state_dataset",
            "price_dataset",
        ):
            _require_identifier(str(getattr(self, name)), name)
        if self.market != "CN_A_SHARE" or self.timezone != "Asia/Shanghai":
            raise ValueError("A-share PIT adapter market/timezone is unsupported")
        if self.availability_semantics != "provider_release_timestamp.v1":
            raise ValueError("A-share PIT availability semantics are unsupported")
        if self.adjustment_semantics != "raw_prices_plus_dated_actions.v1":
            raise ValueError("A-share PIT adjustment semantics are unsupported")

    @property
    def descriptor_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "provider": self.provider,
            "adapter_version": self.adapter_version,
            "market": self.market,
            "calendar_id": self.calendar_id,
            "timezone": self.timezone,
            "membership_dataset": self.membership_dataset,
            "security_master_dataset": self.security_master_dataset,
            "corporate_action_dataset": self.corporate_action_dataset,
            "trade_state_dataset": self.trade_state_dataset,
            "price_dataset": self.price_dataset,
            "availability_semantics": self.availability_semantics,
            "adjustment_semantics": self.adjustment_semantics,
        }


@dataclass(frozen=True)
class AsharePITSnapshotRequestV1:
    adapter_id: str
    calendar_dates: tuple[str, ...]
    required_fields: tuple[str, ...]
    valid_cutoff: str
    evaluation_policy_event_hash: str
    schema_version: Literal["ashare_pit_snapshot_request.v1"] = (
        "ashare_pit_snapshot_request.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_pit_snapshot_request.v1":
            raise ValueError("unsupported A-share PIT snapshot request")
        _require_identifier(self.adapter_id, "adapter_id")
        _require_hash(
            self.evaluation_policy_event_hash,
            "evaluation_policy_event_hash",
        )
        if (
            not self.calendar_dates
            or self.calendar_dates != tuple(sorted(set(self.calendar_dates)))
            or self.valid_cutoff != self.calendar_dates[-1]
        ):
            raise ValueError("PIT request dates/cutoff must be canonical")
        normalized_dates = tuple(
            pd.Timestamp(item).date().isoformat() for item in self.calendar_dates
        )
        if normalized_dates != self.calendar_dates:
            raise ValueError("PIT request dates must be ISO dates")
        if (
            not self.required_fields
            or self.required_fields != tuple(sorted(set(self.required_fields)))
        ):
            raise ValueError("PIT request fields must be sorted and unique")
        for field in self.required_fields:
            _require_identifier(field, "required_field")

    @property
    def request_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "calendar_dates": list(self.calendar_dates),
            "required_fields": list(self.required_fields),
            "valid_cutoff": self.valid_cutoff,
            "evaluation_policy_event_hash": self.evaluation_policy_event_hash,
        }


@dataclass(frozen=True)
class AsharePITSourceManifestV1:
    adapter_id: str
    request_hash: str
    dataset_vintage: str
    source_as_of: str
    query_receipt_hashes: tuple[str, ...]
    source_partition_hashes: tuple[str, ...]
    schema_version: Literal["ashare_pit_source_manifest.v1"] = (
        "ashare_pit_source_manifest.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_pit_source_manifest.v1":
            raise ValueError("unsupported A-share PIT source manifest")
        _require_identifier(self.adapter_id, "adapter_id")
        _require_identifier(self.dataset_vintage, "dataset_vintage")
        _require_hash(self.request_hash, "request_hash")
        try:
            normalized_as_of = pd.Timestamp(self.source_as_of)
        except (TypeError, ValueError) as exc:
            raise ValueError("PIT source_as_of must be a timestamp") from exc
        if normalized_as_of.tzinfo is None:
            raise ValueError("PIT source_as_of must include a timezone")
        for name, values in (
            ("query_receipt_hashes", self.query_receipt_hashes),
            ("source_partition_hashes", self.source_partition_hashes),
        ):
            if not values or values != tuple(sorted(set(values))):
                raise ValueError(f"{name} must be a sorted non-empty hash tuple")
            for value in values:
                _require_hash(value, name)

    @property
    def manifest_hash(self) -> str:
        return canonical_json_hash(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "adapter_id": self.adapter_id,
            "request_hash": self.request_hash,
            "dataset_vintage": self.dataset_vintage,
            "source_as_of": self.source_as_of,
            "query_receipt_hashes": list(self.query_receipt_hashes),
            "source_partition_hashes": list(self.source_partition_hashes),
        }


def _copy_frame(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TypeError(f"{name} must be a DataFrame")
    copied = frame.copy(deep=True)
    if copied.index.has_duplicates or copied.columns.has_duplicates:
        raise ValueError(f"{name} axes must be unique")
    return copied


@dataclass(frozen=True)
class AsharePITSourceBundleV1:
    market_fields: Mapping[str, pd.DataFrame]
    field_available_at: Mapping[str, pd.DataFrame]
    trade_state_fields: Mapping[str, pd.DataFrame]
    daily_membership: pd.DataFrame
    security_master: pd.DataFrame
    corporate_actions: pd.DataFrame
    calendar_dates: tuple[str, ...]
    source_manifest: AsharePITSourceManifestV1
    schema_version: Literal["ashare_pit_source_bundle.v1"] = (
        "ashare_pit_source_bundle.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "ashare_pit_source_bundle.v1":
            raise ValueError("unsupported A-share PIT source bundle")
        fields = {
            str(name): _copy_frame(frame, f"market_fields.{name}")
            for name, frame in self.market_fields.items()
        }
        availability = {
            str(name): _copy_frame(frame, f"field_available_at.{name}")
            for name, frame in self.field_available_at.items()
        }
        if not fields or set(fields) != set(availability):
            raise ValueError("market fields and availability tables must match")
        if list(fields) != sorted(fields) or list(availability) != sorted(availability):
            raise ValueError("PIT bundle field mappings must be sorted")
        for name, frame in fields.items():
            if any(not pd.api.types.is_numeric_dtype(dtype) for dtype in frame.dtypes):
                raise ValueError(f"market field {name} must be numeric")
            if np.isinf(frame.to_numpy(dtype=float, copy=False)).any():
                raise ValueError(f"market field {name} contains Infinity")
        trade_states = {
            str(name): _copy_frame(frame, f"trade_state_fields.{name}")
            for name, frame in self.trade_state_fields.items()
        }
        if not trade_states or list(trade_states) != sorted(trade_states):
            raise ValueError("PIT trade-state mapping must be sorted and non-empty")
        boolean_trade_states = {
            "at_limit_down",
            "at_limit_up",
            "is_st",
            "is_suspended",
        }
        for name, frame in trade_states.items():
            if name in boolean_trade_states:
                if any(dtype != np.dtype(bool) for dtype in frame.dtypes):
                    raise ValueError(f"trade-state {name} must be strict bool")
            elif name == "listing_age_days":
                if any(
                    not pd.api.types.is_numeric_dtype(dtype)
                    for dtype in frame.dtypes
                ):
                    raise ValueError("listing_age_days must be numeric")
                if np.isinf(frame.to_numpy(dtype=float, copy=False)).any():
                    raise ValueError("listing_age_days contains Infinity")
            else:
                raise ValueError("unsupported PIT trade-state field")
        frame = _copy_frame(self.daily_membership, "daily_membership")
        if any(dtype != np.dtype(bool) for dtype in frame.dtypes):
            raise ValueError("daily_membership must be strict bool with no NA")
        object.__setattr__(self, "daily_membership", frame)
        security_master = _copy_frame(self.security_master, "security_master")
        corporate_actions = _copy_frame(self.corporate_actions, "corporate_actions")
        if self.calendar_dates != tuple(sorted(set(self.calendar_dates))):
            raise ValueError("source bundle calendar dates must be canonical")
        normalized_dates = tuple(
            pd.Timestamp(item).date().isoformat() for item in self.calendar_dates
        )
        if normalized_dates != self.calendar_dates:
            raise ValueError("source bundle calendar dates must be ISO dates")
        object.__setattr__(self, "market_fields", MappingProxyType(fields))
        object.__setattr__(self, "field_available_at", MappingProxyType(availability))
        object.__setattr__(self, "trade_state_fields", MappingProxyType(trade_states))
        object.__setattr__(self, "security_master", security_master)
        object.__setattr__(self, "corporate_actions", corporate_actions)

    def sealed_copy(self) -> "AsharePITSourceBundleV1":
        """Detach the producer pipeline from every adapter-owned DataFrame."""
        return AsharePITSourceBundleV1(
            market_fields={
                name: frame.copy(deep=True)
                for name, frame in self.market_fields.items()
            },
            field_available_at={
                name: frame.copy(deep=True)
                for name, frame in self.field_available_at.items()
            },
            trade_state_fields={
                name: frame.copy(deep=True)
                for name, frame in self.trade_state_fields.items()
            },
            daily_membership=self.daily_membership.copy(deep=True),
            security_master=self.security_master.copy(deep=True),
            corporate_actions=self.corporate_actions.copy(deep=True),
            calendar_dates=tuple(self.calendar_dates),
            source_manifest=self.source_manifest,
        )


@runtime_checkable
class AsharePITDataAdapterV1(Protocol):
    def descriptor(self) -> AsharePITAdapterDescriptorV1: ...

    def load(self, request: AsharePITSnapshotRequestV1) -> AsharePITSourceBundleV1: ...


@dataclass(frozen=True)
class RegisteredAsharePITAdapterV1:
    descriptor: AsharePITAdapterDescriptorV1
    implementation_hash: str
    factory_origin: str
    factory_hash: str
    provider_version: str
    authority_class: Literal["built_in_production", "external_unverified"]
    registration_hash: str
    schema_version: Literal["registered_ashare_pit_adapter.v1"] = (
        "registered_ashare_pit_adapter.v1"
    )

    def __post_init__(self) -> None:
        if self.schema_version != "registered_ashare_pit_adapter.v1":
            raise ValueError("unsupported registered A-share PIT adapter")
        _require_hash(self.implementation_hash, "implementation_hash")
        _require_identifier(self.factory_origin, "factory_origin")
        _require_hash(self.factory_hash, "factory_hash")
        _require_identifier(self.provider_version, "provider_version")
        _require_hash(self.registration_hash, "registration_hash")
        if self.authority_class not in {
            "built_in_production",
            "external_unverified",
        }:
            raise ValueError("unknown A-share PIT adapter authority class")
        if self.registration_hash != canonical_json_hash(self._content_dict()):
            raise ValueError("registered A-share PIT adapter hash differs")

    def _content_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "descriptor": self.descriptor.to_dict(),
            "descriptor_hash": self.descriptor.descriptor_hash,
            "implementation_hash": self.implementation_hash,
            "factory_origin": self.factory_origin,
            "factory_hash": self.factory_hash,
            "provider_version": self.provider_version,
            "authority_class": self.authority_class,
        }

    def to_dict(self) -> dict[str, Any]:
        return {**self._content_dict(), "registration_hash": self.registration_hash}


class AsharePITAdapterRegistryV1:
    """Frozen app-construction registry; callers choose IDs, never PIT conclusions."""

    def __init__(self, adapters: Mapping[str, AsharePITDataAdapterV1]) -> None:
        if not adapters:
            raise ValueError("A-share PIT adapter registry cannot be empty")
        registered: dict[str, RegisteredAsharePITAdapterV1] = {}
        instances: dict[str, AsharePITDataAdapterV1] = {}
        for adapter_id, adapter in sorted(adapters.items()):
            if not isinstance(adapter, AsharePITDataAdapterV1):
                raise TypeError("registered A-share PIT adapter violates its protocol")
            descriptor = adapter.descriptor()
            if adapter_id != descriptor.adapter_id or adapter_id in registered:
                raise ValueError("A-share PIT adapter registry identity differs")
            adapter_type = type(adapter)
            try:
                source = inspect.getsource(adapter_type)
            except (OSError, TypeError) as exc:
                raise ValueError("A-share PIT adapter implementation is not inspectable") from exc
            implementation = {
                "module": adapter_type.__module__,
                "qualname": adapter_type.__qualname__,
                "source": source.replace("\r\n", "\n"),
            }
            implementation_hash = canonical_json_hash(implementation)
            builtin_type = (adapter_type.__module__, adapter_type.__qualname__)
            attestation: Mapping[str, Any] | None = None
            if builtin_type in _BUILT_IN_PRODUCTION_ADAPTER_TYPES:
                module = importlib.import_module(adapter_type.__module__)
                attestor = getattr(module, "production_factory_attestation", None)
                if attestor is not None:
                    attestation = attestor(adapter)
                if attestation is not None:
                    implementation_hash = canonical_json_hash(
                        {
                            "module": adapter_type.__module__,
                            "qualname": adapter_type.__qualname__,
                            "source_scope": "module",
                            "source": inspect.getsource(module).replace("\r\n", "\n"),
                        }
                    )
            authority: Literal["built_in_production", "external_unverified"] = (
                "built_in_production" if attestation is not None else "external_unverified"
            )
            factory_origin = (
                str(attestation["factory_origin"])
                if attestation is not None
                else "unverified_or_injected"
            )
            factory_hash = (
                str(attestation["factory_hash"])
                if attestation is not None
                else canonical_json_hash(
                    {
                        "schema_version": "unverified_adapter_factory.v1",
                        "factory_origin": factory_origin,
                        "adapter_module": adapter_type.__module__,
                        "adapter_qualname": adapter_type.__qualname__,
                    }
                )
            )
            provider_version = (
                str(attestation["provider_version"])
                if attestation is not None
                else "unverified"
            )
            content = {
                "schema_version": "registered_ashare_pit_adapter.v1",
                "descriptor": descriptor.to_dict(),
                "descriptor_hash": descriptor.descriptor_hash,
                "implementation_hash": implementation_hash,
                "factory_origin": factory_origin,
                "factory_hash": factory_hash,
                "provider_version": provider_version,
                "authority_class": authority,
            }
            registered[adapter_id] = RegisteredAsharePITAdapterV1(
                descriptor=descriptor,
                implementation_hash=implementation_hash,
                factory_origin=factory_origin,
                factory_hash=factory_hash,
                provider_version=provider_version,
                authority_class=authority,
                registration_hash=canonical_json_hash(content),
            )
            instances[adapter_id] = adapter
        self._registered = MappingProxyType(registered)
        self._instances = MappingProxyType(instances)
        self._registry_hash = canonical_json_hash(
            {
                "schema_version": "ashare_pit_adapter_registry.v1",
                "registrations": {
                    key: value.registration_hash
                    for key, value in registered.items()
                },
            }
        )

    @property
    def registry_hash(self) -> str:
        return self._registry_hash

    def registration(self, adapter_id: str) -> RegisteredAsharePITAdapterV1:
        try:
            return self._registered[adapter_id]
        except KeyError as exc:
            raise KeyError("A-share PIT adapter is not registered") from exc

    def adapter(self, adapter_id: str) -> AsharePITDataAdapterV1:
        registration = self.registration(adapter_id)
        adapter = self._instances[adapter_id]
        if registration.authority_class == "built_in_production":
            module = importlib.import_module(type(adapter).__module__)
            attestor = getattr(module, "production_factory_attestation", None)
            attestation = None if attestor is None else attestor(adapter)
            implementation_hash = canonical_json_hash(
                {
                    "module": type(adapter).__module__,
                    "qualname": type(adapter).__qualname__,
                    "source_scope": "module",
                    "source": inspect.getsource(module).replace("\r\n", "\n"),
                }
            )
            if (
                attestation is None
                or str(attestation["factory_hash"]) != registration.factory_hash
                or str(attestation["provider_version"]) != registration.provider_version
                or implementation_hash != registration.implementation_hash
            ):
                raise ValueError("runtime production adapter attestation differs")
        return adapter

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "ashare_pit_adapter_registry.v1",
            "registry_hash": self.registry_hash,
            "registrations": {
                key: _plain(value.to_dict())
                for key, value in self._registered.items()
            },
        }


__all__ = [
    "AsharePITAdapterDescriptorV1",
    "AsharePITAdapterRegistryV1",
    "AsharePITDataAdapterV1",
    "AsharePITSnapshotRequestV1",
    "AsharePITSourceBundleV1",
    "AsharePITSourceManifestV1",
    "RegisteredAsharePITAdapterV1",
]
