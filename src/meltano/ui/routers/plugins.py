"""Endpoints exposing the project's plugin inventory."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.schemas.plugins import PluginInfo

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
