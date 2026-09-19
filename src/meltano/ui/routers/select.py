"""Endpoints for choosing what an extractor extracts.

Split in two on purpose. `GET .../select` reads the patterns out of
`meltano.yml` and always answers immediately. `GET .../select/catalog` asks the
extractor what it can produce, which means *running* it in discovery mode: it
needs the plugin installed and configured, it can take a long time, and it can
fail for reasons that have nothing to do with this server.

Keeping them apart lets the UI show the patterns the moment the page opens and
treat the catalog as something that may arrive later or not at all, rather than
making every read of the configuration wait on a subprocess.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status

from meltano.core.plugin import PluginType
from meltano.core.plugin.error import PluginExecutionError
from meltano.core.plugin.singer.catalog import SelectionType, SelectPattern
from meltano.core.select_service import SelectService
from meltano.ui.deps import CtxDep, SessionDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.routers.config import resolve_plugin
from meltano.ui.schemas.select import (
    AddPatternRequest,
    SelectCatalog,
    SelectedProperty,
    SelectedStream,
    SelectPatternInfo,
    SelectPatterns,
)

if t.TYPE_CHECKING:
    from meltano.ui.context import AppContext

router = APIRouter(tags=["select"], dependencies=[Depends(require_auth)])

#: How long to wait for an extractor to describe itself before giving up.
#: Discovery talks to the source system, so a misconfigured credential can
#: otherwise leave the request hanging until the client gives up instead.
_CATALOG_TIMEOUT_SECONDS = 180


def _resolve_extractor(ctx: AppContext, plugin_type: str, name: str) -> str:
    """Resolve an extractor by its URL segments.

    Args:
        ctx: The application context.
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.

    Returns:
        The extractor's name.

    Raises:
        HTTPException: 422 when the plugin is not an extractor, since only
            extractors have a catalog to select from.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    if plugin.type is not PluginType.EXTRACTORS:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=f"{plugin.name!r} is a {plugin.type}, and only extractors select",
        )
    return plugin.name


def _declared(ctx: AppContext, service: SelectService) -> set[str]:
    """Return the patterns actually written down for this extractor.

    `current_select` reports the *effective* setting, which includes Meltano's
    default of selecting everything. Only what is declared can be removed -
    `update(remove=True)` calls `list.remove` on exactly this list - so the
    two have to be distinguished before the UI offers a delete control.

    The environment branch mirrors `SelectService.update`, which writes to the
    environment's plugin config whenever one is active.

    Args:
        ctx: The application context.
        service: The select service for the extractor.

    Returns:
        The declared patterns.
    """
    if ctx.project.environment is None:
        extras = service.extractor.extras
    else:
        extras = ctx.project.environment.get_plugin_config(
            service.extractor.type,
            service.extractor.name,
        ).extras
    return set(extras.get("select", ()))


def _parse(
    patterns: t.Iterable[str],
    declared: t.AbstractSet[str] = frozenset(),
) -> list[SelectPatternInfo]:
    """Describe raw patterns for the API.

    Args:
        patterns: The raw pattern strings.
        declared: The subset that is written down and so can be removed.

    Returns:
        The parsed patterns.
    """
    return [
        SelectPatternInfo(
            raw=parsed.raw,
            stream_pattern=parsed.stream_pattern,
            property_pattern=parsed.property_pattern,
            negated=parsed.negated,
            removable=parsed.raw in declared,
        )
        for parsed in map(SelectPattern.parse, patterns)
    ]


def _patterns(ctx: AppContext, extractor: str) -> SelectPatterns:
    """Read the patterns in effect for an extractor.

    Args:
        ctx: The application context.
        extractor: The extractor's name.

    Returns:
        The patterns, and where an edit would be written.
    """
    service = SelectService(ctx.project, extractor)
    return SelectPatterns(
        extractor=extractor,
        patterns=_parse(service.current_select, _declared(ctx, service)),
        environment=ctx.environment_name,
    )


@router.get("/plugins/{plugin_type}/{name}/select", response_model=SelectPatterns)
def get_select(plugin_type: str, name: str, ctx: CtxDep) -> SelectPatterns:
    """Return the select patterns in effect for an extractor.

    Reads `meltano.yml` only, so this never runs the extractor and always
    answers immediately.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        ctx: The application context.

    Returns:
        The patterns.
    """
    return _patterns(ctx, _resolve_extractor(ctx, plugin_type, name))


@router.post(
    "/plugins/{plugin_type}/{name}/select",
    response_model=SelectPatterns,
    status_code=status.HTTP_201_CREATED,
)
async def add_pattern(
    plugin_type: str,
    name: str,
    payload: AddPatternRequest,
    ctx: CtxDep,
) -> SelectPatterns:
    """Add a select pattern.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        payload: The stream and property globs, and whether to exclude.
        ctx: The application context.

    Returns:
        Every pattern now in effect.
    """
    extractor = _resolve_extractor(ctx, plugin_type, name)
    service = SelectService(ctx.project, extractor)

    # Serialized against every other project mutation: this rewrites
    # `meltano.yml` and drops caches shared by all requests.
    async with ctx.write_lock:
        await anyio.to_thread.run_sync(
            service.update,
            payload.streams,
            payload.properties,
            payload.exclude,
        )

    return _patterns(ctx, extractor)


@router.delete(
    "/plugins/{plugin_type}/{name}/select/{pattern:path}",
    response_model=SelectPatterns,
)
async def remove_pattern(
    plugin_type: str,
    name: str,
    pattern: str,
    ctx: CtxDep,
) -> SelectPatterns:
    """Remove one select pattern, addressed by its raw text.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        pattern: The pattern's raw form, e.g. `!orders.card_*`.
        ctx: The application context.

    Returns:
        The patterns that remain.

    Raises:
        HTTPException: 404 when the extractor does not have that pattern.
    """
    extractor = _resolve_extractor(ctx, plugin_type, name)
    service = SelectService(ctx.project, extractor)

    parsed = SelectPattern.parse(pattern)
    # `update` reassembles the raw form from these three, so a pattern that
    # does not round-trip could never have been stored by this endpoint.
    streams = parsed.stream_pattern
    properties = parsed.property_pattern or "*"

    async with ctx.write_lock:
        try:
            await anyio.to_thread.run_sync(
                lambda: service.update(
                    streams,
                    properties,
                    parsed.negated,
                    remove=True,
                ),
            )
        except ValueError as err:
            # `list.remove` on a pattern that is not stored.
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                detail=f"{extractor!r} has no select pattern {pattern!r}",
            ) from err

    return _patterns(ctx, extractor)


@router.delete("/plugins/{plugin_type}/{name}/select", response_model=SelectPatterns)
async def clear_patterns(
    plugin_type: str,
    name: str,
    ctx: CtxDep,
) -> SelectPatterns:
    """Remove every select pattern for an extractor.

    What remains is Meltano's default, which selects everything the catalog
    offers - so this is a reset, not a way to select nothing.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        ctx: The application context.

    Returns:
        The patterns in effect afterwards.
    """
    extractor = _resolve_extractor(ctx, plugin_type, name)
    service = SelectService(ctx.project, extractor)

    async with ctx.write_lock:
        await anyio.to_thread.run_sync(service.clear)

    return _patterns(ctx, extractor)


@router.get(
    "/plugins/{plugin_type}/{name}/select/catalog",
    response_model=SelectCatalog,
)
async def get_catalog(
    plugin_type: str,
    name: str,
    ctx: CtxDep,
    session: SessionDep,
    *,
    refresh: bool = Query(
        default=False,
        description=(
            "Ignore the cached catalog and ask the extractor again. Slower, "
            "and required after the source's own schema changes."
        ),
    ),
) -> SelectCatalog:
    """Return what the extractor can produce, and what is selected.

    This runs the extractor in discovery mode. A cached catalog makes it fast;
    without one - or with `refresh` - it talks to the source system.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The extractor's name.
        ctx: The application context.
        session: A system-database session, used to prepare the invoker.
        refresh: Whether to bypass the cached catalog.

    Returns:
        The catalog, annotated with each node's selection.

    Raises:
        HTTPException: 504 when discovery outruns the timeout, or 400 when the
            extractor cannot describe itself - most often because it is not
            installed, not configured, or does not support discovery.
    """
    extractor = _resolve_extractor(ctx, plugin_type, name)
    service = SelectService(ctx.project, extractor)

    try:
        with anyio.fail_after(_CATALOG_TIMEOUT_SECONDS):
            listed = await service.list_all(session, refresh=refresh)
    except TimeoutError as err:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=(
                f"{extractor!r} did not describe itself within "
                f"{_CATALOG_TIMEOUT_SECONDS}s. Check its configuration, or run "
                f"`meltano select {extractor} --list --all` to see the failure."
            ),
        ) from err
    except PluginExecutionError as err:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Could not read {extractor!r}'s catalog: {err}",
        ) from err

    streams = [
        SelectedStream(
            name=stream.key,
            selection=str(stream.selection),
            properties=[
                SelectedProperty(
                    name=prop.key,
                    # The CLI combines the two the same way; a property in an
                    # excluded stream is not selected however it is marked.
                    selection=str(stream.selection + prop.selection),
                )
                for prop in sorted(listed.properties.get(stream.key, ()))
            ],
        )
        for stream in sorted(listed.streams)
    ]

    return SelectCatalog(
        extractor=extractor,
        streams=streams,
        patterns=_parse(service.current_select, _declared(ctx, service)),
        selection_types=[str(value) for value in SelectionType],
    )
