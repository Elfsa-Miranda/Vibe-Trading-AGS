"""Immutable Alpha Genesis capability flags resolved at construction time."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


AGS_FLAG_DEFAULTS: dict[str, bool] = {
    "VIBE_TRADING_AGS_ENABLED": False,
    "VIBE_TRADING_ALPHA_SCORECARD": False,
    "VIBE_TRADING_TRIAL_LEDGER": False,
    "VIBE_TRADING_ALPHA_FOUNDRY": False,
    "VIBE_TRADING_ADMISSION_GATE": False,
    "VIBE_TRADING_FORWARD_TRACKING": False,
    "VIBE_TRADING_ALPHA_REPORT_API": False,
    "VIBE_TRADING_RESEARCH_EVENTS": False,
    "VIBE_TRADING_FACTOR_DAG": False,
    "VIBE_TRADING_PROCESS_MEMORY": False,
    "VIBE_TRADING_TOPOLOGY_RETRIEVER": False,
    "VIBE_TRADING_TOPOLOGY_RETRIEVER_ACTIVE": False,
    "VIBE_TRADING_FALSIFICATION_CONTRACT": False,
    "VIBE_TRADING_COMPLEMENT_V2": False,
    "VIBE_TRADING_DECISION_V2": False,
}

# research_event.v1 envelopes emitted before any future default-off capability
# additions carry this exact frozen key set.  It is a replay contract: never
# rewrite historical event hashes merely because a new flag is added.
AGS_FLAG_SNAPSHOT_KEYSET_V1 = frozenset(
    name
    for name in AGS_FLAG_DEFAULTS
    if name != "VIBE_TRADING_TOPOLOGY_RETRIEVER_ACTIVE"
)
AGS_FLAG_SNAPSHOT_KEYSET_V2 = frozenset(AGS_FLAG_DEFAULTS)
_LEGACY_FLAG_KEYSETS = (AGS_FLAG_SNAPSHOT_KEYSET_V1, AGS_FLAG_SNAPSHOT_KEYSET_V2)

_MASTER_FLAG = "VIBE_TRADING_AGS_ENABLED"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"", "0", "false", "no", "off"})


def _setting_value(settings: Mapping[str, Any] | object, name: str) -> Any:
    if isinstance(settings, Mapping):
        return settings.get(name)
    getter = getattr(settings, "get", None)
    if callable(getter):
        try:
            return getter(name)
        except TypeError:
            pass
    return getattr(settings, name, None)


def _parse_flag(value: Any, *, name: str) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ValueError(f"invalid boolean value for {name}")


def is_valid_ags_flag_snapshot(values: Mapping[str, Any]) -> bool:
    """Validate a frozen current or historical event-envelope flag snapshot."""

    supplied = dict(values)
    current = frozenset(AGS_FLAG_DEFAULTS)
    if frozenset(supplied) not in {*_LEGACY_FLAG_KEYSETS, current}:
        return False
    if any(not isinstance(value, bool) for value in supplied.values()):
        return False
    master = supplied.get(_MASTER_FLAG)
    if master is not True:
        return not any(
            value for name, value in supplied.items() if name != _MASTER_FLAG
        )
    return bool(supplied.get("VIBE_TRADING_RESEARCH_EVENTS"))


@dataclass(frozen=True)
class ResolvedAGSFlags:
    """A parent-dominated immutable flag snapshot for one app or run."""

    values: Mapping[str, bool] = field(repr=False)

    def __post_init__(self) -> None:
        supplied = dict(self.values)
        if set(supplied) != set(AGS_FLAG_DEFAULTS):
            missing = sorted(set(AGS_FLAG_DEFAULTS) - set(supplied))
            unknown = sorted(set(supplied) - set(AGS_FLAG_DEFAULTS))
            raise ValueError(f"invalid AGS flag snapshot; missing={missing}, unknown={unknown}")
        object.__setattr__(self, "values", MappingProxyType(supplied))

    @classmethod
    def from_settings(
        cls,
        settings: Mapping[str, Any] | object | None = None,
    ) -> "ResolvedAGSFlags":
        source: Mapping[str, Any] | object = os.environ if settings is None else settings
        requested = {
            name: _parse_flag(_setting_value(source, name), name=name)
            for name in AGS_FLAG_DEFAULTS
        }
        master_enabled = requested[_MASTER_FLAG]
        resolved = {
            name: value if name == _MASTER_FLAG else master_enabled and value
            for name, value in requested.items()
        }
        return cls(resolved)

    def enabled(self, name: str) -> bool:
        if name not in self.values:
            raise KeyError(f"unknown AGS feature flag: {name}")
        return self.values[name]

    def as_dict(self) -> dict[str, bool]:
        return dict(self.values)


__all__ = [
    "AGS_FLAG_DEFAULTS",
    "AGS_FLAG_SNAPSHOT_KEYSET_V1",
    "AGS_FLAG_SNAPSHOT_KEYSET_V2",
    "ResolvedAGSFlags",
    "is_valid_ags_flag_snapshot",
]
