"""Endpoints exposing the project's plugin inventory."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from meltano.core.plugin import PluginType
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.routers.config import resolve_plugin
from meltano.ui.schemas.plugins import (
    PluginCommand,
    PluginInfo,
    PluginTaskAccepted,
    PluginTaskRequest,
)
from meltano.ui.services.run_manager import RunKind

router = APIRouter(tags=["plugins"], dependencies=[Depends(require_auth)])


@router.get("/plugins", response_model=list[PluginInfo])
def list_plugins(ctx: CtxDep) -> list[PluginInfo]:
    """List every plugin declared in the project.

    Args:
        ctx: The application context.

    Returns:
        The project's plugins, ordered by type then name.
    """
    plugins = []
    for plugin in ctx.project.plugins.plugins():
        # `make_dirs=False` matters twice: a GET must not write to disk, and
        # the directory-creating default would make every plugin look
        # installed. The interpreter is the real evidence of an install -
        # `meltano add --no-install` leaves the directory behind without one.
        venv = ctx.project.dirs.plugin(plugin, "venv", make_dirs=False)
        installed = (venv / "bin" / "python").exists() or (
            venv / "Scripts" / "python.exe"
        ).exists()
        plugins.append(
            PluginInfo(
                name=plugin.name,
                type=str(plugin.type),
                label=getattr(plugin, "label", None),
                variant=getattr(plugin, "variant", None),
                docs=getattr(plugin, "docs", None),
                is_installed=installed,
            ),
        )

    return sorted(plugins, key=lambda item: (item.type, item.name))


@router.get(
    "/plugins/{plugin_type}/{name}/commands",
    response_model=list[PluginCommand],
)
def list_plugin_commands(
    plugin_type: str,
    name: str,
    ctx: CtxDep,
) -> list[PluginCommand]:
    """List the commands a plugin declares.

    This is what makes a transformer reachable from the browser. `meltano run`
    takes a block spelled `plugin:command`, so a plugin that declares `run`,
    `test` and `build` - dbt does - offers three pipeline steps rather than
    one, and there is no other way to discover them from the API.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        ctx: The application context.

    Returns:
        The commands, ordered by name. Empty for the many plugins that
        declare none.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    return sorted(
        (
            PluginCommand(
                name=command_name,
                description=command.description,
                # Inherited from the plugin's Hub definition, so this is the
                # argv a run would actually pass - worth showing, because
                # `docs-generate` running `docs generate` is not obvious.
                args=command.args or "",
                block=f"{plugin.name}:{command_name}",
            )
            for command_name, command in plugin.all_commands.items()
        ),
        key=lambda command: command.name,
    )


#: `LoaderTestService` writes a real row into the destination, so the UI must
#: say so before the task starts rather than after it has already happened.
_LOADER_TEST_WARNING = (
    "Testing a loader writes a `meltano_test_stream` table to the destination."
)


@router.post(
    "/plugins/{plugin_type}/{name}/install",
    response_model=PluginTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_plugin(
    plugin_type: str,
    name: str,
    payload: PluginTaskRequest,
    ctx: CtxDep,
) -> PluginTaskAccepted:
    """Install a plugin's virtual environment as a supervised task.

    Installing builds a venv and downloads packages, which is far too slow to
    hold a request open, so it runs on the same machinery as pipeline runs and
    streams its output the same way.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        payload: Task options.
        ctx: The application context.

    Returns:
        The accepted task, followable at `/runs/{run_id}/events`.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    argv = ctx.run_manager.build_install_argv(
        str(plugin.type),
        plugin.name,
        environment=ctx.environment_name,
        clean=payload.clean,
    )
    record = await ctx.run_manager.start(
        argv,
        kind=RunKind.install,
        environment=ctx.environment_name,
    )
    return PluginTaskAccepted(run_id=record.run_id, kind=record.kind.value)


@router.post(
    "/plugins/{plugin_type}/{name}/test",
    response_model=PluginTaskAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def test_plugin(
    plugin_type: str,
    name: str,
    ctx: CtxDep,
) -> PluginTaskAccepted:
    """Check a plugin's configuration by connecting with it.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        ctx: The application context.

    Returns:
        The accepted task, followable at `/runs/{run_id}/events`.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    argv = ctx.run_manager.build_test_argv(
        str(plugin.type),
        plugin.name,
        environment=ctx.environment_name,
    )
    record = await ctx.run_manager.start(
        argv,
        kind=RunKind.test,
        environment=ctx.environment_name,
    )
    return PluginTaskAccepted(
        run_id=record.run_id,
        kind=record.kind.value,
        warning=(_LOADER_TEST_WARNING if plugin.type is PluginType.LOADERS else None),
    )
