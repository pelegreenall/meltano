"""Endpoints for looking at an extractor's data and shaping it.

Two things, deliberately in one place because they are one loop: read some
records, apply steps, look again.

Previewing runs the extractor alone and stops it once it has enough records.
It writes nothing - no state, no destination, no project file - so it is safe
to run repeatedly while someone is still deciding what they want.
"""

from __future__ import annotations

import typing as t

from fastapi import APIRouter, Depends, HTTPException, status

from meltano.core.plugin import PluginType
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.routers.config import resolve_plugin
from meltano.ui.schemas.transforms import (
    CompileRequest,
    CompileResponse,
    PreviewRequest,
    PreviewResponse,
)
from meltano.ui.services import preview as preview_service
from meltano.ui.services.transforms import TransformError, apply_steps, compile_steps

if t.TYPE_CHECKING:
    from meltano.ui.context import AppContext

router = APIRouter(tags=["transforms"], dependencies=[Depends(require_auth)])

#: How long to let an extractor run before giving up on a preview. Generous
#: enough for a slow API, short enough that a misconfigured tap does not hold
#: a request open indefinitely.
_PREVIEW_TIMEOUT_SECONDS = 120.0


def _resolve_extractor(ctx: AppContext, plugin_type: str, name: str) -> str:
    """Resolve an extractor, refusing anything else.

    Args:
        ctx: The application context.
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.

    Returns:
        The extractor's name.

    Raises:
        HTTPException: 422 when the plugin is not an extractor.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    if plugin.type is not PluginType.EXTRACTORS:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=f"{plugin.name!r} is a {plugin.type}; only extractors emit records",
        )
    return plugin.name


def _ordered_columns(rows: list[dict[str, t.Any]]) -> list[str]:
    """List the columns present across rows, first-seen order preserved.

    Sorting would scramble the shape the tap chose; a table is easier to read
    when its columns stay where the data put them.

    Args:
        rows: The rows to inspect.

    Returns:
        The column names.
    """
    columns: dict[str, None] = {}
    for row in rows:
        for key in row:
            columns.setdefault(key, None)
    return list(columns)


@router.post(
    "/plugins/{plugin_type}/{name}/preview",
    response_model=PreviewResponse,
)
async def preview(
    plugin_type: str,
    name: str,
    payload: PreviewRequest,
    ctx: CtxDep,
) -> PreviewResponse:
    """Read a few records from an extractor, optionally shaped by steps.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        payload: Which stream, how many rows, and the steps to apply.
        ctx: The application context.

    Returns:
        The rows, the streams the tap announced, and the stream map the steps
        compile to.

    Raises:
        HTTPException: 422 for malformed steps, 400 when the extractor fails,
            504 when it does not answer in time.
    """
    extractor = _resolve_extractor(ctx, plugin_type, name)
    steps = [step.model_dump() for step in payload.steps]

    # Compiled first: a malformed step should not cost the user a tap run.
    try:
        stream_map = compile_steps(steps) if steps else {}
    except TransformError as err:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(err)) from err

    result = await preview_service.preview_records(
        ctx.project,
        extractor,
        stream=payload.stream,
        limit=payload.limit,
        timeout=_PREVIEW_TIMEOUT_SECONDS,
        environment=ctx.environment_name,
    )

    if result.error:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"{extractor!r} could not produce records: {result.error}",
        )

    if result.timed_out and not result.records:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"{extractor!r} produced no records within "
                f"{_PREVIEW_TIMEOUT_SECONDS:.0f}s"
            ),
        )

    try:
        rows = apply_steps(result.records, steps) if steps else list(result.records)
    except TransformError as err:
        # Reached only once real records are in hand: the steps compiled, and
        # it is this data that they cannot be applied to. Worth the same 422
        # as a malformed step, because the fix is the same kind of edit.
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(err)) from err

    return PreviewResponse(
        extractor=extractor,
        stream=payload.stream,
        columns=_ordered_columns(rows),
        schemas=result.schemas,
        rows=rows,
        row_count=len(rows),
        read_count=len(result.records),
        truncated=result.truncated,
        timed_out=result.timed_out,
        stream_map=stream_map,
    )


@router.post("/transforms/compile", response_model=CompileResponse)
def compile_transform(payload: CompileRequest) -> CompileResponse:
    """Compile steps into a Meltano stream map without running anything.

    Lets the UI show what will be written to `meltano.yml` while the steps are
    still being edited, and gives anyone wiring this up by hand a way to check
    their step list.

    Args:
        payload: The steps to compile.

    Returns:
        The stream map.

    Raises:
        HTTPException: 422 when a step is malformed.
    """
    try:
        return CompileResponse(
            stream_map=compile_steps([step.model_dump() for step in payload.steps]),
        )
    except TransformError as err:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(err)) from err
