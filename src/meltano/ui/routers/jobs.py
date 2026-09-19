"""Endpoints for the project's named jobs (task sets).

Jobs are the unit that schedules point at and that `POST /runs` accepts by
name, so this is the declarative half of the run surface: the runs endpoints
execute, these endpoints define what there is to execute.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response, status
from jsonschema import validate as validate_json_schema
from jsonschema.exceptions import ValidationError

from meltano.core.task_sets import TASKS_JSON_SCHEMA, TaskSets
from meltano.core.task_sets_service import (
    JobAlreadyExistsError,
    JobNotFoundError,
    TaskSetsService,
)
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.routers.runs import validate_blocks
from meltano.ui.schemas.jobs import JobCreateRequest, JobInfo, JobUpdateRequest

if t.TYPE_CHECKING:
    from meltano.ui.schemas.jobs import Task

router = APIRouter(tags=["jobs"], dependencies=[Depends(require_auth)])


def _to_info(task_sets: TaskSets) -> JobInfo:
    """Describe a task set for the API.

    Args:
        task_sets: The job to describe.

    Returns:
        The job's declared tasks plus the blocks they resolve to.
    """
    # `TaskSets.tasks` is annotated as a homogeneous `list[str] |
    # list[list[str]]`, but `TASKS_JSON_SCHEMA` - which is what core actually
    # validates against - permits the two shapes to be mixed. The casts on
    # this boundary reconcile the annotation with the schema.
    return JobInfo(
        name=task_sets.name,
        tasks=t.cast("list[Task]", task_sets.tasks),
        blocks=task_sets.flat_args,
    )


def _build(name: str, tasks: list[Task], ctx: CtxDep) -> TaskSets:
    """Validate a job's tasks and return the task set they describe.

    Pydantic has already checked the outer shape, but `TASKS_JSON_SCHEMA` is
    what core validates against and is the authority on nesting depth, so it
    runs here too rather than being approximated in the schema module.

    Args:
        name: The job's name.
        tasks: The requested tasks.
        ctx: The application context.

    Returns:
        The validated task set.

    Raises:
        HTTPException: 422 when the tasks are malformed.
    """
    try:
        validate_json_schema(instance=tasks, schema=TASKS_JSON_SCHEMA)
    except ValidationError as err:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=f"Invalid tasks: {err.message}",
        ) from err

    task_sets = TaskSets(name=name, tasks=t.cast("list[str]", tasks))

    # The same guarantee `POST /runs` relies on: a job may only ever name
    # things the project already declares, so defining one cannot smuggle in
    # an arbitrary command to be executed later.
    validate_blocks(ctx.project, task_sets.flat_args)

    return task_sets


@router.get("/jobs", response_model=list[JobInfo])
def list_jobs(ctx: CtxDep) -> list[JobInfo]:
    """List every job declared in the project.

    Args:
        ctx: The application context.

    Returns:
        The project's jobs, ordered by name.
    """
    jobs = [_to_info(job) for job in TaskSetsService(ctx.project).list_task_sets()]
    return sorted(jobs, key=lambda item: item.name)


@router.get("/jobs/{name}", response_model=JobInfo)
def get_job(name: str, ctx: CtxDep) -> JobInfo:
    """Return a single job.

    Args:
        name: The job's name.
        ctx: The application context.

    Returns:
        The job.

    Raises:
        HTTPException: 404 when no such job is declared.
    """
    try:
        return _to_info(TaskSetsService(ctx.project).get(name))
    except JobNotFoundError as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(err)) from err


@router.post("/jobs", response_model=JobInfo, status_code=status.HTTP_201_CREATED)
async def create_job(payload: JobCreateRequest, ctx: CtxDep) -> JobInfo:
    """Declare a new job.

    Args:
        payload: The job's name and tasks.
        ctx: The application context.

    Returns:
        The created job.

    Raises:
        HTTPException: 409 when a job of that name already exists.
    """
    task_sets = _build(payload.name, payload.tasks, ctx)
    service = TaskSetsService(ctx.project)

    # Serialized against every other project mutation: `meltano_update()`
    # rewrites `meltano.yml` and drops caches shared by all requests.
    async with ctx.write_lock:
        try:
            await anyio.to_thread.run_sync(service.add, task_sets)
        except JobAlreadyExistsError as err:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Job {payload.name!r} already exists",
            ) from err

    return _to_info(task_sets)


@router.put("/jobs/{name}", response_model=JobInfo)
async def update_job(name: str, payload: JobUpdateRequest, ctx: CtxDep) -> JobInfo:
    """Replace an existing job's tasks.

    Args:
        name: The job to update.
        payload: The replacement tasks.
        ctx: The application context.

    Returns:
        The updated job.

    Raises:
        HTTPException: 404 when no such job is declared.
    """
    task_sets = _build(name, payload.tasks, ctx)
    service = TaskSetsService(ctx.project)

    async with ctx.write_lock:
        try:
            await anyio.to_thread.run_sync(service.update, task_sets)
        except JobNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(err)) from err

    return _to_info(task_sets)


@router.delete("/jobs/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(name: str, ctx: CtxDep) -> Response:
    """Remove a job from the project.

    Args:
        name: The job to remove.
        ctx: The application context.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 when no such job is declared.
    """
    service = TaskSetsService(ctx.project)

    async with ctx.write_lock:
        try:
            await anyio.to_thread.run_sync(service.remove, name)
        except JobNotFoundError as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(err)) from err

    return Response(status_code=status.HTTP_204_NO_CONTENT)
