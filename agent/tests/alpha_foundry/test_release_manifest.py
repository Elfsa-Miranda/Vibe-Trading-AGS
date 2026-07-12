from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.alpha_foundry.activation.release_manifest import ReleaseManifestBuilderV1
from src.research_ledger.hash_utils import canonical_json_hash


ACCEPTED_COMMIT = "e11630c33f07600b5e9fd6af7894dbd6432ff566"
AUDIT_COMMIT = "5f71c8d6599533b4cde8d900e71990b1b3ed5747"


def _fixture_repository(tmp_path: Path) -> Path:
    source = Path(__file__).resolve().parents[3]
    root = tmp_path / "repository"
    for relative in (
        Path("agent/research_evidence/activation"),
        Path("docs/alpha-genesis-final-acceptance.md"),
        Path("docs/alpha-genesis-known-limitations.md"),
    ):
        origin = source / relative
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if origin.is_dir():
            shutil.copytree(origin, target)
        else:
            shutil.copy2(origin, target)
    (root / "problem.md").write_text("fixture audit input\n", encoding="utf-8")
    return root


def _builder(root: Path) -> ReleaseManifestBuilderV1:
    return ReleaseManifestBuilderV1(
        root,
        accepted_code_commit=ACCEPTED_COMMIT,
        audit_document=root / "problem.md",
        audit_document_worktree_commit=AUDIT_COMMIT,
    )


def _write_semantic_artifact(
    root: Path,
    kind: str,
    payload: dict[str, object],
    hash_field: str,
) -> None:
    content_hash = canonical_json_hash(payload, exclude_keys=(hash_field,))
    payload[hash_field] = content_hash
    digest = content_hash.removeprefix("sha256:")
    target = root / "agent" / "research_evidence" / "activation" / kind
    target = target / digest[:2] / f"{digest}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_release_manifest_rebuild_is_deterministic_and_fail_closed(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    builder = _builder(root)

    first = builder.build()
    second = builder.build()

    assert first.to_dict() == second.to_dict()
    assert first.activation_status == "preflight_invalidated"
    assert first.empirical_status == "not_established"
    assert first.engineering_status == "implementation_present_evidence_not_event_bound"
    assert "TREATMENT_POLICY_HASH_SUPERSEDED" in first.limitations
    assert "ACTIVATION_ARTIFACTS_HAVE_NO_TRACKED_SOURCE_EVENT_BINDING" in first.limitations
    assert all(entry.authority_status == "legacy_unverified" for entry in first.artifacts)
    assert all(entry.source_event_hash is None for entry in first.artifacts)
    assert all(entry.superseded for entry in first.artifacts)
    assert all(
        entry.superseded_reason == "TREATMENT_POLICY_HASH_SUPERSEDED"
        for entry in first.artifacts
    )
    assert first.to_dict()["verification_records"] == []


def test_rehashed_approved_legacy_claim_cannot_become_empirical_authority(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    decision_root = root / "agent" / "research_evidence" / "activation" / "decision"
    original = next(decision_root.glob("*/*.json"))
    payload = json.loads(original.read_text(encoding="utf-8"))
    original.unlink()
    payload["verdict"] = "approved"
    payload["active_research_only"] = True
    payload["reasons"] = []
    _write_semantic_artifact(root, "decision", payload, "decision_hash")

    manifest = _builder(root).build()

    assert manifest.activation_status == "legacy_unverified_claim"
    assert manifest.empirical_status == "not_established"
    decision = next(entry for entry in manifest.artifacts if entry.kind == "decision")
    assert decision.lifecycle_status == "legacy_unverified_claim"
    assert decision.authority_status == "legacy_unverified"
    assert decision.source_event_hash is None


def test_release_manifest_rejects_tampered_or_unbound_artifacts(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    result = next(
        (root / "agent" / "research_evidence" / "activation" / "result").glob(
            "*/*.json"
        )
    )
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload["effective_sample"] = 12
    result.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="content hash mismatch"):
        _builder(root).build()


def test_release_manifest_rejects_cross_artifact_relationship_forgery(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    result_root = root / "agent" / "research_evidence" / "activation" / "result"
    original = next(result_root.glob("*/*.json"))
    payload = json.loads(original.read_text(encoding="utf-8"))
    original.unlink()
    payload["plan_hash"] = "sha256:" + "1" * 64
    _write_semantic_artifact(root, "result", payload, "result_hash")

    with pytest.raises(ValueError, match="not bound to an indexed plan"):
        _builder(root).build()


def test_release_manifest_writer_is_a_stable_projection(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    builder = _builder(root)

    path = builder.write()
    first = path.read_bytes()
    path = builder.write()
    second = path.read_bytes()

    assert first == second
    payload = json.loads(first)
    assert payload == builder.build().to_dict()
    assert str(root) not in first.decode("utf-8")


def test_legacy_document_hashes_are_checkout_line_ending_invariant(
    tmp_path: Path,
) -> None:
    root = _fixture_repository(tmp_path)
    before = _builder(root).build()
    for relative in (
        "docs/alpha-genesis-final-acceptance.md",
        "docs/alpha-genesis-known-limitations.md",
    ):
        path = root / relative
        text = path.read_text(encoding="utf-8").replace("\r\n", "\n")
        path.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))

    after = _builder(root).build()

    assert before.to_dict() == after.to_dict()


def test_release_manifest_requires_exact_accepted_commit(tmp_path: Path) -> None:
    root = _fixture_repository(tmp_path)
    with pytest.raises(ValueError, match="full lowercase git commit"):
        ReleaseManifestBuilderV1(
            root,
            accepted_code_commit="e11630c",
            audit_document=root / "problem.md",
            audit_document_worktree_commit=AUDIT_COMMIT,
        )


def test_tracked_release_manifest_matches_deterministic_rebuild() -> None:
    root = Path(__file__).resolve().parents[3]
    tracked = json.loads(
        (root / "agent/research_evidence/release_manifest.json").read_text(
            encoding="utf-8"
        )
    )

    rebuilt = ReleaseManifestBuilderV1(
        root,
        accepted_code_commit=ACCEPTED_COMMIT,
        audit_document=Path("D:/Vibe-Trading/problem.md"),
        audit_document_worktree_commit=AUDIT_COMMIT,
    ).build()

    assert tracked == rebuilt.to_dict()
