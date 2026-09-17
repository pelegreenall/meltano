"""FastAPI application factory for the Meltano UI."""

from __future__ import annotations

import typing as t
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from meltano.core.utils import get_meltano_version
from meltano.ui import security
from meltano.ui.deps import require_auth
from meltano.ui.errors import install_exception_handlers
from meltano.ui.routers import API_PREFIX, api_router
from meltano.ui.routers import setup as setup_router

if t.TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from starlette.responses import Response

    from meltano.ui.context import AppContext, SetupContext

logger = structlog.stdlib.get_logger(__name__)

#: Vite build output. Gitignored, and absent in a source checkout that has not
#: run `nox -s ui_build`.
STATIC_DIR = Path(__file__).parent / "static"
INDEX_FILE = STATIC_DIR / "index.html"
MISSING_FILE = STATIC_DIR / "MISSING.html"

OPENAPI_URL = f"{API_PREFIX}/openapi.json"
DOCS_URL = "/docs"

_MISSING_FALLBACK = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Meltano UI</title></head>
<body>
<h1>The Meltano UI frontend has not been built</h1>
<p>The API is running and fully functional under <code>/api/v1</code>.
Interactive docs are at <a href="/docs">/docs</a>.</p>
<p>To build the frontend, run <code>nox -s ui_build</code>.</p>
</body></html>
"""


def _index_response() -> Response:
    """Return the SPA shell, or an explanation if it was never built.

    Returns:
        A response serving `index.html`, the build-missing placeholder, or a
        built-in fallback when neither file exists.
    """
    if INDEX_FILE.is_file():
        return FileResponse(INDEX_FILE)
    if MISSING_FILE.is_file():
        return FileResponse(MISSING_FILE)
    return HTMLResponse(_MISSING_FALLBACK)


def _mount_spa(app: FastAPI, token: str) -> None:
    """Serve the built frontend, and exchange a bootstrap token for a cookie.

    Shared by both apps: the setup screen is the same single-page application,
    reached the same way, and needs the same cookie to call its own endpoints.

    Args:
        app: The application to configure.
        token: The server's auth token.
    """

    @app.get("/", include_in_schema=False)
    def index(request: Request) -> Response:
        """Serve the SPA shell, exchanging a bootstrap token for a cookie."""
        response = _index_response()
        presented = request.query_params.get(security.TOKEN_QUERY_PARAM)
        if presented is not None and security.constant_time_eq(presented, token):
            security.set_token_cookie(response, presented)
        return response

    assets_dir = STATIC_DIR / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    # Registered last: any path that is not an API route is handed to the SPA
    # so that client-side routing survives a page reload.
    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str) -> Response:
        """Serve the SPA shell for client-side routes.

        Unmatched API paths are excluded: returning the HTML shell with a 200
        for a mistyped endpoint would turn an obvious 404 into a confusing
        parse error in the client.
        """
        if f"/{full_path}".startswith(f"{API_PREFIX}/"):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Unknown API endpoint",
            )
        return _index_response()


def create_setup_app(ctx: SetupContext) -> FastAPI:
    """Build the application a server shows when it has no project.

    Only the setup routes exist here. Every other router dereferences
    `ctx.project`, so mounting them against a projectless context would turn
    each one into a crash rather than a clear "choose a project first".

    Args:
        ctx: The setup context, stored on `app.state`.

    Returns:
        The configured application.
    """
    app = FastAPI(
        title="Meltano UI (setup)",
        version=get_meltano_version(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
    )
    app.state.ctx = ctx

    install_exception_handlers(app)
    app.include_router(setup_router.router, prefix=API_PREFIX)
    _mount_spa(app, ctx.settings.token)

    return app


def create_app(ctx: AppContext) -> FastAPI:
    """Build the ASGI application for a given context.

    Args:
        ctx: The server's resolved state. Stored on `app.state` and reached
            only through dependencies, never through module globals.

    Returns:
        The configured application.
    """

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        """Re-adopt subprocesses that outlived a previous server process."""
        if recovered := ctx.run_manager.recover():
            logger.info("Recovered previous UI runs", count=len(recovered))
        yield

    # The generated schema enumerates the whole attack surface, so the built-in
    # unauthenticated docs routes are disabled and re-registered behind
    # `require_auth` below.
    app = FastAPI(
        title="Meltano UI",
        version=get_meltano_version(),
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.ctx = ctx

    install_exception_handlers(app)
    app.include_router(api_router)

    @app.get(OPENAPI_URL, include_in_schema=False, dependencies=[Depends(require_auth)])
    def openapi_schema() -> JSONResponse:
        """Serve the OpenAPI schema to authenticated callers."""
        return JSONResponse(app.openapi())

    @app.get(DOCS_URL, include_in_schema=False, dependencies=[Depends(require_auth)])
    def swagger_ui() -> HTMLResponse:
        """Serve Swagger UI to authenticated callers."""
        return get_swagger_ui_html(openapi_url=OPENAPI_URL, title="Meltano UI API")

    _mount_spa(app, ctx.settings.token)

    return app


__all__ = [
    "DOCS_URL",
    "OPENAPI_URL",
    "STATIC_DIR",
    "create_app",
    "create_setup_app",
]
