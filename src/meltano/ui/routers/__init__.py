"""HTTP routers for the Meltano UI, one module per domain."""

# ruff: file-ignore[non-empty-init-module]

from __future__ import annotations

from fastapi import APIRouter

from meltano.ui.routers import (
    config,
    hub,
    jobs,
    meta,
    plugins,
    project,
    runs,
    schedules,
    select,
    sources,
    state,
    transforms,
)

#: Versioned from the first release: the previous Meltano API's lack of
#: versioning is a large part of why it could not be evolved.
API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(config.router)
api_router.include_router(hub.router)
api_router.include_router(jobs.router)
api_router.include_router(meta.router)
api_router.include_router(plugins.router)
api_router.include_router(project.router)
api_router.include_router(runs.router)
api_router.include_router(schedules.router)
api_router.include_router(select.router)
api_router.include_router(sources.router)
api_router.include_router(state.router)
api_router.include_router(transforms.router)

__all__ = ["API_PREFIX", "api_router"]
