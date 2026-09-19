"""Tests for the job (task set) endpoints."""

from __future__ import annotations

import typing as t

import pytest

from meltano.core.task_sets import TaskSets
from meltano.core.task_sets_service import TaskSetsService
from meltano.ui.routers.runs import validate_blocks

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project

JOBS = "/api/v1/jobs"


@pytest.fixture
def clean_jobs(project: Project) -> t.Iterator[TaskSetsService]:
    """Yield a service over a project with no jobs, restoring them after.

    The `project` fixture is class-scoped, so a job left behind by one test
    would be visible to the next under `pytest-randomly`'s shuffling.

    Args:
        project: The test project.

    Yields:
        The service under test.
    """
    service = TaskSetsService(project)
    original = service.list_task_sets()
    for task_set in original:
        service.remove(task_set.name)
    try:
        yield service
    finally:
        for task_set in service.list_task_sets():
            service.remove(task_set.name)
        for task_set in original:
            service.add(task_set)


@pytest.mark.usefixtures("tap", "target")
class TestJobEndpoints:
    """CRUD over the project's declared jobs."""

    def test_list_is_empty_without_jobs(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,  # noqa: ARG002
    ) -> None:
        """A project with no jobs reports none."""
        response = ui_client.get(JOBS)
        assert response.status_code == 200
        assert response.json() == []

    def test_create_declares_the_job(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
        target: ProjectPlugin,
    ) -> None:
        """A created job is written to the project, not merely echoed back."""
        response = ui_client.post(
            JOBS,
            json={"name": "nightly", "tasks": [f"{tap.name} {target.name}"]},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "nightly"
        # The declared task is one string; `blocks` is what it resolves to.
        assert body["tasks"] == [f"{tap.name} {target.name}"]
        assert body["blocks"] == [tap.name, target.name]

        assert clean_jobs.get("nightly").tasks == [f"{tap.name} {target.name}"]

    def test_create_accepts_nested_tasks(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,  # noqa: ARG002
        tap: ProjectPlugin,
        target: ProjectPlugin,
    ) -> None:
        """The list-of-lists task shape core allows is accepted too."""
        response = ui_client.post(
            JOBS,
            json={"name": "nested", "tasks": [[tap.name, target.name]]},
        )
        assert response.status_code == 201
        assert response.json()["blocks"] == [tap.name, target.name]

    def test_create_rejects_an_unknown_block(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,  # noqa: ARG002
    ) -> None:
        """A job may not name something the project does not declare.

        This is the same guarantee `POST /runs` provides: without it, a job
        would be a way to store an arbitrary command for later execution.
        """
        response = ui_client.post(
            JOBS,
            json={"name": "sneaky", "tasks": ["rm -rf /"]},
        )
        assert response.status_code == 400
        assert "not a plugin or job" in response.json()["detail"]

    def test_create_rejects_malformed_tasks(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,  # noqa: ARG002
    ) -> None:
        """Nesting deeper than the task schema permits is refused."""
        response = ui_client.post(
            JOBS,
            json={"name": "deep", "tasks": [[["too", "deep"]]]},
        )
        assert response.status_code == 422

    def test_create_rejects_a_duplicate_name(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
    ) -> None:
        """Re-declaring an existing job is a conflict, not a silent update."""
        clean_jobs.add(TaskSets(name="dupe", tasks=[tap.name]))

        response = ui_client.post(JOBS, json={"name": "dupe", "tasks": [tap.name]})
        assert response.status_code == 409

    def test_get_returns_one_job(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
    ) -> None:
        """A declared job is retrievable by name."""
        clean_jobs.add(TaskSets(name="single", tasks=[tap.name]))

        response = ui_client.get(f"{JOBS}/single")
        assert response.status_code == 200
        assert response.json()["blocks"] == [tap.name]

    def test_get_unknown_job_is_404(self, ui_client: TestClient) -> None:
        """An undeclared name is a 404."""
        assert ui_client.get(f"{JOBS}/nope").status_code == 404

    def test_update_replaces_the_tasks(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
        target: ProjectPlugin,
    ) -> None:
        """A put swaps the job's tasks in the project file."""
        clean_jobs.add(TaskSets(name="evolving", tasks=[tap.name]))

        response = ui_client.put(
            f"{JOBS}/evolving",
            json={"tasks": [f"{tap.name} {target.name}"]},
        )
        assert response.status_code == 200
        assert clean_jobs.get("evolving").flat_args == [tap.name, target.name]

    def test_update_unknown_job_is_404(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """A put does not create a job that was never declared."""
        response = ui_client.put(f"{JOBS}/ghost", json={"tasks": [tap.name]})
        assert response.status_code == 404

    def test_delete_removes_the_job(
        self,
        ui_client: TestClient,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
    ) -> None:
        """A delete removes the job from the project."""
        clean_jobs.add(TaskSets(name="doomed", tasks=[tap.name]))

        assert ui_client.delete(f"{JOBS}/doomed").status_code == 204
        assert not clean_jobs.exists("doomed")

    def test_delete_unknown_job_is_404(self, ui_client: TestClient) -> None:
        """Deleting something undeclared is a 404."""
        assert ui_client.delete(f"{JOBS}/nope").status_code == 404

    def test_a_job_is_runnable_by_name(
        self,
        clean_jobs: TaskSetsService,
        tap: ProjectPlugin,
    ) -> None:
        """A declared job satisfies the run endpoint's block validation.

        This is the seam between the two routers: defining a job is only
        useful if `POST /runs` will then accept its name.
        """
        clean_jobs.add(TaskSets(name="runnable", tasks=[tap.name]))

        validate_blocks(clean_jobs.project, ["runnable"])


class TestJobAuth:
    """Admission control applies to these endpoints like any other."""

    def test_anonymous_list_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Without a token, the job list is not readable."""
        assert ui_client_anonymous.get(JOBS).status_code == 401
