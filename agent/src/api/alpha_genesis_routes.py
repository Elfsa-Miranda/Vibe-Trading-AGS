"""Read-only Alpha Genesis report routes."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

from fastapi import Depends, FastAPI, HTTPException, Response

from src.alpha_quality.reporting import (
    ReportArtifactKind,
    ReportArtifactNotFound,
    ReportArtifactReader,
    ReportArtifactValidationError,
    ReportPathError,
)
from src.alpha_quality.research_dossier_v1 import (
    AUDIENCES,
    CanonicalDossierResolverV1,
)
from src.research_ledger.events import EventValidationError


AuthDep = Callable[..., Awaitable[Any] | Any]
def _default_report_root() -> Path:
    repo_root = Path(__file__).resolve().parents[3]
    return repo_root / "agent" / "reports" / "alpha_genesis"


def resolve_report_root(settings: Mapping[str, Any] | object | None = None) -> Path:
    """Resolve the report root once while constructing the application."""

    raw: Any = None
    if isinstance(settings, Mapping):
        raw = settings.get("VIBE_TRADING_ALPHA_GENESIS_REPORT_DIR")
    elif settings is not None:
        raw = getattr(settings, "VIBE_TRADING_ALPHA_GENESIS_REPORT_DIR", None)
    if raw is None:
        raw = os.getenv("VIBE_TRADING_ALPHA_GENESIS_REPORT_DIR")
    return Path(str(raw)) if raw else _default_report_root()


def _set_read_only_headers(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = "default-src 'none'; sandbox"
    response.headers["Referrer-Policy"] = "no-referrer"


def _read_artifact(
    reader: ReportArtifactReader,
    artifact_id: str,
    kind: ReportArtifactKind,
) -> dict[str, Any]:
    try:
        return reader.read(artifact_id, kind)
    except ReportPathError:
        raise HTTPException(status_code=400, detail="invalid artifact id") from None
    except ReportArtifactNotFound:
        raise HTTPException(status_code=404, detail="alpha genesis artifact not found") from None
    except ReportArtifactValidationError as exc:
        if "invalid JSON" in str(exc):
            raise HTTPException(
                status_code=500,
                detail="alpha genesis artifact is invalid JSON",
            ) from None
        if "must be a JSON object" in str(exc):
            raise HTTPException(status_code=500, detail=str(exc)) from None
        if "unknown report artifact schema" in str(exc):
            raise HTTPException(
                status_code=404,
                detail="alpha genesis artifact not found",
            ) from None
        raise HTTPException(status_code=422, detail=str(exc)) from None


def register_alpha_genesis_routes(
    app: FastAPI,
    require_auth: AuthDep | None = None,
    *,
    report_reader: ReportArtifactReader | None = None,
    report_root: str | Path | None = None,
    dossier_resolver: CanonicalDossierResolverV1 | None = None,
) -> None:
    if require_auth is None:
        import sys as _sys

        host = _sys.modules.get("api_server") or _sys.modules.get("agent.api_server")
        if host is None:
            raise RuntimeError(
                "register_alpha_genesis_routes: pass require_auth explicitly when api_server is not loaded"
            )
        require_auth = host.require_auth

    reader = report_reader or ReportArtifactReader(
        report_root if report_root is not None else resolve_report_root()
    )

    @app.get("/api/alpha-genesis/reports/{report_id}", dependencies=[Depends(require_auth)])
    async def get_alpha_genesis_report(report_id: str, response: Response) -> dict[str, Any]:
        _set_read_only_headers(response)
        return _read_artifact(reader, report_id, ReportArtifactKind.REPORT)

    @app.get("/api/alpha-genesis/scorecards/{candidate_id}", dependencies=[Depends(require_auth)])
    async def get_alpha_genesis_scorecard(candidate_id: str, response: Response) -> dict[str, Any]:
        _set_read_only_headers(response)
        return _read_artifact(reader, candidate_id, ReportArtifactKind.SCORECARD)

    @app.get("/api/alpha-genesis/quality-decisions/{candidate_id}", dependencies=[Depends(require_auth)])
    async def get_alpha_genesis_quality_decision(candidate_id: str, response: Response) -> dict[str, Any]:
        _set_read_only_headers(response)
        return _read_artifact(reader, candidate_id, ReportArtifactKind.DECISION)

    if dossier_resolver is not None:
        @app.get(
            "/api/alpha-genesis/dossiers/{factor_spec_id}",
            dependencies=[Depends(require_auth)],
        )
        async def get_canonical_research_dossier(
            factor_spec_id: str, response: Response
        ) -> dict[str, Any]:
            _set_read_only_headers(response)
            try:
                return dict(dossier_resolver.candidate(factor_spec_id))
            except LookupError:
                raise HTTPException(
                    status_code=404, detail="canonical research dossier not found"
                ) from None
            except (EventValidationError, ValueError):
                raise HTTPException(
                    status_code=422, detail="canonical research dossier is invalid"
                ) from None

        @app.get(
            "/api/alpha-genesis/dossiers/{factor_spec_id}/views/{audience}",
            dependencies=[Depends(require_auth)],
        )
        async def get_canonical_research_view(
            factor_spec_id: str, audience: str, response: Response
        ) -> dict[str, Any]:
            _set_read_only_headers(response)
            if audience not in AUDIENCES:
                raise HTTPException(status_code=400, detail="unknown dossier audience")
            try:
                return dict(
                    dossier_resolver.view(factor_spec_id, audience)  # type: ignore[arg-type]
                )
            except LookupError:
                raise HTTPException(
                    status_code=404, detail="canonical research dossier not found"
                ) from None
            except (EventValidationError, ValueError):
                raise HTTPException(
                    status_code=422, detail="canonical research dossier is invalid"
                ) from None
