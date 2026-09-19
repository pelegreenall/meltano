"""Tests for the preview and step-compilation endpoints."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.ui.services.preview import PreviewResult

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin

COMPILE = "/api/v1/transforms/compile"

ROWS = [
    {"id": 1, "name": "Ada", "status": "active"},
    {"id": 2, "name": "Bo", "status": "churned"},
]


def preview_url(plugin: ProjectPlugin) -> str:
    """Build the preview URL for a plugin.

    Args:
        plugin: The plugin to address.

    Returns:
        The URL.
    """
    return f"/api/v1/plugins/{plugin.type}/{plugin.name}/preview"


def stub(**overrides: t.Any) -> mock.AsyncMock:
    """Stand in for a tap run.

    Previewing spawns the extractor, which these tests are not about.

    Args:
        overrides: Fields to override on the result.

    Returns:
        An async mock returning a preview result.
    """
    defaults: dict[str, t.Any] = {
        "schemas": {"customers": ["id", "name", "status"]},
        "records": list(ROWS),
        "truncated": False,
        "timed_out": False,
        "error": None,
    }
    return mock.AsyncMock(return_value=PreviewResult(**{**defaults, **overrides}))


@pytest.mark.usefixtures("tap")
class TestPreview:
    """Reading rows out of an extractor."""

    def test_returns_rows_and_columns(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """The rows come back with a column order taken from the data."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(),
        ):
            body = ui_client.post(preview_url(tap), json={}).json()

        assert body["rows"] == ROWS
        assert body["columns"] == ["id", "name", "status"]
        assert body["row_count"] == 2
        assert body["read_count"] == 2

    def test_reports_every_stream_the_tap_announced(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Schemas arrive even for streams that produced no records."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(schemas={"customers": ["id"], "orders": ["ref"]}),
        ):
            body = ui_client.post(preview_url(tap), json={}).json()

        assert set(body["schemas"]) == {"customers", "orders"}

    def test_steps_are_applied_to_the_rows(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """The point of the loop: change a step, see different rows."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(),
        ):
            body = ui_client.post(
                preview_url(tap),
                json={
                    "steps": [
                        {"kind": "drop", "column": "status"},
                        {"kind": "rename", "column": "name", "to": "who"},
                    ],
                },
            ).json()

        assert body["rows"][0] == {"id": 1, "who": "Ada"}
        assert body["read_count"] == 2

    def test_a_filter_reports_both_counts(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Both counts are reported, so the UI can say "3 of 4 rows"."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(),
        ):
            body = ui_client.post(
                preview_url(tap),
                json={
                    "steps": [
                        {
                            "kind": "filter",
                            "column": "status",
                            "operator": "eq",
                            "value": "active",
                        },
                    ],
                },
            ).json()

        assert body["row_count"] == 1
        assert body["read_count"] == 2

    def test_the_compiled_stream_map_comes_back(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """What would be written to `meltano.yml`, shown while editing."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(),
        ):
            body = ui_client.post(
                preview_url(tap),
                json={"steps": [{"kind": "drop", "column": "status"}]},
            ).json()

        assert body["stream_map"] == {"status": None}

    def test_a_malformed_step_does_not_run_the_tap(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Steps compile first, so a typo costs nothing.

        Spawning an extractor takes seconds and may hit a rate-limited API;
        doing it before validating the request would be careless.
        """
        runner = stub()
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=runner,
        ):
            response = ui_client.post(
                preview_url(tap),
                json={"steps": [{"kind": "rename", "column": "a"}]},
            )

        assert response.status_code == 422
        runner.assert_not_awaited()

    def test_a_failing_extractor_is_a_400(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """The tap's own stderr is what explains the failure."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(records=[], schemas={}, error="Missing required setting"),
        ):
            response = ui_client.post(preview_url(tap), json={})

        assert response.status_code == 400
        assert "Missing required setting" in response.json()["detail"]

    def test_a_silent_extractor_times_out(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A tap that never emits must not hold the request forever."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(records=[], schemas={}, timed_out=True),
        ):
            response = ui_client.post(preview_url(tap), json={})

        assert response.status_code == 504

    def test_a_timeout_after_some_rows_still_returns_them(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A slow tap that produced something is more useful than an error."""
        with mock.patch(
            "meltano.ui.routers.transforms.preview_service.preview_records",
            new=stub(timed_out=True),
        ):
            body = ui_client.post(preview_url(tap), json={}).json()

        assert body["timed_out"] is True
        assert body["rows"] == ROWS

    def test_a_loader_cannot_be_previewed(
        self,
        ui_client: TestClient,
        target: ProjectPlugin,
    ) -> None:
        """Only extractors emit records."""
        response = ui_client.post(preview_url(target), json={})

        assert response.status_code == 422
        assert "only extractors emit records" in response.json()["detail"]

    def test_the_limit_is_bounded(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A preview is not a way to pull a whole table into a browser."""
        response = ui_client.post(preview_url(tap), json={"limit": 100_000})

        assert response.status_code == 422


class TestCompileEndpoint:
    """Compiling steps without running anything."""

    def test_compiles_steps(self, ui_client: TestClient) -> None:
        """Shows what will be written while the steps are still being edited."""
        body = ui_client.post(
            COMPILE,
            json={"steps": [{"kind": "cast", "column": "id", "type": "string"}]},
        ).json()

        assert body["stream_map"] == {
            "id": "(str(record['id']) if record['id'] is not None else None)",
        }

    def test_rejects_a_structurally_invalid_step(
        self,
        ui_client: TestClient,
    ) -> None:
        """A rename with no target reaches the compiler, which names it.

        The position matters: a step list is edited as a list, so "step 2" is
        how the UI knows which row to mark.
        """
        response = ui_client.post(
            COMPILE,
            json={
                "steps": [
                    {"kind": "drop", "column": "a"},
                    {"kind": "rename", "column": "b"},
                ],
            },
        )

        assert response.status_code == 422
        assert "step 2" in response.json()["detail"]

    def test_an_unknown_cast_is_rejected_by_the_schema(
        self,
        ui_client: TestClient,
    ) -> None:
        """Closed vocabularies are enforced before the compiler sees them.

        `CastType` is a literal, so pydantic refuses an unknown type without
        the request reaching any of our own code.
        """
        response = ui_client.post(
            COMPILE,
            json={"steps": [{"kind": "cast", "column": "id", "type": "date"}]},
        )

        assert response.status_code == 422

    def test_anonymous_compile_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Admission control applies here like anywhere else."""
        response = ui_client_anonymous.post(
            COMPILE,
            json={"steps": [{"kind": "drop", "column": "a"}]},
        )
        assert response.status_code == 401
