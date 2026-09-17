"""Run supervision endpoints, including the live log stream."""

from __future__ import annotations

import typing as t
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from meltano.core.error import MeltanoError
from meltano.core.task_sets_service import TaskSetsService
from meltano.core.utils import new_run_id
from meltano.ui.deps import CtxDep, SessionDep, require_auth
from meltano.ui.schemas.runs import RunInfo, RunJobInfo, RunLog, RunRequest
from meltano.ui.services import log_stream, run_history
from meltano.ui.services.run_manager import RunKind, RunStatus

if t.TYPE_CHECKING:
    from collections.abc import Sequence

    from meltano.core.job import Job
    from meltano.core.project import Project
    from meltano.ui.services.run_manager import RunRecord

router = APIRouter(tags=["runs"], dependencies=[Depends(require_auth)])

#: How many runs `GET /runs` returns when the caller does not say. Large
#: enough to fill the UI's list without paging, small enough that a long-lived
#: project does not serialize its entire history on every poll.
DEFAULT_RUN_LIMIT = 50
MAX_RUN_LIMIT = 500

#: Sorts ahead of any real timestamp, for the malformed-value case below.
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class UnknownBlockError(MeltanoError):
    """Raised when a requested block does not exist in the project."""

    def __init__(self, name: str) -> None:
        """Initialize the error.

        Args:
            name: The unrecognized block name.
        """
        super().__init__(
            reason=f"{name!r} is not a plugin or job defined in this project",
            instruction=(
                "Use `meltano add` to install a plugin, or `meltano job add` "
                "to define a job"
            ),
        )


def validate_blocks(project: Project, blocks: Sequence[str]) -> None:
    """Reject block names that the project does not define.

    This is what keeps `POST /runs` from being an arbitrary-command endpoint:
    the caller supplies names, never argv, and every name must resolve to
    something already declared in `meltano.yml`.

    Args:
        project: The project to validate against.
        blocks: The requested block names.

    Raises:
        UnknownBlockError: If any name is unrecognized.
    """
    known = {plugin.name for plugin in project.plugins.plugins()}
    known.update(
        task_set.name for task_set in TaskSetsService(project).list_task_sets()
    )

    for block in blocks:
        # `plugin:command` selects a declared command on a known plugin.
        name = block.split(":", 1)[0]
        if name not in known:
            raise UnknownBlockError(block)


def _isoformat(value: datetime | None) -> str | None:
    """Render a timestamp the way supervised records render theirs.

    Args:
        value: The timestamp, or None.

    Returns:
        An ISO-8601 string, or None.
    """
    return value.isoformat() if value is not None else None


def _sort_key(info: RunInfo) -> datetime:
    """Return the instant a run started, for ordering.

    Parsed rather than compared as text: the two sources format timestamps
    identically today, but ordering the run list is not the right place to
    depend on that.

    Args:
        info: The run to place.

    Returns:
        The parsed start time, or the epoch if it cannot be parsed.
    """
    try:
        return datetime.fromisoformat(info.started_at)
    except ValueError:
        return _EPOCH


def _describe(
    run_id: str,
    record: RunRecord | None,
    jobs: Sequence[Job],
) -> RunInfo:
    """Combine what this server supervised with what the database recorded.

    Args:
        run_id: The run being described.
        record: The supervised record, or None for a run this server did not
            launch.
        jobs: The system-database rows sharing this run ID.

    Returns:
        The merged view.
    """
    job_infos = [
        RunJobInfo(
            job_name=job.job_name,
            state=str(job.state),
            started_at=_isoformat(job.started_at),
            ended_at=_isoformat(job.ended_at),
            trigger=job.trigger,
        )
        for job in jobs
    ]
    recorded = run_history.status_from_jobs(jobs)

    if record is None:
        # History-only: known from the `runs` table alone, so there is no argv
        # to show and no captured output to stream.
        started = jobs[0].started_at if jobs else None
        ended = [job.ended_at for job in jobs]
        return RunInfo(
            run_id=run_id,
            kind=RunKind.run.value,
            status=(recorded or RunStatus.unknown).value,
            started_at=_isoformat(started) or _EPOCH.isoformat(),
            finished_at=(
                _isoformat(max(ended))  # type: ignore[type-var]
                if ended and all(value is not None for value in ended)
                else None
            ),
            jobs=job_infos,
            has_log=False,
        )

    status_ = record.status
    if status_ is RunStatus.unknown and recorded is not None:
        # `RunStatus.unknown` means the server restarted mid-run and lost
        # track of the process. The row is authoritative there, as the enum's
        # own definition says.
        status_ = recorded

    return RunInfo(
        **{**record.to_dict(), "status": status_.value},
        jobs=job_infos,
        has_log=True,
    )


@router.get("/runs", response_model=list[RunInfo])
def list_runs(
    ctx: CtxDep,
    session: SessionDep,
    limit: int = Query(DEFAULT_RUN_LIMIT, ge=1, le=MAX_RUN_LIMIT),
) -> list[RunInfo]:
    """List runs, newest first.

    Merges the subprocesses this server supervised with the system database's
    record of every execution, so runs started from a terminal or by an
    earlier server process are not missing from the list.

    Args:
        ctx: The application context.
        session: A system-database session.
        limit: The maximum number of runs to return.

    Returns:
        The merged run list.
    """
    supervised = {record.run_id: record for record in ctx.run_manager.list_runs()}
    history_ids = run_history.recent_run_ids(session, limit=limit)

    # Supervised runs are included even when they fall outside the history
    # window, and install/test tasks are included even though they never
    # produce a row at all.
    jobs = run_history.jobs_by_run_id(session, set(history_ids) | set(supervised))

    run_ids = [*supervised, *(rid for rid in history_ids if rid not in supervised)]
    runs = [
        _describe(run_id, supervised.get(run_id), jobs.get(run_id, ()))
        for run_id in run_ids
    ]
    runs.sort(key=_sort_key, reverse=True)
    return runs[:limit]


@router.get("/runs/{run_id}", response_model=RunInfo)
def get_run(run_id: str, ctx: CtxDep, session: SessionDep) -> RunInfo:
    """Return a single run.

    Args:
        run_id: The run to fetch.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The run record.

    Raises:
        HTTPException: 404 when neither this server nor the database knows the
            run.
    """
    record = ctx.run_manager.get(run_id)
    jobs = run_history.jobs_by_run_id(session, (run_id,)).get(run_id, ())
    if record is None and not jobs:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown run")
    return _describe(run_id, record, jobs)


@router.post("/runs", response_model=RunInfo, status_code=status.HTTP_202_ACCEPTED)
async def start_run(payload: RunRequest, ctx: CtxDep) -> RunInfo:
    """Start a pipeline run as a supervised subprocess.

    Args:
        payload: The blocks to run and the flags to apply.
        ctx: The application context.

    Returns:
        The record for the started run, available before it produces output.
    """
    validate_blocks(ctx.project, payload.blocks)

    # Generated here so the caller holds a handle before the process exists,
    # and so the resulting `Job` row can be correlated back to this run.
    run_id = str(new_run_id())
    argv = ctx.run_manager.build_run_argv(
        payload.blocks,
        run_id=run_id,
        environment=ctx.environment_name,
        full_refresh=payload.full_refresh,
        no_state_update=payload.no_state_update,
        force=payload.force,
    )
    record = await ctx.run_manager.start(
        argv,
        kind=RunKind.run,
        run_id=run_id,
        environment=ctx.environment_name,
    )
    # No rows exist yet - the subprocess has only just been spawned.
    return _describe(record.run_id, record, ())


@router.delete("/runs/{run_id}", response_model=RunInfo)
async def cancel_run(run_id: str, ctx: CtxDep) -> RunInfo:
    """Cancel a running subprocess and everything it spawned.

    Args:
        run_id: The run to cancel.
        ctx: The application context.

    Returns:
        The updated record.

    Raises:
        HTTPException: 404 when the run is unknown.
    """
    record = await ctx.run_manager.cancel(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown run")
    return _describe(record.run_id, record, ())


@router.get("/runs/{run_id}/log", response_model=RunLog)
def get_run_log(run_id: str, ctx: CtxDep) -> RunLog:
    """Return a run's captured output.

    Args:
        run_id: The run to read.
        ctx: The application context.

    Returns:
        The run's log lines.

    Raises:
        HTTPException: 404 when the run is unknown.
    """
    record = ctx.run_manager.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown run")

    # The path comes from the manager's own registry, never from user input.
    path = ctx.run_manager.log_path(run_id)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    return RunLog(run_id=run_id, lines=lines)


@router.get("/runs/{run_id}/events")
async def stream_run_events(
    run_id: str,
    request: Request,
    ctx: CtxDep,
) -> StreamingResponse:
    """Stream a run's output as server-sent events.

    Args:
        run_id: The run to follow.
        request: The incoming request, read for `Last-Event-ID`.
        ctx: The application context.

    Returns:
        A streaming response of SSE frames.

    Raises:
        HTTPException: 404 when the run is unknown.
    """
    if ctx.run_manager.get(run_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown run")

    last_event_id = log_stream.parse_last_event_id(
        request.headers.get("last-event-id"),
    )

    return StreamingResponse(
        log_stream.stream_run(
            ctx.run_manager,
            run_id,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Defensive: this server is same-origin only, but a proxy in front
            # of it must not buffer the stream.
            "X-Accel-Buffering": "no",
        },
    )
