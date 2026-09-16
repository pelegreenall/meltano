"""Liveness and server-description endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meltano.core.utils import get_meltano_version
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.schemas.meta import Health, ServerMeta

router = APIRouter(tags=["meta"])


@router.get("/health", response_model=Health)
def health() -> Health:
    """Report that the server is up.

    Deliberately unauthenticated so that a launcher can poll for readiness
    without holding the token. It discloses nothing about the project.

    Returns:
        A fixed ok payload.
    """
    return Health(status="ok")


@router.get("/meta", response_model=ServerMeta, dependencies=[Depends(require_auth)])
def server_meta(ctx: CtxDep) -> ServerMeta:
    """Describe the running server and its project.

    Args:
        ctx: The application context.

    Returns:
        Version, project root, active environment and read-only status.
    """
    return ServerMeta(
        meltano_version=get_meltano_version(),
        project_root=str(ctx.project.root),
        environment=ctx.environment_name,
        readonly=ctx.readonly,
        host=ctx.settings.host,
        port=ctx.settings.port,
    )
