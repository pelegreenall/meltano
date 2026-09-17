"""FastAPI dependencies for the Meltano UI.

Every handler reaches shared state through these, never through module-level
globals. That keeps the app constructible per-test and avoids recreating the
``Project._default`` problem one layer up.
"""

from __future__ import annotations

import typing as t

from fastapi import Depends, Request
from sqlalchemy.orm import Session

# `Session`, `Project` and `AppContext` must be imported at runtime, not merely
# for type checking: FastAPI resolves handler annotations with
# `get_type_hints`, and an unresolvable name inside `Annotated[...]` silently
# degrades the parameter into a query field instead of raising.
from meltano.core.project import Project
from meltano.ui import security
from meltano.ui.context import AppContext, SetupContext

if t.TYPE_CHECKING:
    from collections.abc import Iterator


def get_ctx(request: Request) -> AppContext:
    """Return the application context, current with `meltano.yml` on disk.

    FastAPI resolves a dependency once per request, so the staleness check runs
    exactly once no matter how many handlers ask for the context. The project
    object is never re-activated; only its parsed-config caches are dropped,
    and only when the file actually changed, so that edits made outside the
    server (`meltano add` in a terminal, say) appear without a restart.

    Args:
        request: The incoming request.

    Returns:
        The context stored on the app at startup.
    """
    ctx = t.cast("AppContext", request.app.state.ctx)
    ctx.watcher.refresh_if_stale()
    return ctx


#: Convenience aliases for handler signatures. Defined here so that handlers
#: below can use them; `from __future__ import annotations` makes the forward
#: reference in the remaining aliases resolvable at route-build time.
CtxDep = t.Annotated[AppContext, Depends(get_ctx)]


def get_project(ctx: CtxDep) -> Project:
    """Return the server's single activated project.

    Args:
        ctx: The application context, already checked for staleness.

    Returns:
        The project.
    """
    return ctx.project


def get_session(ctx: CtxDep) -> Iterator[Session]:
    """Yield a system-database session.

    Meltano's engine is synchronous with a `NullPool`, so handlers that use
    this should be declared with `def` rather than `async def` and will be run
    in FastAPI's threadpool.

    Args:
        ctx: The application context.

    Yields:
        A session, closed when the request ends.
    """
    session = ctx.session_factory()
    try:
        yield session
    finally:
        session.close()


def require_auth(request: Request, ctx: CtxDep) -> None:
    """Enforce the full request-admission policy.

    Order matters: the host check runs first so that a rebinding attempt is
    refused before any token comparison happens.

    Args:
        request: The incoming request.
        ctx: The application context.
    """
    security.check_host(request, ctx)
    security.check_origin(request, ctx)
    security.check_token(request, ctx)
    security.check_writable(request, ctx)


def get_setup_ctx(request: Request) -> SetupContext:
    """Return the context of a server that has no project yet.

    Setup mode cannot use `get_ctx`: there is no project to re-read and no
    `ProjectWatcher` to ask about staleness.

    Args:
        request: The incoming request.

    Returns:
        The context stored on the app at startup.
    """
    return t.cast("SetupContext", request.app.state.ctx)


SetupCtxDep = t.Annotated[SetupContext, Depends(get_setup_ctx)]


def require_setup_auth(request: Request, ctx: SetupCtxDep) -> None:
    """Enforce the same admission policy on the setup server.

    A server sitting on the setup screen is still an open port that can write
    to the filesystem, so it is gated exactly as the full server is.

    Args:
        request: The incoming request.
        ctx: The setup context.
    """
    security.check_host(request, ctx)
    security.check_origin(request, ctx)
    security.check_token(request, ctx)
    security.check_writable(request, ctx)


ProjectDep = t.Annotated[Project, Depends(get_project)]
SessionDep = t.Annotated[Session, Depends(get_session)]

__all__ = [
    "CtxDep",
    "ProjectDep",
    "SessionDep",
    "SetupCtxDep",
    "get_ctx",
    "get_project",
    "get_session",
    "get_setup_ctx",
    "require_auth",
    "require_setup_auth",
]
