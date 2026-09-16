"""Project-level endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meltano.core.environment_service import EnvironmentService
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.schemas.meta import ProjectInfo

router = APIRouter(tags=["project"], dependencies=[Depends(require_auth)])


@router.get("/project", response_model=ProjectInfo)
def read_project(ctx: CtxDep) -> ProjectInfo:
    """Summarize the project the server is bound to.

    Args:
        ctx: The application context.

    Returns:
        Root path, read-only status, and the available environments.
    """
    environments = EnvironmentService(ctx.project).list_environments()
    return ProjectInfo(
        root=str(ctx.project.root),
        readonly=ctx.readonly,
        environment=ctx.environment_name,
        environments=sorted(env.name for env in environments),
    )
