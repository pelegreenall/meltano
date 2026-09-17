"""Uvicorn wiring for the Meltano UI server."""

from __future__ import annotations

import typing as t
import webbrowser

import structlog
import uvicorn

from meltano.ui.app import create_app, create_setup_app
from meltano.ui.context import AppContext, SetupContext

if t.TYPE_CHECKING:
    from pathlib import Path

    from meltano.core.project import Project
    from meltano.ui.settings import UIServerSettings

logger = structlog.stdlib.get_logger(__name__)


def _build(app: t.Any, settings: UIServerSettings) -> uvicorn.Server:  # noqa: ANN401
    """Configure a uvicorn server for an already-built application.

    Args:
        app: The ASGI application to serve.
        settings: Launch-time settings.

    Returns:
        An unstarted server.
    """
    config = uvicorn.Config(
        app,
        host=settings.host,
        port=settings.port,
        # Meltano configures structlog on the root logger in `setup_logging`.
        # Letting uvicorn install its own dictConfig would silently undo that
        # and break `--log-format=json`.
        log_config=None,
        access_log=False,
    )
    return uvicorn.Server(config)


def build_server(ctx: AppContext) -> uvicorn.Server:
    """Configure a uvicorn server for the given context.

    Args:
        ctx: The application context.

    Returns:
        An unstarted server.
    """
    return _build(create_app(ctx), ctx.settings)


async def serve_setup(settings: UIServerSettings) -> Path | None:
    """Run the setup server until a project is chosen or the user gives up.

    The chosen project is *not* served by this process's server. Starting a
    fresh one is what keeps `Project.activate()` and the default engine from
    being set twice in one process.

    Args:
        settings: Launch-time settings.

    Returns:
        The chosen project's root, or None if the server was interrupted
        before a choice was made.
    """
    ctx = SetupContext(settings=settings)
    server = _build(create_setup_app(ctx), settings)
    ctx.server = server

    logger.info(
        "Starting Meltano UI without a project",
        host=settings.host,
        port=settings.port,
    )

    if settings.open_browser:
        webbrowser.open(settings.url)

    await server.serve()
    return ctx.project_path


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
