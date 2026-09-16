"""Tests for the UI's request-admission policy."""

from __future__ import annotations

import typing as t

import pytest
from fastapi.testclient import TestClient

from meltano.ui.app import create_app
from meltano.ui.context import AppContext
from meltano.ui.settings import UIServerSettings

if t.TYPE_CHECKING:
    from fastapi import FastAPI

    from meltano.core.project import Project

META = "/api/v1/meta"


class TestTokenAuth:
    """The per-launch bearer token."""

    def test_health_needs_no_token(self, ui_client_anonymous: TestClient) -> None:
        """Readiness probing must not require credentials."""
        response = ui_client_anonymous.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_missing_token_is_rejected(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """A request with no credentials is refused."""
        response = ui_client_anonymous.get(META)
        assert response.status_code == 401
        assert response.json()["detail"] == "Missing authentication token"

    def test_wrong_token_is_rejected(self, ui_client_anonymous: TestClient) -> None:
        """A request with the wrong token is refused."""
        response = ui_client_anonymous.get(
            META,
            headers={"Authorization": "Bearer wrong"},
        )
        assert response.status_code == 401
        assert response.json()["detail"] == "Invalid authentication token"

    def test_bearer_token_is_accepted(self, ui_client: TestClient) -> None:
        """The correct token grants access."""
        assert ui_client.get(META).status_code == 200

    def test_cookie_is_accepted(
        self,
        ui_client_anonymous: TestClient,
        test_token: str,
    ) -> None:
        """The session cookie is an equally valid credential."""
        ui_client_anonymous.cookies.set("meltano_ui_token", test_token)
        assert ui_client_anonymous.get(META).status_code == 200

    def test_index_exchanges_token_for_cookie(
        self,
        ui_client_anonymous: TestClient,
        test_token: str,
    ) -> None:
        """Loading the SPA with a bootstrap token sets the session cookie."""
        response = ui_client_anonymous.get(
            f"/?token={test_token}",
            follow_redirects=False,
        )
        assert response.status_code == 200
        assert response.cookies.get("meltano_ui_token") == test_token

    def test_index_ignores_a_bad_bootstrap_token(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """A wrong bootstrap token must not mint a session."""
        response = ui_client_anonymous.get("/?token=wrong", follow_redirects=False)
        assert response.cookies.get("meltano_ui_token") is None


class TestHostAllowlist:
    """The DNS-rebinding defense."""

    def test_foreign_host_header_is_rejected(self, ui_client: TestClient) -> None:
        """A rebinding attempt is refused even with a valid token."""
        response = ui_client.get(META, headers={"Host": "evil.com"})
        assert response.status_code == 403
        assert "Unexpected Host header" in response.json()["detail"]

    def test_real_allowlist_rejects_the_test_client(
        self,
        project: Project,
        test_token: str,
        test_host: str,
    ) -> None:
        """Without the test widening, `Host: testserver` is refused.

        This is the one test that exercises the shipping allowlist rather than
        the widened one, so the fixture's convenience cannot mask a regression.
        """
        settings = UIServerSettings(
            host="127.0.0.1",
            port=5001,
            open_browser=False,
            token=test_token,
        )
        app = create_app(AppContext.create(project, settings))
        with TestClient(app) as client:
            response = client.get(
                META,
                headers={"Authorization": f"Bearer {test_token}"},
            )
        assert response.status_code == 403
        assert test_host in response.json()["detail"]

    def test_loopback_variants_are_allowed(self, ui_settings: UIServerSettings) -> None:
        """Every loopback spelling, with and without the port, is accepted."""
        allowed = ui_settings.allowed_hosts
        assert "127.0.0.1" in allowed
        assert "127.0.0.1:5001" in allowed
        assert "localhost:5001" in allowed
        assert "[::1]:5001" in allowed


class TestOriginChecks:
    """Cross-site request rejection."""

    def test_cross_site_fetch_is_rejected(self, ui_client: TestClient) -> None:
        """`Sec-Fetch-Site: cross-site` is refused outright."""
        response = ui_client.get(META, headers={"Sec-Fetch-Site": "cross-site"})
        assert response.status_code == 403

    def test_foreign_origin_on_mutation_is_rejected(
        self,
        ui_client: TestClient,
    ) -> None:
        """A mutating request from another origin is refused."""
        response = ui_client.post(
            "/api/v1/runs",
            json={"blocks": ["tap-mock"]},
            headers={"Origin": "http://evil.com"},
        )
        assert response.status_code == 403

    def test_foreign_origin_on_read_is_allowed(self, ui_client: TestClient) -> None:
        """Safe methods are not origin-checked; the token still gates them."""
        response = ui_client.get(META, headers={"Origin": "http://evil.com"})
        assert response.status_code == 200


class TestReadonlyMode:
    """`--readonly` and `MELTANO_PROJECT_READONLY`."""

    @pytest.fixture
    def readonly_app(
        self,
        project: Project,
        test_token: str,
        test_host: str,
    ) -> FastAPI:
        """Build an app in read-only mode."""
        settings = UIServerSettings(
            host="127.0.0.1",
            port=5001,
            open_browser=False,
            readonly=True,
            token=test_token,
            extra_allowed_hosts=frozenset({test_host}),
        )
        return create_app(AppContext.create(project, settings))

    def test_reads_are_permitted(
        self,
        readonly_app: FastAPI,
        test_token: str,
    ) -> None:
        """Read-only mode still serves GETs."""
        with TestClient(
            readonly_app,
            headers={"Authorization": f"Bearer {test_token}"},
        ) as client:
            response = client.get(META)
        assert response.status_code == 200
        assert response.json()["readonly"] is True

    def test_mutations_are_refused(
        self,
        readonly_app: FastAPI,
        test_token: str,
    ) -> None:
        """Read-only mode refuses every non-safe method."""
        with TestClient(
            readonly_app,
            headers={"Authorization": f"Bearer {test_token}"},
        ) as client:
            response = client.post("/api/v1/runs", json={"blocks": ["tap-mock"]})
        assert response.status_code == 403
        assert "read-only" in response.json()["detail"]


class TestDocsAreGuarded:
    """The schema enumerates the attack surface, so it is not public."""

    @pytest.mark.parametrize("path", ("/docs", "/api/v1/openapi.json"))
    def test_docs_require_auth(
        self,
        ui_client_anonymous: TestClient,
        path: str,
    ) -> None:
        """Anonymous callers cannot read the API documentation."""
        assert ui_client_anonymous.get(path).status_code == 401

    @pytest.mark.parametrize("path", ("/docs", "/api/v1/openapi.json"))
    def test_docs_served_when_authenticated(
        self,
        ui_client: TestClient,
        path: str,
    ) -> None:
        """Authenticated callers get the documentation."""
        assert ui_client.get(path).status_code == 200
