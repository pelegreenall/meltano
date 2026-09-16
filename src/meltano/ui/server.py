"""Uvicorn wiring for the Meltano UI server."""

from __future__ import annotations

import typing as t
import webbrowser

import structlog
import uvicorn

from meltano.ui.app import create_app
from meltano.ui.context import AppContext

if t.TYPE_CHECKING:
    from meltano.core.project import Project
    from meltano.ui.settings import UIServerSettings

logger = structlog.stdlib.get_logger(__name__)


def build_server(ctx: AppContext) -> uvicorn.Server:
    """Configure a uvicorn server for the given context.

    Args:
        ctx: The application context.

    Returns:
        An unstarted server.
    """
    config = uvicorn.Config(
        create_app(ctx),
        host=ctx.settings.host,
        port=ctx.settings.port,
        # Meltano configures structlog on the root logger in `setup_logging`.
        # Letting uvicorn install its own dictConfig would silently undo that
        # and break `--log-format=json`.
        log_config=None,
        access_log=False,
    )
    return uvicorn.Server(config)


async def serve(project: Project, settings: UIServerSettings) -> None:
    """Run the UI server until it is interrupted.

    Args:
        project: The already-activated project to serve.
        settings: Launch-time settings.
    """
    ctx = AppContext.create(project, settings)
    server = build_server(ctx)

    logger.info(
        "Starting Meltano UI",
        host=settings.host,
        port=settings.port,
        environment=ctx.environment_name,
        readonly=ctx.readonly,
    )

    if settings.open_browser:
        # Opened before `serve()` blocks; the browser will retry until the
        # socket is accepting.
        webbrowser.open(settings.url)

    await server.serve()
