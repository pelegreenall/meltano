"""Fixtures for the Meltano UI tests."""

from __future__ import annotations

import typing as t

import pytest

from meltano.ui.app import create_app
from meltano.ui.context import AppContext
from meltano.ui.settings import UIServerSettings

if t.TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

    from meltano.core.project import Project

#: Fixed so tests can assert on it; a real server generates a random one.
TEST_TOKEN = "test-token-not-random"  # noqa: S105

#: `TestClient` sends this as the `Host` header.
TEST_HOST = "testserver"


@pytest.fixture
def test_token() -> str:
    """Return the fixed token used across UI tests.

    Returns:
        The token.
    """
    return TEST_TOKEN


@pytest.fixture
def test_host() -> str:
    """Return the Host header the ASGI test client sends.

    Returns:
        The host.
    """
    return TEST_HOST


@pytest.fixture
def ui_settings() -> UIServerSettings:
    """Return deterministic server settings.

    Returns:
        Settings with a fixed token and an allowlist covering the test client.
    """
    return UIServerSettings(
        host="127.0.0.1",
        port=5001,
        open_browser=False,
        token=TEST_TOKEN,
        extra_allowed_hosts=frozenset({TEST_HOST}),
    )


@pytest.fixture
def ui_context(project: Project, ui_settings: UIServerSettings) -> AppContext:
    """Build an application context over the standard project fixture.

    Args:
        project: The test project.
        ui_settings: The server settings.

    Returns:
        A context suitable for `create_app`.
    """
    return AppContext.create(project, ui_settings)


@pytest.fixture
def ui_app(ui_context: AppContext) -> FastAPI:
    """Build the FastAPI application under test.

    Args:
        ui_context: The application context.

    Returns:
        The configured application.
    """
    return create_app(ui_context)


@pytest.fixture
def ui_client(ui_app: FastAPI) -> Iterator[TestClient]:
    """Yield an authenticated client.

    The real `require_auth` chain runs on every request; only the host
    allowlist is widened, via settings, so admission is genuinely exercised.

    Args:
        ui_app: The application under test.

    Yields:
        An authenticated client.
    """
    from fastapi.testclient import TestClient

    with TestClient(
        ui_app,
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    ) as client:
        yield client


@pytest.fixture
def ui_client_with_history(ui_app: FastAPI, session: Session) -> Iterator[TestClient]:
    """Yield an authenticated client whose handlers see the test session.

    The `session` fixture runs inside a transaction that is rolled back, so
    rows it writes are invisible to a session opened on another connection -
    which is what `get_session` would otherwise hand the handler. Overriding
    the dependency points the handlers at the same connection, so tests can
    seed run history and then read it back over HTTP.

    Args:
        ui_app: The application under test.
        session: The test database session.

    Yields:
        An authenticated client sharing the test transaction.
    """
    from fastapi.testclient import TestClient

    from meltano.ui.deps import get_session

    ui_app.dependency_overrides[get_session] = lambda: session
    try:
        with TestClient(
            ui_app,
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        ) as client:
            yield client
    finally:
        ui_app.dependency_overrides.clear()


@pytest.fixture
def ui_client_anonymous(ui_app: FastAPI) -> Iterator[TestClient]:
    """Yield a client that presents no credentials.

    Args:
        ui_app: The application under test.

    Yields:
        An unauthenticated client.
    """
    from fastapi.testclient import TestClient

    with TestClient(ui_app) as client:
        yield client
