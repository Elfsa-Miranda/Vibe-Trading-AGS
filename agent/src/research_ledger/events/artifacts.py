"""Content-addressed artifact reference validation outside write transactions."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Mapping
from urllib.parse import unquote

from src.research_ledger.events.model import ArtifactReferenceError
from src.research_ledger.hash_utils import canonical_json, canonical_json_hash, redact_secrets


_WINDOWS_ABSOLUTE_RE = re.compile(r"^(?:[A-Za-z]:|\\\\|//|\\[?.]\\)")
_ADS_RE = re.compile(r"^[^/]+:[^/]+")
_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_HASH_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


class ArtifactConflictError(ArtifactReferenceError):
    """A semantic path already exists with non-canonical or different content."""


@dataclass(frozen=True)
class ContentAddressedArtifact:
    semantic_hash: str
    relative_path: str
    blob_hash: str
    media_type: str

    def reference(self) -> dict[str, str]:
        return {
            "relative_path": self.relative_path,
            "artifact_hash": self.blob_hash,
            "media_type": self.media_type,
        }


def hash_artifact(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _hash_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _normalized_absolute(path: Path) -> str:
    value = os.path.normcase(os.path.abspath(str(path)))
    if value.startswith("\\\\?\\"):
        value = value[4:]
    return value.rstrip("\\/")


def _is_within(path: Path, root: Path) -> bool:
    candidate = _normalized_absolute(path)
    boundary = _normalized_absolute(root)
    return candidate == boundary or candidate.startswith(boundary + os.sep)


def _strict_plain(value: Any, *, depth: int = 0) -> Any:
    if depth > 64:
        raise ArtifactReferenceError("artifact JSON nesting limit exceeded")
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, child in value.items():
            if not isinstance(key, str) or not key or key in result:
                raise ArtifactReferenceError("artifact JSON keys must be unique strings")
            result[key] = _strict_plain(child, depth=depth + 1)
        return result
    if isinstance(value, (list, tuple)):
        return [_strict_plain(child, depth=depth + 1) for child in value]
    if isinstance(value, bool) or value is None or isinstance(value, (int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ArtifactReferenceError("artifact JSON contains a non-finite number")
        return value
    raise ArtifactReferenceError("artifact JSON contains an unsupported value")


def _decode_strict_json(raw: bytes) -> dict[str, Any]:
    def reject_constant(value: str) -> None:
        raise ArtifactConflictError(f"non-finite JSON constant is forbidden: {value}")

    def reject_duplicates(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ArtifactConflictError("duplicate artifact JSON key")
            result[key] = value
        return result

    try:
        decoded = raw.decode("utf-8", errors="strict")
        payload = json.loads(
            decoded,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicates,
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ArtifactConflictError("artifact is not strict UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ArtifactConflictError("artifact must contain one JSON object")
    return payload


class AtomicContentAddressedArtifactWriter:
    """Canonical JSON create-if-absent writer with semantic collision checks.

    The target is installed with a same-filesystem hard link from a fully
    fsynced staging file. This avoids exposing a partially written semantic
    path and, unlike ``os.replace``, never overwrites an existing artifact.
    """

    def __init__(self, root: str | Path, *, max_bytes: int = 16 * 1024 * 1024) -> None:
        if not 1 <= max_bytes <= 256 * 1024 * 1024:
            raise ValueError("artifact max_bytes is out of bounds")
        root_path = Path(root)
        try:
            self._root = root_path.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise ArtifactReferenceError("artifact root must already exist") from exc
        if not self._root.is_dir():
            raise ArtifactReferenceError("artifact root must be a directory")
        self._max_bytes = max_bytes

    @property
    def root(self) -> Path:
        return self._root

    def write_json(
        self,
        *,
        namespace: str,
        payload: Mapping[str, Any],
        schema_version: str,
        semantic_hash_field: str,
        closed_keys: frozenset[str],
        media_type: str,
    ) -> ContentAddressedArtifact:
        if _NAMESPACE_RE.fullmatch(namespace) is None:
            raise ArtifactReferenceError("artifact namespace is invalid")
        if not schema_version or not media_type or not semantic_hash_field:
            raise ArtifactReferenceError("artifact contract fields are required")
        plain = _strict_plain(payload)
        if set(plain) != set(closed_keys):
            raise ArtifactReferenceError("artifact does not match its closed schema")
        if plain.get("schema_version") != schema_version:
            raise ArtifactReferenceError("artifact schema version is invalid")
        if semantic_hash_field not in closed_keys:
            raise ArtifactReferenceError("semantic hash field is outside the closed schema")
        if canonical_json(redact_secrets(plain)) != canonical_json(plain):
            raise ArtifactReferenceError("artifact contains secret or private-path material")
        semantic_hash = str(plain.get(semantic_hash_field, ""))
        if _HASH_RE.fullmatch(semantic_hash) is None:
            raise ArtifactReferenceError("artifact semantic hash is invalid")
        rebuilt_hash = canonical_json_hash(plain, exclude_keys=(semantic_hash_field,))
        if rebuilt_hash != semantic_hash:
            raise ArtifactReferenceError("artifact semantic hash does not match content")
        canonical = (canonical_json(plain) + "\n").encode("utf-8")
        if len(canonical) > self._max_bytes:
            raise ArtifactReferenceError("artifact exceeds the configured byte limit")
        digest = semantic_hash.removeprefix("sha256:")
        relative = f"{namespace}/{digest[:2]}/{digest}.json"
        target = self._safe_target(relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not _is_within(target.parent.resolve(strict=True), self._root):
            raise ArtifactReferenceError("artifact parent escapes its root")
        if target.exists():
            return self._validate_existing(
                target=target,
                relative=relative,
                expected_payload=plain,
                expected_bytes=canonical,
                semantic_hash=semantic_hash,
                schema_version=schema_version,
                semantic_hash_field=semantic_hash_field,
                closed_keys=closed_keys,
                media_type=media_type,
            )

        staging = self._root / ".artifact-staging"
        staging.mkdir(parents=True, exist_ok=True)
        temporary = staging / f"{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("xb") as handle:
                handle.write(canonical)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.link(temporary, target)
            except FileExistsError:
                pass
            return self._validate_existing(
                target=target,
                relative=relative,
                expected_payload=plain,
                expected_bytes=canonical,
                semantic_hash=semantic_hash,
                schema_version=schema_version,
                semantic_hash_field=semantic_hash_field,
                closed_keys=closed_keys,
                media_type=media_type,
            )
        finally:
            temporary.unlink(missing_ok=True)

    def _validate_existing(
        self,
        *,
        target: Path,
        relative: str,
        expected_payload: dict[str, Any],
        expected_bytes: bytes,
        semantic_hash: str,
        schema_version: str,
        semantic_hash_field: str,
        closed_keys: frozenset[str],
        media_type: str,
    ) -> ContentAddressedArtifact:
        try:
            resolved = target.resolve(strict=True)
            if not _is_within(resolved, self._root):
                raise ArtifactConflictError("semantic artifact path escapes its root")
            if not resolved.is_file() or target.is_symlink():
                raise ArtifactConflictError("semantic artifact path is not a regular file")
            raw = resolved.read_bytes()
        except (FileNotFoundError, OSError, ValueError) as exc:
            if isinstance(exc, ArtifactConflictError):
                raise
            raise ArtifactConflictError("semantic artifact path is unavailable") from exc
        if len(raw) > self._max_bytes:
            raise ArtifactConflictError("existing artifact exceeds the byte limit")
        parsed = _decode_strict_json(raw)
        try:
            plain = _strict_plain(parsed)
        except ArtifactReferenceError as exc:
            raise ArtifactConflictError(str(exc)) from exc
        if set(plain) != set(closed_keys) or plain.get("schema_version") != schema_version:
            raise ArtifactConflictError("existing artifact violates the closed schema")
        if plain.get(semantic_hash_field) != semantic_hash:
            raise ArtifactConflictError("existing artifact semantic identity differs")
        rebuilt = canonical_json_hash(plain, exclude_keys=(semantic_hash_field,))
        if rebuilt != semantic_hash:
            raise ArtifactConflictError("existing artifact semantic hash is invalid")
        if canonical_json(redact_secrets(plain)) != canonical_json(plain):
            raise ArtifactConflictError("existing artifact contains unsafe material")
        if plain != expected_payload or raw != expected_bytes:
            raise ArtifactConflictError(
                "preexisting semantic path contains non-canonical or conflicting content"
            )
        return ContentAddressedArtifact(
            semantic_hash=semantic_hash,
            relative_path=relative,
            blob_hash=_hash_bytes(raw),
            media_type=media_type,
        )

    def _safe_target(self, relative: str) -> Path:
        parts = _safe_relative_path(relative)
        target = self._root.joinpath(*parts.parts)
        if not _is_within(target, self._root):
            raise ArtifactReferenceError("artifact target escapes its root")
        return target


def _safe_relative_path(raw: str) -> PurePosixPath:
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise ArtifactReferenceError("invalid relative artifact path")
    if unquote(raw) != raw:
        raise ArtifactReferenceError("encoded relative artifact path is forbidden")
    if "\\" in raw or _WINDOWS_ABSOLUTE_RE.match(raw) or _ADS_RE.match(raw):
        raise ArtifactReferenceError("invalid relative artifact path")
    relative = PurePosixPath(raw)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArtifactReferenceError("invalid relative artifact path")
    return relative


def safe_artifact_target(artifact_root: str | Path, relative_path: str) -> Path:
    """Resolve a not-yet-created artifact target without importing feature layers."""
    root = Path(artifact_root).resolve(strict=True)
    if not root.is_dir():
        raise ArtifactReferenceError("artifact root must be a directory")
    relative = _safe_relative_path(relative_path)
    target = root.joinpath(*relative.parts)
    if not _is_within(target, root):
        raise ArtifactReferenceError("artifact target escapes its root")
    ancestor = target.parent
    while not ancestor.exists() and ancestor != root:
        ancestor = ancestor.parent
    try:
        resolved_ancestor = ancestor.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ArtifactReferenceError("artifact parent is unavailable") from exc
    if not _is_within(resolved_ancestor, root):
        raise ArtifactReferenceError("artifact parent escapes its root")
    if target.exists() and target.is_symlink():
        raise ArtifactReferenceError("artifact target cannot be a symlink")
    return target


def validate_artifact_references(
    artifact_root: str | Path,
    references: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
) -> list[dict[str, str]]:
    root = Path(artifact_root).resolve(strict=True)
    normalized: list[dict[str, str]] = []
    for reference in references:
        relative = _safe_relative_path(str(reference["relative_path"]))
        candidate = root.joinpath(*relative.parts)
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (FileNotFoundError, OSError, ValueError) as exc:
            raise ArtifactReferenceError(
                f"artifact is missing or escapes artifact root: {relative.as_posix()}"
            ) from exc
        if not resolved.is_file():
            raise ArtifactReferenceError(f"artifact is not a regular file: {relative.as_posix()}")
        actual_hash = hash_artifact(resolved)
        expected_hash = str(reference["artifact_hash"])
        if actual_hash != expected_hash:
            raise ArtifactReferenceError(
                f"artifact hash mismatch for {relative.as_posix()}"
            )
        normalized.append(
            {
                "relative_path": relative.as_posix(),
                "artifact_hash": expected_hash,
                "media_type": str(reference["media_type"]),
            }
        )
    return normalized


__all__ = [
    "ArtifactConflictError",
    "AtomicContentAddressedArtifactWriter",
    "ContentAddressedArtifact",
    "hash_artifact",
    "validate_artifact_references",
]
