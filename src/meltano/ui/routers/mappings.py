"""Endpoints for the named mappings a mapper plugin carries.

Saving a step list here is what turns a preview into something a pipeline can
run: `meltano run tap my-mapping target`.

Mappings are stored under a mapper in `meltano.yml`, and Meltano expands each
one into a synthetic plugin when it parses the file. Those synthetic entries
appear alongside the real mapper in `plugins.mappers`, so everything here
filters them out with `is_mapping()` before touching anything - writing one
back would add a mapper to the project that nobody asked for.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, Response, status

from meltano.core.plugin import PluginType
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.schemas.mappings import (
    MapperStatus,
    MappingInfo,
    SaveMappingRequest,
)
from meltano.ui.services.transforms import TransformError, compile_steps

if t.TYPE_CHECKING:
    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.ui.context import AppContext

router = APIRouter(tags=["mappings"], dependencies=[Depends(require_auth)])

#: What `meltano add mapper` installs, and the one these endpoints suggest
#: when a project has no mapper yet.
DEFAULT_MAPPER = "meltano-map-transformer"


def _mappers(ctx: AppContext) -> list[ProjectPlugin]:
    """Return the project's real mapper plugins.

    Args:
        ctx: The application context.

    Returns:
        The mappers, excluding the synthetic per-mapping plugins Meltano
        generates when it parses the file.
    """
    return [
        plugin
        for plugin in ctx.project.plugins.get_plugins_of_type(PluginType.MAPPERS)
        if not plugin.is_mapping()
    ]


def _entries(mapper: ProjectPlugin) -> list[dict[str, t.Any]]:
    """Return a mapper's stored mappings.

    `mappings` lives in the plugin's extras rather than its own attributes,
    which is also where a write has to go: assigning `plugin.mappings` looks
    like it works for a longer list and silently does nothing for a shorter
    one, because the extras are what gets serialized.

    Args:
        mapper: The mapper plugin.

    Returns:
        The mapping entries, as stored.
    """
    entries = mapper.extras.get("mappings") or []
    return [dict(entry) for entry in entries]


@router.get("/mappings/mapper", response_model=MapperStatus)
def mapper_status(ctx: CtxDep) -> MapperStatus:
    """Report whether a mapping saved here would actually be applied.

    Registered before `/mappings/{name}` would be: a literal path has to win
    over a parameterised one, or "mapper" reads as a mapping's name.

    Args:
        ctx: The application context.

    Returns:
        The mapper that would carry a mapping, and whether it is installed.
    """
    mappers = _mappers(ctx)
    if not mappers:
        return MapperStatus(name=None, is_installed=False, suggested=DEFAULT_MAPPER)

    mapper = mappers[0]
    # The interpreter is the evidence of an install, as it is on the plugin
    # inventory: `meltano add --no-install` leaves a directory without one.
    venv = ctx.project.dirs.plugin(mapper, "venv", make_dirs=False)
    installed = (venv / "bin" / "python").exists() or (
        venv / "Scripts" / "python.exe"
    ).exists()

    return MapperStatus(
        name=mapper.name,
        is_installed=installed,
        suggested=DEFAULT_MAPPER,
    )


@router.get("/mappings", response_model=list[MappingInfo])
def list_mappings(ctx: CtxDep) -> list[MappingInfo]:
    """List every mapping in the project.

    Args:
        ctx: The application context.

    Returns:
        The mappings, ordered by name.
    """
    found = []
    for mapper in _mappers(ctx):
        for entry in _entries(mapper):
            stream_maps = (entry.get("config") or {}).get("stream_maps") or {}
            found.append(
                MappingInfo(
                    name=entry.get("name", ""),
                    mapper=mapper.name,
                    streams=sorted(stream_maps),
                    stream_maps=stream_maps,
                ),
            )
    return sorted(found, key=lambda item: item.name)


def _resolve_mapper(ctx: AppContext, requested: str | None) -> ProjectPlugin:
    """Choose which mapper a mapping should be stored under.

    Args:
        ctx: The application context.
        requested: The mapper the caller named, if any.

    Returns:
        The mapper plugin.

    Raises:
        HTTPException: 422 when the project has no mapper, when the named one
            does not exist, or when there are several and none was named.
    """
    mappers = _mappers(ctx)

    if not mappers:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=(
                "This project has no mapper to store a mapping under. Add one "
                f"first - `{DEFAULT_MAPPER}` is the usual choice."
            ),
        )

    if requested is None:
        if len(mappers) > 1:
            names = ", ".join(sorted(mapper.name for mapper in mappers))
            raise HTTPException(
                HTTP_422_UNPROCESSABLE,
                detail=f"This project has several mappers; name one of: {names}",
            )
        return mappers[0]

    for mapper in mappers:
        if mapper.name == requested:
            return mapper

    raise HTTPException(
        HTTP_422_UNPROCESSABLE,
        detail=f"No mapper named {requested!r} in this project",
    )


def _find_existing(ctx: AppContext, name: str) -> tuple[ProjectPlugin, int] | None:
    """Locate a mapping by name across every mapper.

    Args:
        ctx: The application context.
        name: The mapping's name.

    Returns:
        The mapper holding it and the entry's index, or None.
    """
    for mapper in _mappers(ctx):
        for index, entry in enumerate(_entries(mapper)):
            if entry.get("name") == name:
                return mapper, index
    return None


def _write(ctx: AppContext, mapper_name: str, entries: list[dict[str, t.Any]]) -> None:
    """Replace a mapper's mappings in `meltano.yml`.

    Written through `extras`, which is where `mappings` actually lives.

    Args:
        ctx: The application context.
        mapper_name: The mapper to write to.
        entries: The mappings it should now carry.
    """
    with ctx.project.config_service.update_meltano_yml() as meltano_yml:
        for plugin in meltano_yml["plugins"]["mappers"]:
            if plugin.name == mapper_name and not plugin.is_mapping():
                plugin.extras["mappings"] = entries
                return


@router.post(
    "/mappings",
    response_model=MappingInfo,
    status_code=status.HTTP_201_CREATED,
)
async def save_mapping(payload: SaveMappingRequest, ctx: CtxDep) -> MappingInfo:
    """Store a step list as a named mapping.

    Args:
        payload: The name, the stream, and the steps to compile.
        ctx: The application context.

    Returns:
        The stored mapping.

    Raises:
        HTTPException: 422 when the steps do not compile or no mapper fits,
            409 when the name is taken and `overwrite` was not set.
    """
    try:
        stream_map = compile_steps([step.model_dump() for step in payload.steps])
    except TransformError as err:
        raise HTTPException(HTTP_422_UNPROCESSABLE, detail=str(err)) from err

    existing = _find_existing(ctx, payload.name)
    if existing is not None and not payload.overwrite:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=(
                f"A mapping named {payload.name!r} already exists on "
                f"{existing[0].name!r}"
            ),
        )

    # An overwrite stays where it already lives: moving it between mappers
    # would silently change which plugin runs it.
    mapper = existing[0] if existing else _resolve_mapper(ctx, payload.mapper)

    entries = _entries(mapper)
    entry = {
        "name": payload.name,
        "config": {"stream_maps": {payload.stream: stream_map}},
    }
    if existing is not None:
        entries[existing[1]] = entry
    else:
        entries.append(entry)

    # Serialized against every other project mutation, like any other write.
    async with ctx.write_lock:
        await anyio.to_thread.run_sync(_write, ctx, mapper.name, entries)

    return MappingInfo(
        name=payload.name,
        mapper=mapper.name,
        streams=[payload.stream],
        stream_maps={payload.stream: stream_map},
    )


@router.delete("/mappings/{name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mapping(name: str, ctx: CtxDep) -> Response:
    """Remove a mapping from the project.

    Args:
        name: The mapping's name.
        ctx: The application context.

    Returns:
        An empty 204 response.

    Raises:
        HTTPException: 404 when no mapping of that name exists.
    """
    existing = _find_existing(ctx, name)
    if existing is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"No mapping named {name!r} in this project",
        )

    mapper, index = existing
    entries = _entries(mapper)
    del entries[index]

    async with ctx.write_lock:
        await anyio.to_thread.run_sync(_write, ctx, mapper.name, entries)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
