"""Endpoints for the project's schedules.

Nothing here starts a scheduler. Meltano's schedules are declarations that an
external orchestrator - Airflow, Dagster, or cron - reads and acts on; this
server only reads and writes those declarations, plus offers an explicit
"run now" that goes through the same supervision as any other run.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response, status

from meltano.core.schedule import (
    CRON_INTERVALS,
    ELTSchedule,
    JobSchedule,
    is_valid_cron,
)
from meltano.core.schedule_service import (
    BadCronError,
    ScheduleAlreadyExistsError,
    ScheduleService,
)
from meltano.core.task_sets_service import TaskSetsService
from meltano.core.utils import NotFound, new_run_id
from meltano.ui.deps import CtxDep, SessionDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.routers.runs import UnknownBlockError
from meltano.ui.schemas.runs import RunInfo
from meltano.ui.schemas.schedules import (
    ScheduleCreateRequest,
    ScheduleInfo,
    ScheduleUpdateRequest,
)
from meltano.ui.services.run_manager import RunKind

if t.TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from meltano.core.schedule import Schedule

router = APIRouter(tags=["schedules"], dependencies=[Depends(require_auth)])

#: Why a legacy `elt` schedule cannot be started from here. `meltano elt` is
#: superseded by `meltano run`, and building UI for the deprecated path would
#: mean carrying it for as long as the UI exists.
_ELT_NOT_RUNNABLE = (
    "This is a legacy 'elt' schedule. Run it with `meltano schedule run "
    "{name}`, or migrate it to a job schedule."
)


def _describe(schedule: Schedule, session: Session) -> ScheduleInfo:
    """Render a schedule for the API.

    Args:
        schedule: The schedule to describe.
        session: A system-database session, for the last-run lookup.

    Returns:
        The schedule's declared state.
    """
    if isinstance(schedule, ELTSchedule):
        # An `elt` schedule's state ID is its own name, which is what makes
        # this lookup possible here and not for job schedules.
        last = schedule.last_successful_run(session)
        return ScheduleInfo(
            name=schedule.name,
            kind="elt",
            interval=schedule.interval,
            cron_interval=schedule.cron_interval,
            env=dict(schedule.env),
            extractor=schedule.extractor,
            loader=schedule.loader,
            transform=schedule.transform,
            last_successful_run_at=(
                last.ended_at.isoformat() if last and last.ended_at else None
            ),
            can_run=False,
        )

    return ScheduleInfo(
        name=schedule.name,
        kind="job",
        interval=schedule.interval,
        cron_interval=schedule.cron_interval,
        env=dict(schedule.env),
        job=getattr(schedule, "job", None),
        can_run=True,
    )


def _find(ctx: CtxDep, name: str) -> Schedule:
    """Look up a schedule by name.

    Args:
        ctx: The application context.
        name: The schedule's name.

    Returns:
        The schedule.

    Raises:
        HTTPException: 404 when no such schedule is declared.
    """
    try:
        return ScheduleService(ctx.project).find_schedule(name)
    except NotFound as err:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(err)) from err


def _validate_interval(interval: str) -> None:
    """Reject an interval core would not accept.

    Checked here as well as in `add_schedule` so that an update, which core
    does not validate, cannot write an unschedulable expression to
    `meltano.yml`.

    Args:
        interval: The requested interval.

    Raises:
        BadCronError: If the expression is neither an alias nor valid cron.
    """
    if interval not in CRON_INTERVALS and not is_valid_cron(interval):
        raise BadCronError(interval)


def _validate_job(ctx: CtxDep, job: str) -> None:
    """Reject a schedule that points at a job the project does not declare.

    The CLI does not check this, so `meltano schedule add` can produce a
    schedule that fails only when an orchestrator eventually fires it. Failing
    at declaration time is the whole point of doing it in a form.

    Args:
        ctx: The application context.
        job: The job name to check.

    Raises:
        UnknownBlockError: If no such job is declared.
    """
    if not TaskSetsService(ctx.project).exists(job):
        raise UnknownBlockError(job)


@router.get("/schedules", response_model=list[ScheduleInfo])
def list_schedules(ctx: CtxDep, session: SessionDep) -> list[ScheduleInfo]:
    """List every schedule declared in the project.

    Args:
        ctx: The application context.
        session: A system-database session.

    Returns:
        The project's schedules, ordered by name.
    """
    schedules = [
        _describe(schedule, session)
        for schedule in ScheduleService(ctx.project).schedules()
    ]
    return sorted(schedules, key=lambda item: item.name)


@router.get("/schedules/{name}", response_model=ScheduleInfo)
def get_schedule(name: str, ctx: CtxDep, session: SessionDep) -> ScheduleInfo:
    """Return a single schedule.

    Args:
        name: The schedule's name.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The schedule.
    """
    return _describe(_find(ctx, name), session)


@router.post(
    "/schedules",
    response_model=ScheduleInfo,
    status_code=status.HTTP_201_CREATED,
)
async def create_schedule(
    payload: ScheduleCreateRequest,
    ctx: CtxDep,
    session: SessionDep,
) -> ScheduleInfo:
    """Declare a new job schedule.

    Args:
        payload: The schedule's name, job and interval.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The created schedule.

    Raises:
        HTTPException: 409 when a schedule of that name already exists.
    """
    _validate_interval(payload.interval)
    _validate_job(ctx, payload.job)

    service = ScheduleService(ctx.project)

    def write() -> JobSchedule:
        return service.add(payload.name, payload.job, payload.interval, **payload.env)

    # Serialized against every other project mutation, like any other write.
    async with ctx.write_lock:
        try:
            schedule = await anyio.to_thread.run_sync(write)
        except ScheduleAlreadyExistsError as err:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Schedule {payload.name!r} already exists",
            ) from err

    return _describe(schedule, session)


@router.put("/schedules/{name}", response_model=ScheduleInfo)
async def update_schedule(
    name: str,
    payload: ScheduleUpdateRequest,
    ctx: CtxDep,
    session: SessionDep,
) -> ScheduleInfo:
    """Change an existing schedule's interval, or the job it runs.

    Args:
        name: The schedule to update.
        payload: The fields to change; omitted ones are left alone.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The updated schedule.

    Raises:
        HTTPException: 422 when changing the job of a legacy `elt` schedule,
            which has no job to change.
    """
    schedule = _find(ctx, name)

    if payload.interval is not None:
        _validate_interval(payload.interval)

    if payload.job is not None:
        if not isinstance(schedule, JobSchedule):
            raise HTTPException(
                HTTP_422_UNPROCESSABLE,
                detail=(
                    f"Schedule {name!r} is a legacy 'elt' schedule and runs an "
                    "extractor and loader, not a job"
                ),
            )
        _validate_job(ctx, payload.job)

    service = ScheduleService(ctx.project)

    def write() -> None:
        # Mutated in place: `update_schedule` locates the existing entry by
        # name, `Schedule` being compared by name alone.
        if payload.interval is not None:
            schedule.interval = payload.interval
        if payload.job is not None:
            schedule.job = payload.job
        service.update_schedule(schedule)

    async with ctx.write_lock:
        await anyio.to_thread.run_sync(write)

    return _describe(schedule, session)


@router.delete("/schedules/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_schedule(name: str, ctx: CtxDep) -> Response:
    """Remove a schedule from the project.

    Args:
        name: The schedule to remove.
        ctx: The application context.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 when no such schedule is declared.
    """
    service = ScheduleService(ctx.project)

    async with ctx.write_lock:
        try:
            await anyio.to_thread.run_sync(service.remove_schedule, name)
        except NotFound as err:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(err)) from err

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/schedules/{name}/run",
    response_model=RunInfo,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_schedule(name: str, ctx: CtxDep) -> RunInfo:
    """Start a schedule's job now, without waiting for an orchestrator.

    Args:
        name: The schedule to run.
        ctx: The application context.

    Returns:
        The record for the started run, followable at `/runs/{run_id}/events`.

    Raises:
        HTTPException: 422 for a legacy `elt` schedule, which this server does
            not know how to spawn.
    """
    schedule = _find(ctx, name)
    if not isinstance(schedule, JobSchedule):
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=_ELT_NOT_RUNNABLE.format(name=name),
        )

    # The job is validated now rather than trusted from `meltano.yml`: a job
    # can be deleted after the schedule that names it was declared.
    _validate_job(ctx, schedule.job)

    run_id = str(new_run_id())
    argv = ctx.run_manager.build_run_argv(
        [schedule.job],
        run_id=run_id,
        environment=ctx.environment_name,
    )
    record = await ctx.run_manager.start(
        argv,
        kind=RunKind.run,
        run_id=run_id,
        environment=ctx.environment_name,
        # What the orchestrator would have applied when firing this schedule.
        env=schedule.env,
    )
    return RunInfo(**record.to_dict(), has_log=True)
