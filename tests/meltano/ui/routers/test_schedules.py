"""Tests for the schedule endpoints."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.core.schedule_service import ScheduleService
from meltano.core.task_sets import TaskSets
from meltano.core.task_sets_service import TaskSetsService
from tests.meltano.ui.routers.test_runs import make_record

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project
    from meltano.ui.context import AppContext

SCHEDULES = "/api/v1/schedules"

#: The job every schedule in these tests points at.
JOB = "scheduled-job"


@pytest.fixture
def scheduling(project: Project, tap: ProjectPlugin) -> t.Iterator[ScheduleService]:
    """Yield a service over a project with one job and no schedules.

    The `project` fixture is class-scoped, so anything left behind would be
    visible to the next test under `pytest-randomly`'s shuffling.

    Args:
        project: The test project.
        tap: A plugin for the job to reference.

    Yields:
        The service under test.
    """
    schedules = ScheduleService(project)
    jobs = TaskSetsService(project)

    original_schedules = schedules.schedules()
    original_jobs = jobs.list_task_sets()
    for schedule in original_schedules:
        schedules.remove_schedule(schedule.name)
    for task_set in original_jobs:
        jobs.remove(task_set.name)
    jobs.add(TaskSets(name=JOB, tasks=[tap.name]))

    try:
        yield schedules
    finally:
        for schedule in schedules.schedules():
            schedules.remove_schedule(schedule.name)
        for task_set in jobs.list_task_sets():
            jobs.remove(task_set.name)
        for schedule in original_schedules:
            schedules.add_schedule(schedule)
        for task_set in original_jobs:
            jobs.add(task_set)


@pytest.mark.usefixtures("tap")
class TestScheduleEndpoints:
    """CRUD over the project's declared schedules."""

    def test_list_is_empty_without_schedules(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """A project with no schedules reports none."""
        response = ui_client.get(SCHEDULES)
        assert response.status_code == 200
        assert response.json() == []

    def test_create_declares_the_schedule(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """A created schedule is written to the project, not merely echoed."""
        response = ui_client.post(
            SCHEDULES,
            json={"name": "nightly", "job": JOB, "interval": "@daily"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["kind"] == "job"
        assert body["job"] == JOB
        # The alias is resolved so the UI can show when it actually fires.
        assert body["cron_interval"] == "0 0 * * *"
        assert body["can_run"] is True

        assert scheduling.find_schedule("nightly").interval == "@daily"

    def test_create_accepts_a_cron_expression(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """A raw cron expression is stored as given."""
        response = ui_client.post(
            SCHEDULES,
            json={"name": "every-15", "job": JOB, "interval": "*/15 * * * *"},
        )
        assert response.status_code == 201
        assert response.json()["cron_interval"] == "*/15 * * * *"

    def test_a_manual_schedule_has_no_cron(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """`@manual` never fires on its own, so it resolves to no cron.

        The UI relies on this to avoid claiming a schedule will run when it
        will not.
        """
        response = ui_client.post(
            SCHEDULES,
            json={"name": "by-hand", "job": JOB, "interval": "@manual"},
        )
        assert response.status_code == 201
        assert response.json()["cron_interval"] is None

    def test_create_rejects_a_bad_interval(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """An unschedulable expression is refused."""
        response = ui_client.post(
            SCHEDULES,
            json={"name": "broken", "job": JOB, "interval": "not-a-cron"},
        )
        assert response.status_code == 400
        assert response.json()["code"] == "meltano_error"

    def test_create_rejects_an_unknown_job(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """A schedule may not point at a job the project does not declare.

        The CLI allows this, producing a schedule that fails only when an
        orchestrator eventually fires it.
        """
        response = ui_client.post(
            SCHEDULES,
            json={"name": "dangling", "job": "no-such-job", "interval": "@daily"},
        )
        assert response.status_code == 400
        assert "not a plugin or job" in response.json()["detail"]

    def test_create_rejects_a_duplicate_name(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """Re-declaring a schedule is a conflict, not a silent update."""
        scheduling.add("dupe", JOB, "@daily")

        response = ui_client.post(
            SCHEDULES,
            json={"name": "dupe", "job": JOB, "interval": "@hourly"},
        )
        assert response.status_code == 409

    def test_get_unknown_schedule_is_404(self, ui_client: TestClient) -> None:
        """An undeclared name is a 404."""
        assert ui_client.get(f"{SCHEDULES}/nope").status_code == 404

    def test_update_changes_the_interval(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """A put rewrites the interval in the project file."""
        scheduling.add("evolving", JOB, "@daily")

        response = ui_client.put(
            f"{SCHEDULES}/evolving",
            json={"interval": "@hourly"},
        )
        assert response.status_code == 200
        assert scheduling.find_schedule("evolving").interval == "@hourly"

    def test_update_rejects_a_bad_interval(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """Core does not validate updates, so this layer must.

        Without the check an update could write an expression no orchestrator
        can parse, which `add` would have refused.
        """
        scheduling.add("guarded", JOB, "@daily")

        response = ui_client.put(
            f"{SCHEDULES}/guarded",
            json={"interval": "61 * * * *"},
        )
        assert response.status_code == 400
        assert scheduling.find_schedule("guarded").interval == "@daily"

    def test_update_rejects_an_unknown_job(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """Repointing a schedule at a nonexistent job is refused."""
        scheduling.add("repoint", JOB, "@daily")

        response = ui_client.put(
            f"{SCHEDULES}/repoint",
            json={"job": "no-such-job"},
        )
        assert response.status_code == 400

    def test_update_unknown_schedule_is_404(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,  # noqa: ARG002
    ) -> None:
        """A put does not create a schedule that was never declared."""
        response = ui_client.put(f"{SCHEDULES}/ghost", json={"interval": "@daily"})
        assert response.status_code == 404

    def test_delete_removes_the_schedule(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """A delete removes the schedule from the project."""
        scheduling.add("doomed", JOB, "@daily")

        assert ui_client.delete(f"{SCHEDULES}/doomed").status_code == 204
        assert [s.name for s in scheduling.schedules()] == []

    def test_delete_unknown_schedule_is_404(self, ui_client: TestClient) -> None:
        """Deleting something undeclared is a 404."""
        assert ui_client.delete(f"{SCHEDULES}/nope").status_code == 404


@pytest.mark.usefixtures("tap")
class TestLegacyEltSchedules:
    """`elt` schedules predate `meltano run` and are read-only here."""

    def test_an_elt_schedule_is_listed(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
        tap: ProjectPlugin,
    ) -> None:
        """Legacy schedules stay visible rather than silently disappearing."""
        scheduling.add_elt("legacy", tap.name, "target-mock", "skip", "@daily")

        listed = ui_client.get(SCHEDULES).json()

        entry = next(item for item in listed if item["name"] == "legacy")
        assert entry["kind"] == "elt"
        assert entry["extractor"] == tap.name
        assert entry["job"] is None
        # This server has no way to spawn the legacy `elt` path.
        assert entry["can_run"] is False

    def test_an_elt_schedule_cannot_be_repointed_at_a_job(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
        tap: ProjectPlugin,
    ) -> None:
        """An `elt` schedule has no job, so setting one is a validation error."""
        scheduling.add_elt("legacy-put", tap.name, "target-mock", "skip", "@daily")

        response = ui_client.put(f"{SCHEDULES}/legacy-put", json={"job": JOB})
        assert response.status_code == 422

    def test_an_elt_schedule_cannot_be_run(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
        tap: ProjectPlugin,
    ) -> None:
        """Running one is refused with a pointer to the CLI."""
        scheduling.add_elt("legacy-run", tap.name, "target-mock", "skip", "@daily")

        response = ui_client.post(f"{SCHEDULES}/legacy-run/run")
        assert response.status_code == 422
        assert "meltano schedule run" in response.json()["detail"]

    def test_an_elt_schedule_can_still_be_deleted(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
        tap: ProjectPlugin,
    ) -> None:
        """Read-only means it cannot be created or run, not that it is stuck."""
        scheduling.add_elt("legacy-del", tap.name, "target-mock", "skip", "@daily")

        assert ui_client.delete(f"{SCHEDULES}/legacy-del").status_code == 204


@pytest.mark.usefixtures("tap")
class TestRunSchedule:
    """Starting a schedule on demand, without waiting for an orchestrator."""

    def test_run_spawns_the_schedules_job(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
        scheduling: ScheduleService,
    ) -> None:
        """The run is the schedule's job, supervised like any other."""
        scheduling.add("now", JOB, "@daily")

        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=make_record()),
        ) as start:
            response = ui_client.post(f"{SCHEDULES}/now/run")

        assert response.status_code == 202
        assert start.await_count == 1
        assert JOB in start.await_args.args[0]

    def test_run_applies_the_schedules_env(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
        scheduling: ScheduleService,
    ) -> None:
        """The env an orchestrator would apply is applied here too.

        Dropping it would make "run now" quietly behave differently from the
        same schedule firing for real.
        """
        scheduling.add("with-env", JOB, "@daily", TARGET_REGION="eu-west-1")

        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=make_record()),
        ) as start:
            ui_client.post(f"{SCHEDULES}/with-env/run")

        assert start.await_args.kwargs["env"] == {"TARGET_REGION": "eu-west-1"}

    def test_run_rejects_a_schedule_whose_job_is_gone(
        self,
        ui_client: TestClient,
        scheduling: ScheduleService,
    ) -> None:
        """A job can be deleted after the schedule naming it was declared."""
        scheduling.add("orphaned", JOB, "@daily")
        TaskSetsService(scheduling.project).remove(JOB)

        response = ui_client.post(f"{SCHEDULES}/orphaned/run")
        assert response.status_code == 400

    def test_run_unknown_schedule_is_404(self, ui_client: TestClient) -> None:
        """Running something undeclared is a 404."""
        assert ui_client.post(f"{SCHEDULES}/nope/run").status_code == 404


class TestScheduleAuth:
    """Admission control applies to these endpoints like any other."""

    def test_anonymous_list_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Without a token, the schedule list is not readable."""
        assert ui_client_anonymous.get(SCHEDULES).status_code == 401
