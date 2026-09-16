"""HTTP routers for the Meltano UI, one module per domain."""

# ruff: file-ignore[non-empty-init-module]

from __future__ import annotations

from fastapi import APIRouter

from meltano.ui.routers import config, meta, plugins, project, runs

#: Versioned from the first release: the previous Meltano API's lack of
#: versioning is a large part of why it could not be evolved.
API_PREFIX = "/api/v1"

api_router = APIRouter(prefix=API_PREFIX)
api_router.include_router(config.router)
api_router.include_router(meta.router)
api_router.include_router(plugins.router)
api_router.include_router(project.router)
api_router.include_router(runs.router)

__all__ = ["API_PREFIX", "api_router"]
