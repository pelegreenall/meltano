"""Tests for application assembly and the SPA fallback."""

from __future__ import annotations

import typing as t

import pytest

from meltano.ui import app as app_module

if t.TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from meltano.ui.context import AppContext


class TestMetaEndpoints:
    """`/health`, `/meta` and `/project`."""

    def test_meta_describes_the_server(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """`/meta` reports the project and server it is bound to."""
        payload = ui_client.get("/api/v1/meta").json()
        assert payload["project_root"] == str(ui_context.project.root)
        assert payload["readonly"] is False
        assert payload["port"] == 5001
        assert "meltano_version" in payload

    def test_meta_never_leaks_the_token(self, ui_client: TestClient) -> None:
        """The auth token must not appear in any response body."""
        body = ui_client.get("/api/v1/meta").text
        assert "test-token-not-random" not in body

    def test_project_lists_environments(self, ui_client: TestClient) -> None:
        """`/project` enumerates the environments defined in meltano.yml."""
        payload = ui_client.get("/api/v1/project").json()
        assert isinstance(payload["environments"], list)
        assert payload["root"]


class TestSpaFallback:
    """Client-side routes must survive a page reload."""

    @pytest.mark.parametrize("path", ("/", "/runs", "/plugins/extractors/tap-mock"))
    def test_unknown_paths_serve_the_shell(
        self,
        ui_client_anonymous: TestClient,
        path: str,
    ) -> None:
        """Any non-API path returns the SPA shell, not a 404."""
        response = ui_client_anonymous.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    def test_placeholder_is_served_when_unbuilt(
        self,
        ui_client_anonymous: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """A checkout without a frontend build explains how to make one.

        The paths are patched rather than read from disk so the result does not
        depend on whether the developer running the suite has built the SPA.
        """
        monkeypatch.setattr(app_module, "INDEX_FILE", tmp_path / "absent.html")
        monkeypatch.setattr(app_module, "MISSING_FILE", tmp_path / "absent.html")

        body = ui_client_anonymous.get("/").text
        assert "has not been built" in body

    def test_built_spa_is_served_when_present(
        self,
        ui_client_anonymous: TestClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Once built, the real SPA shell is served instead."""
        index = tmp_path / "index.html"
        index.write_text("<!doctype html><title>Meltano</title>")
        monkeypatch.setattr(app_module, "INDEX_FILE", index)

        body = ui_client_anonymous.get("/").text
        assert "has not been built" not in body
        assert "Meltano" in body

    def test_api_404s_are_not_swallowed_by_the_fallback(
        self,
        ui_client: TestClient,
    ) -> None:
        """An unknown API route must not silently return HTML."""
        response = ui_client.get("/api/v1/does-not-exist")
        assert response.status_code == 404
        assert "text/html" not in response.headers.get("content-type", "")


class TestOpenApiContract:
    """The schema is the frontend's generated contract."""

    def test_documents_the_expected_routes(self, ui_client: TestClient) -> None:
        """Every domain router is reachable and documented."""
        paths = ui_client.get("/api/v1/openapi.json").json()["paths"]
        for expected in (
            "/api/v1/health",
            "/api/v1/meta",
            "/api/v1/project",
            "/api/v1/runs",
            "/api/v1/runs/{run_id}",
            "/api/v1/runs/{run_id}/events",
        ):
            assert expected in paths, f"{expected} missing from the schema"

    def test_internal_routes_are_hidden(self, ui_client: TestClient) -> None:
        """The SPA fallback must not appear in the public schema."""
        paths = ui_client.get("/api/v1/openapi.json").json()["paths"]
        assert "/{full_path}" not in paths
        assert "/docs" not in paths
