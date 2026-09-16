"""Run supervision endpoints, including the live log stream."""

from __future__ import annotations

import typing as t

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from meltano.core.error import MeltanoError
from meltano.core.task_sets_service import TaskSetsService
from meltano.core.utils import new_run_id
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.schemas.runs import RunInfo, RunLog, RunRequest
from meltano.ui.services import log_stream
from meltano.ui.services.run_manager import RunKind

if t.TYPE_CHECKING:
    from collections.abc import Sequence

    from meltano.core.project import Project

router = APIRouter(tags=["runs"], dependencies=[Depends(require_auth)])


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


@router.get("/runs", response_model=list[RunInfo])
def list_runs(ctx: CtxDep) -> list[RunInfo]:
    """List every run this server knows about, newest first.

    Args:
        ctx: The application context.

    Returns:
        The known run records.
    """
    return [RunInfo(**record.to_dict()) for record in ctx.run_manager.list_runs()]


@router.get("/runs/{run_id}", response_model=RunInfo)
def get_run(run_id: str, ctx: CtxDep) -> RunInfo:
    """Return a single run.

    Args:
        run_id: The run to fetch.
        ctx: The application context.

    Returns:
        The run record.

    Raises:
        HTTPException: 404 when the run is unknown.
    """
    record = ctx.run_manager.get(run_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown run")
    return RunInfo(**record.to_dict())


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
    return RunInfo(**record.to_dict())


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
    return RunInfo(**record.to_dict())


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
