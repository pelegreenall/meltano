"""Translation of Meltano's exceptions into HTTP responses."""

from __future__ import annotations

import typing as t

from fastapi import status
from fastapi.responses import JSONResponse

from meltano.core.error import MeltanoError, ProjectReadonly
from meltano.core.plugin.error import PluginNotFoundError

if t.TYPE_CHECKING:
    from fastapi import FastAPI, Request


def _payload(exc: Exception, *, code: str) -> dict[str, t.Any]:
    reason = getattr(exc, "reason", None)
    instruction = getattr(exc, "instruction", None)
    return {
        "detail": str(reason) if reason is not None else str(exc),
        "instruction": instruction,
        "code": code,
    }


def meltano_error_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render a `MeltanoError` as a structured JSON body.

    The two-part reason/instruction shape is preserved so the UI can show the
    remediation step the CLI would have printed.

    Args:
        _: The request, unused.
        exc: The raised error.

    Returns:
        A 400 response describing the error.
    """
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=_payload(exc, code="meltano_error"),
    )


def project_readonly_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render a `ProjectReadonly` error as a 403.

    Args:
        _: The request, unused.
        exc: The raised error.

    Returns:
        A 403 response.
    """
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=_payload(exc, code="project_readonly"),
    )


def not_found_handler(_: Request, exc: Exception) -> JSONResponse:
    """Render a missing-plugin error as a 404.

    Args:
        _: The request, unused.
        exc: The raised error.

    Returns:
        A 404 response.
    """
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content=_payload(exc, code="not_found"),
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Register every Meltano-specific exception handler on the app.

    More specific subclasses are registered after their base classes;
    Starlette resolves handlers by walking the MRO, so ordering here is not
    load-bearing, but the explicit list documents what is mapped.

    Args:
        app: The application to configure.
    """
    app.add_exception_handler(MeltanoError, meltano_error_handler)
    app.add_exception_handler(ProjectReadonly, project_readonly_handler)
    app.add_exception_handler(PluginNotFoundError, not_found_handler)
