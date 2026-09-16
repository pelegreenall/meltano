"""Tests for the run supervision endpoints."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.ui.routers.runs import UnknownBlockError, validate_blocks
from meltano.ui.services.run_manager import RunKind, RunRecord, RunStatus

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project
    from meltano.ui.context import AppContext

RUNS = "/api/v1/runs"


def make_record(run_id: str = "r1", **overrides: t.Any) -> RunRecord:
    """Build a run record for stubbing the manager.

    Args:
        run_id: The record's identifier.
        overrides: Fields to override.

    Returns:
        The record.
    """
    defaults: dict[str, t.Any] = {
        "run_id": run_id,
        "kind": RunKind.run,
        "argv": ["meltano", "run", "tap-mock"],
        "status": RunStatus.running,
        "started_at": "2026-01-01T00:00:00+00:00",
        "log_path": "/tmp/run.log",  # noqa: S108
    }
    return RunRecord(**{**defaults, **overrides})


class TestValidateBlocks:
    """The guard that keeps `POST /runs` from executing arbitrary commands."""

    def test_rejects_an_unknown_name(self, project: Project) -> None:
        """A name that is not in the project is refused."""
        with pytest.raises(UnknownBlockError):
            validate_blocks(project, ["rm -rf /"])

    def test_rejects_a_shell_string(self, project: Project) -> None:
        """Shell metacharacters are not a bypass; names are matched exactly."""
        with pytest.raises(UnknownBlockError):
            validate_blocks(project, ["tap-mock; curl evil.com"])

    def test_accepts_an_installed_plugin(
        self,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """A plugin declared in meltano.yml is permitted."""
        validate_blocks(project, [tap.name])

    def test_accepts_a_plugin_command(
        self,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """`plugin:command` is resolved against the plugin name."""
        validate_blocks(project, [f"{tap.name}:some-command"])


class TestRunEndpoints:
    """Listing, fetching and cancelling."""

    def test_listing_is_empty_initially(self, ui_client: TestClient) -> None:
        """A fresh server reports no runs."""
        assert ui_client.get(RUNS).json() == []

    def test_unknown_run_is_404(self, ui_client: TestClient) -> None:
        """Fetching a nonexistent run is a clean 404."""
        assert ui_client.get(f"{RUNS}/nope").status_code == 404

    def test_unknown_run_log_is_404(self, ui_client: TestClient) -> None:
        """Log retrieval does not leak arbitrary files."""
        assert ui_client.get(f"{RUNS}/nope/log").status_code == 404

    def test_unknown_run_stream_is_404(self, ui_client: TestClient) -> None:
        """Streaming a nonexistent run is refused up front."""
        assert ui_client.get(f"{RUNS}/nope/events").status_code == 404

    def test_cancelling_unknown_run_is_404(self, ui_client: TestClient) -> None:
        """Cancelling something unknown is a 404, not a 500."""
        assert ui_client.delete(f"{RUNS}/nope").status_code == 404

    def test_starting_a_run_rejects_unknown_blocks(
        self,
        ui_client: TestClient,
    ) -> None:
        """The endpoint refuses names the project does not define."""
        response = ui_client.post(RUNS, json={"blocks": ["definitely-not-a-plugin"]})
        assert response.status_code == 400
        assert response.json()["code"] == "meltano_error"

    def test_starting_a_run_requires_blocks(self, ui_client: TestClient) -> None:
        """An empty block list is a validation error."""
        assert ui_client.post(RUNS, json={"blocks": []}).status_code == 422

    def test_start_spawns_via_the_manager(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """A valid request reaches `RunManager.start` and returns 202.

        The manager is stubbed so the test asserts on wiring rather than
        spawning a real pipeline.
        """
        record = make_record()
        with (
            mock.patch(
                "meltano.ui.routers.runs.validate_blocks",
                return_value=None,
            ),
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(return_value=record),
            ) as start,
        ):
            response = ui_client.post(RUNS, json={"blocks": ["tap-mock"]})

        assert response.status_code == 202
        assert response.json()["run_id"] == "r1"
        assert start.await_count == 1
        assert start.await_args.kwargs["kind"] is RunKind.run

    def test_started_run_carries_the_generated_run_id(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """The run id is generated before spawning and passed to the CLI."""
        with (
            mock.patch(
                "meltano.ui.routers.runs.validate_blocks",
                return_value=None,
            ),
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(
                    side_effect=lambda argv, **kw: make_record(  # noqa: ARG005
                        kw["run_id"],
                    )
                ),
            ) as start,
        ):
            ui_client.post(RUNS, json={"blocks": ["tap-mock"]})

        argv = start.await_args.args[0]
        run_id = start.await_args.kwargs["run_id"]
        assert f"--run-id={run_id}" in argv
