from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from src.research_ledger.events.artifacts import (
    ArtifactConflictError,
    AtomicContentAddressedArtifactWriter,
    hash_artifact,
)
from src.research_ledger.events.model import ArtifactReferenceError
from src.research_ledger.hash_utils import canonical_json_hash


SCHEMA = frozenset({"schema_version", "value", "content_hash"})


def _payload(value: str = "evidence") -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": "fixture_artifact.v1",
        "value": value,
    }
    payload["content_hash"] = canonical_json_hash(payload)
    return payload


def _write(
    writer: AtomicContentAddressedArtifactWriter,
    payload: dict[str, object] | None = None,
):
    return writer.write_json(
        namespace="fixture",
        payload=_payload() if payload is None else payload,
        schema_version="fixture_artifact.v1",
        semantic_hash_field="content_hash",
        closed_keys=SCHEMA,
        media_type="application/vnd.vibe.fixture-artifact-v1+json",
    )


def test_atomic_writer_binds_semantic_path_blob_and_closed_content(
    tmp_path: Path,
) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)

    artifact = _write(writer)
    target = tmp_path / Path(artifact.relative_path)

    assert target.is_file()
    assert artifact.relative_path.endswith(
        artifact.semantic_hash.removeprefix("sha256:") + ".json"
    )
    assert artifact.blob_hash == hash_artifact(target)
    assert artifact.reference() == {
        "relative_path": artifact.relative_path,
        "artifact_hash": artifact.blob_hash,
        "media_type": "application/vnd.vibe.fixture-artifact-v1+json",
    }
    assert json.loads(target.read_text(encoding="utf-8")) == _payload()


def test_writer_constructor_does_not_create_a_missing_artifact_root(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(ArtifactReferenceError, match="must already exist"):
        AtomicContentAddressedArtifactWriter(missing)
    assert not missing.exists()


def test_atomic_writer_same_content_is_idempotent_under_concurrency(
    tmp_path: Path,
) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _write(writer), range(32)))

    assert len({result.semantic_hash for result in results}) == 1
    assert len({result.relative_path for result in results}) == 1
    assert len({result.blob_hash for result in results}) == 1
    assert len(list((tmp_path / "fixture").glob("*/*.json"))) == 1
    staging = tmp_path / ".artifact-staging"
    assert not list(staging.glob("*.tmp"))


def test_preexisting_wrong_content_at_semantic_path_is_rejected(
    tmp_path: Path,
) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    expected = _payload()
    digest = str(expected["content_hash"]).removeprefix("sha256:")
    target = tmp_path / "fixture" / digest[:2] / f"{digest}.json"
    target.parent.mkdir(parents=True)
    target.write_text('{"schema_version":"fixture_artifact.v1","value":"wrong"}\n')

    with pytest.raises(ArtifactConflictError):
        _write(writer, expected)

    assert json.loads(target.read_text(encoding="utf-8"))["value"] == "wrong"


def test_noncanonical_existing_bytes_are_not_silently_reused(tmp_path: Path) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    expected = _payload()
    digest = str(expected["content_hash"]).removeprefix("sha256:")
    target = tmp_path / "fixture" / digest[:2] / f"{digest}.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps(expected, indent=2), encoding="utf-8")

    with pytest.raises(ArtifactConflictError, match="non-canonical"):
        _write(writer, expected)


@pytest.mark.parametrize(
    "payload",
    [
        {
            "schema_version": "fixture_artifact.v1",
            "value": float("nan"),
            "content_hash": "sha256:" + "0" * 64,
        },
        {
            "schema_version": "fixture_artifact.v1",
            "value": "ok",
            "extra": "caller field",
            "content_hash": "sha256:" + "0" * 64,
        },
        {
            "schema_version": "fixture_artifact.v1",
            "value": "api_key=forbidden-value",
            "content_hash": "sha256:" + "0" * 64,
        },
    ],
)
def test_writer_rejects_nonfinite_open_schema_and_secret_content(
    tmp_path: Path,
    payload: dict[str, object],
) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)

    with pytest.raises(ArtifactReferenceError):
        _write(writer, payload)

    assert not list(tmp_path.glob("fixture/*/*.json"))


def test_semantic_hash_field_and_content_must_agree(tmp_path: Path) -> None:
    writer = AtomicContentAddressedArtifactWriter(tmp_path)
    payload = _payload()
    payload["value"] = "changed after hash"

    with pytest.raises(ArtifactReferenceError, match="does not match content"):
        _write(writer, payload)
