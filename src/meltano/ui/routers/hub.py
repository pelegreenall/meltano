"""Endpoints for browsing Meltano Hub and adding plugins to the project.

This is what lets a project be built up without a terminal: every other
endpoint in this server operates on plugins that are already declared, and
until now the only way to declare one was `meltano add`.

Hub is a remote service, so listing it can be slow or fail outright. That is
reported as such rather than as a server error - an unreachable Hub says
nothing about whether this project is healthy.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, Query, status

from meltano.core.hub.client import HubConnectionError, MeltanoHubService
from meltano.core.plugin import PluginType
from meltano.core.project_add_service import ProjectAddService
from meltano.core.project_plugins_service import PluginAlreadyAddedException
from meltano.core.utils import new_run_id
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.schemas.hub import AddedPlugin, AddPluginRequest, HubPlugin, HubVariant
from meltano.ui.services.run_manager import RunKind

if t.TYPE_CHECKING:
    from meltano.core.hub.schema import IndexedPlugin

router = APIRouter(tags=["hub"], dependencies=[Depends(require_auth)])

#: How long to wait on Hub before giving up. `MeltanoHubService` sets retries
#: but no socket timeout, so without this a hung Hub would hold the request
#: open indefinitely.
_HUB_TIMEOUT_SECONDS = 30


def _plugin_type(value: str) -> PluginType:
    """Resolve a plural plugin type from the path.

    Args:
        value: The plural type, e.g. "extractors".

    Returns:
        The plugin type.

    Raises:
        HTTPException: 404 for an unknown type, 422 for one Hub does not list.
    """
    try:
        plugin_type = PluginType.from_cli_argument(value)
    except ValueError as err:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plugin type {value!r}",
        ) from err

    if not plugin_type.discoverable:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=f"{value!r} are defined in the project, not listed on Hub",
        )

    return plugin_type


@router.get("/hub/{plugin_type}", response_model=list[HubPlugin])
async def list_hub_plugins(
    plugin_type: str,
    ctx: CtxDep,
    q: str | None = Query(
        default=None,
        description="Case-insensitive substring filter on the plugin's name.",
    ),
) -> list[HubPlugin]:
    """List the plugins of one type that Meltano Hub offers.

    Args:
        plugin_type: Plural plugin type from the path.
        ctx: The application context.
        q: Optional name filter.

    Returns:
        The matching plugins, ordered by name, each flagged with whether the
        project already declares it.

    Raises:
        HTTPException: 504 when Hub does not answer in time, or 502 when it
            cannot be reached at all.
    """
    resolved = _plugin_type(plugin_type)
    hub = MeltanoHubService(ctx.project)

    try:
        with anyio.fail_after(_HUB_TIMEOUT_SECONDS):
            # Blocking `requests` calls, so off the event loop. Cancelling the
            # await does not stop the thread - Python cannot kill one - but it
            # does stop this request from waiting on it.
            plugins: dict[str, IndexedPlugin] = await anyio.to_thread.run_sync(
                hub.get_plugins_of_type,
                resolved,
            )
    except TimeoutError as err:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Meltano Hub did not respond within {_HUB_TIMEOUT_SECONDS}s",
        ) from err
    except HubConnectionError as err:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            detail=f"Could not reach Meltano Hub: {err}",
        ) from err

    declared = {plugin.name for plugin in ctx.project.plugins.plugins()}
    needle = q.casefold() if q else None

    return sorted(
        (
            HubPlugin(
                name=name,
                plugin_type=str(resolved),
                default_variant=plugin.default_variant,
                variants=[
                    HubVariant(
                        name=variant,
                        is_default=variant == plugin.default_variant,
                    )
                    for variant in sorted(plugin.variants)
                ],
                logo_url=plugin.logo_url,
                is_added=name in declared,
            )
            for name, plugin in plugins.items()
            if needle is None or needle in name.casefold()
        ),
        key=lambda item: item.name,
    )


@router.post(
    "/plugins",
    response_model=AddedPlugin,
    status_code=status.HTTP_201_CREATED,
)
async def add_plugin(payload: AddPluginRequest, ctx: CtxDep) -> AddedPlugin:
    """Add a Hub plugin to the project.

    Mirrors `meltano add`, including installing afterwards by default. The
    install is far too slow to hold a request open, so it runs on the same
    supervised machinery as a pipeline and streams its output the same way.

    Args:
        payload: The plugin to add.
        ctx: The application context.

    Returns:
        The added plugin, with the install task's run ID when one was started.

    Raises:
        HTTPException: 409 when the project already declares the plugin, or
            502 when the definition cannot be fetched from Hub.
    """
    resolved = _plugin_type(payload.plugin_type)
    service = ProjectAddService(ctx.project)

    attrs: dict[str, t.Any] = {}
    if payload.variant is not None:
        attrs["variant"] = payload.variant

    def write() -> t.Any:  # noqa: ANN401
        return service.add(resolved, payload.name, **attrs)

    # Serialized against every other project mutation: adding writes
    # `meltano.yml` and a lockfile, and drops caches shared by all requests.
    async with ctx.write_lock:
        try:
            plugin = await anyio.to_thread.run_sync(write)
        except PluginAlreadyAddedException as err:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{payload.name!r} is already in this project",
            ) from err
        except HubConnectionError as err:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not reach Meltano Hub: {err}",
            ) from err

    run_id: str | None = None
    if payload.install:
        run_id = str(new_run_id())
        argv = ctx.run_manager.build_install_argv(
            str(plugin.type),
            plugin.name,
            environment=ctx.environment_name,
        )
        record = await ctx.run_manager.start(
            argv,
            kind=RunKind.install,
            run_id=run_id,
            environment=ctx.environment_name,
        )
        run_id = record.run_id

    return AddedPlugin(
        name=plugin.name,
        type=str(plugin.type),
        variant=getattr(plugin, "variant", None),
        pip_url=getattr(plugin, "pip_url", None),
        run_id=run_id,
    )
