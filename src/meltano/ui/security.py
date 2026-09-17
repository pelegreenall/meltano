"""Authentication and request-origin checks for the Meltano UI.

A localhost server that spawns plugin subprocesses and reads project secrets is
functionally a remote-code-execution endpoint, so "it's only localhost" is not
a security model. Two independent controls apply to every request:

1. A per-launch bearer token, delivered once via the launch URL and then held
   in a ``SameSite=Strict`` cookie.
2. A ``Host`` header allowlist. This is the control that actually stops DNS
   rebinding, where a page on ``evil.com`` re-resolves its own hostname to
   127.0.0.1 and then talks to this server carrying the user's cookie.
"""

from __future__ import annotations

import secrets
import typing as t

from fastapi import HTTPException, Request, status

if t.TYPE_CHECKING:
    from meltano.ui.context import ServerContext

#: Name of the cookie holding the session token.
TOKEN_COOKIE = "meltano_ui_token"  # noqa: S105
#: Query parameter used to bootstrap the cookie on first load.
TOKEN_QUERY_PARAM = "token"  # noqa: S105

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def constant_time_eq(presented: str, expected: str) -> bool:
    """Compare two tokens without leaking their contents through timing.

    Args:
        presented: The value supplied by the caller.
        expected: The server's token.

    Returns:
        True if the values match.
    """
    return secrets.compare_digest(presented, expected)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def extract_token(request: Request) -> str | None:
    """Pull the caller's token from the request, if present.

    Checks the ``Authorization: Bearer`` header first so that scripts and
    ``curl`` work, then the session cookie, then the bootstrap query parameter.

    Args:
        request: The incoming request.

    Returns:
        The presented token, or None if the caller supplied none.
    """
    header = request.headers.get("authorization")
    if header:
        scheme, _, credentials = header.partition(" ")
        if scheme.lower() == "bearer" and credentials:
            return credentials.strip()

    if cookie := request.cookies.get(TOKEN_COOKIE):
        return cookie

    return request.query_params.get(TOKEN_QUERY_PARAM)


def check_host(request: Request, ctx: ServerContext) -> None:
    """Reject requests whose ``Host`` header is not an expected value.

    Args:
        request: The incoming request.
        ctx: The application context, holding the allowlist.

    Raises:
        HTTPException: 403 when the header is missing or unrecognized.
    """
    host = request.headers.get("host")
    if host is None or host not in ctx.settings.allowed_hosts:
        msg = f"Unexpected Host header: {host!r}"
        raise _forbidden(msg)


def check_origin(request: Request, ctx: ServerContext) -> None:
    """Reject cross-site requests that attempt to mutate state.

    The SPA is served from the same origin as the API, so there is no
    legitimate cross-origin caller and no CORS middleware is installed.

    Args:
        request: The incoming request.
        ctx: The application context, holding the expected origin.

    Raises:
        HTTPException: 403 when the request is cross-site.
    """
    if request.headers.get("sec-fetch-site") == "cross-site":
        msg = "Cross-site requests are not permitted"
        raise _forbidden(msg)

    if request.method in _SAFE_METHODS:
        return

    origin = request.headers.get("origin")
    if origin is not None and origin != ctx.settings.origin:
        msg = f"Unexpected Origin header: {origin!r}"
        raise _forbidden(msg)


def check_token(request: Request, ctx: ServerContext) -> None:
    """Validate the caller's token in constant time.

    Args:
        request: The incoming request.
        ctx: The application context, holding the expected token.

    Raises:
        HTTPException: 401 when the token is absent or wrong.
    """
    presented = extract_token(request)
    if presented is None:
        msg = "Missing authentication token"
        raise _unauthorized(msg)

    if not constant_time_eq(presented, ctx.settings.token):
        msg = "Invalid authentication token"
        raise _unauthorized(msg)


def check_writable(request: Request, ctx: ServerContext) -> None:
    """Refuse mutating requests when the server is read-only.

    Args:
        request: The incoming request.
        ctx: The application context.

    Raises:
        HTTPException: 403 when a mutation is attempted in read-only mode.
    """
    if request.method not in _SAFE_METHODS and ctx.readonly:
        msg = "This Meltano UI server is running in read-only mode"
        raise _forbidden(msg)


def set_token_cookie(response: t.Any, token: str) -> None:  # noqa: ANN401
    """Attach the session cookie to a response.

    ``secure`` is deliberately False: the server is plain HTTP on loopback, and
    a ``Secure`` cookie would never be sent back.

    Args:
        response: The response to modify.
        token: The token to store.
    """
    response.set_cookie(
        TOKEN_COOKIE,
        token,
        httponly=True,
        samesite="strict",
        secure=False,
        path="/",
    )
