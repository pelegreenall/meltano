"""Tests for the run supervision endpoints."""

from __future__ import annotations

import contextlib
import typing as t
import uuid
from unittest import mock

import pytest

from meltano.core.job import State
from meltano.core.plugin import PluginType
from meltano.core.plugin.project_plugin import ProjectPlugin
from meltano.core.project_plugins_service import PluginAlreadyAddedException
from meltano.ui.routers.runs import UnknownBlockError, validate_blocks
from meltano.ui.services.run_manager import RunKind, RunRecord, RunStatus
from tests.meltano.ui.services.test_run_history import make_job

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import Session

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

    def test_accepts_a_mapping_name(self, project: Project) -> None:
        """`meltano run tap my-mapping target` has to get past this guard.

        A mapping is runnable under its own name, but Meltano expands it into
        a synthetic plugin named after the mapper that carries it - so a check
        that looked only at plugin names refused every mapping ever saved,
        which is the only thing saving one is for.
        """
        mapper = ProjectPlugin(
            PluginType.MAPPERS,
            "test-mapper",
            namespace="test_mapper",
            executable="test-mapper",
            mappings=[{"name": "tidy", "config": {"stream_maps": {}}}],
        )
        with contextlib.suppress(PluginAlreadyAddedException):
            project.plugins.add_to_file(mapper)

        try:
            validate_blocks(project, ["tidy"])
        finally:
            project.plugins.remove_from_file(mapper)


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


class TestRunHistory:
    """Merging supervised subprocesses with the system database's record."""

    def test_a_run_from_elsewhere_is_listed(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """A run this server never launched still appears in the list.

        This is the point of the merge: pipelines started from a terminal, or
        by an earlier server process, were previously invisible.
        """
        run_id = uuid.uuid4()
        make_job(session, job_name="tap-mock-to-target-mock", run_id=run_id)

        listed = ui_client_with_history.get(RUNS).json()

        entry = next(run for run in listed if run["run_id"] == str(run_id))
        assert entry["status"] == "success"
        assert entry["jobs"][0]["job_name"] == "tap-mock-to-target-mock"
        # Nothing was supervised, so there is no argv and no captured output.
        assert entry["argv"] == []
        assert entry["has_log"] is False

    def test_a_history_only_run_is_fetchable(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """`GET /runs/{id}` resolves runs known only from the database."""
        run_id = uuid.uuid4()
        make_job(session, job_name="nightly", run_id=run_id)

        response = ui_client_with_history.get(f"{RUNS}/{run_id}")

        assert response.status_code == 200
        assert response.json()["has_log"] is False

    def test_an_unknown_run_is_still_404(
        self,
        ui_client_with_history: TestClient,
    ) -> None:
        """Merging does not turn unknown runs into empty successes."""
        response = ui_client_with_history.get(f"{RUNS}/{uuid.uuid4()}")
        assert response.status_code == 404

    def test_every_block_of_a_run_is_reported(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """A multi-block run reports each row, not just the first.

        `BlockParser` stamps the run ID onto every ExtractLoadBlock, so the
        relationship is one-to-many.
        """
        run_id = uuid.uuid4()
        make_job(session, job_name="first", run_id=run_id, minutes=0)
        make_job(session, job_name="second", run_id=run_id, minutes=1)

        entry = ui_client_with_history.get(f"{RUNS}/{run_id}").json()

        assert [job["job_name"] for job in entry["jobs"]] == ["first", "second"]

    def test_a_partial_failure_is_not_a_success(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """One failed block makes the whole run failed."""
        run_id = uuid.uuid4()
        make_job(session, job_name="ok", run_id=run_id)
        make_job(session, job_name="bad", run_id=run_id, state=State.FAIL)

        entry = ui_client_with_history.get(f"{RUNS}/{run_id}").json()

        assert entry["status"] == "failed"

    def test_a_supervised_run_gains_its_rows(
        self,
        ui_client_with_history: TestClient,
        ui_context: AppContext,
        session: Session,
    ) -> None:
        """A run this server launched is annotated, not duplicated."""
        run_id = uuid.uuid4()
        make_job(session, job_name="tap-mock-to-target-mock", run_id=run_id)
        record = make_record(str(run_id))

        with mock.patch.object(
            ui_context.run_manager,
            "list_runs",
            return_value=[record],
        ):
            listed = ui_client_with_history.get(RUNS).json()

        matching = [run for run in listed if run["run_id"] == str(run_id)]
        assert len(matching) == 1
        assert matching[0]["argv"] == record.argv
        assert matching[0]["has_log"] is True
        assert matching[0]["jobs"][0]["job_name"] == "tap-mock-to-target-mock"

    def test_an_unknown_status_is_resolved_from_the_rows(
        self,
        ui_client_with_history: TestClient,
        ui_context: AppContext,
        session: Session,
    ) -> None:
        """A run lost to a server restart is settled by the database.

        `RunStatus.unknown` exists precisely for the case where the server
        restarted mid-run; its own definition names the `Job` row as
        authoritative, which this makes true.
        """
        run_id = uuid.uuid4()
        make_job(session, job_name="recovered", run_id=run_id, state=State.FAIL)
        record = make_record(str(run_id), status=RunStatus.unknown)

        with mock.patch.object(
            ui_context.run_manager,
            "list_runs",
            return_value=[record],
        ):
            listed = ui_client_with_history.get(RUNS).json()

        assert listed[0]["status"] == "failed"

    def test_a_supervised_status_is_not_overridden(
        self,
        ui_client_with_history: TestClient,
        ui_context: AppContext,
        session: Session,
    ) -> None:
        """A live process keeps its own status; only `unknown` defers."""
        run_id = uuid.uuid4()
        make_job(session, job_name="live", run_id=run_id, state=State.SUCCESS)
        record = make_record(str(run_id), status=RunStatus.running)

        with mock.patch.object(
            ui_context.run_manager,
            "list_runs",
            return_value=[record],
        ):
            listed = ui_client_with_history.get(RUNS).json()

        assert listed[0]["status"] == "running"

    def test_state_edits_are_not_runs(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """`meltano state set` writes a row that must not look like a run."""
        run_id = uuid.uuid4()
        make_job(session, job_name="edit", run_id=run_id, state=State.STATE_EDIT)

        listed = ui_client_with_history.get(RUNS).json()

        assert all(run["run_id"] != str(run_id) for run in listed)

    def test_runs_are_ordered_newest_first(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """Ordering holds across both sources, not just within each."""
        older, newer = uuid.uuid4(), uuid.uuid4()
        make_job(session, job_name="older", run_id=older, minutes=0)
        make_job(session, job_name="newer", run_id=newer, minutes=30)

        listed = ui_client_with_history.get(RUNS).json()
        positions = {run["run_id"]: index for index, run in enumerate(listed)}

        assert positions[str(newer)] < positions[str(older)]

    def test_limit_caps_the_list(
        self,
        ui_client_with_history: TestClient,
        session: Session,
    ) -> None:
        """The caller can bound how much history is returned."""
        for index in range(4):
            make_job(
                session,
                job_name=f"run{index}",
                run_id=uuid.uuid4(),
                minutes=index,
            )

        listed = ui_client_with_history.get(RUNS, params={"limit": 2}).json()

        assert len(listed) == 2

    def test_limit_is_bounded(self, ui_client_with_history: TestClient) -> None:
        """An absurd limit is a validation error, not a full table scan."""
        response = ui_client_with_history.get(RUNS, params={"limit": 10_000})
        assert response.status_code == 422
