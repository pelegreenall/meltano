"""Endpoints producing `.source` documents for downstream readers.

A `.source` is a connector config: everything another system needs to run one
of this project's connectors, without that system having to understand a
Meltano project.

Secrets never appear in one. `PluginSettingsService` is read with
`redacted=True`, and a sensitive setting is emitted as the name of the
environment variable that supplies it. A `.source` is a file, frequently a
committed one, and the config endpoints already refuse to return a stored
secret; writing one to disk here would undo that.
"""

from __future__ import annotations

import json
import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, status

from meltano.core.plugin import PluginType
from meltano.core.select_service import SelectService
from meltano.core.settings_service import REDACTED_VALUE
from meltano.core.utils import get_meltano_version
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.routers.config import resolve_plugin
from meltano.ui.schemas.sources import (
    ExportedSource,
    ExportSourcesRequest,
    ExportSourcesResponse,
    SourceDocument,
    SourceSetting,
)

if t.TYPE_CHECKING:
    from pathlib import Path

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.ui.context import AppContext

router = APIRouter(tags=["sources"], dependencies=[Depends(require_auth)])

#: Sources that mean "nothing has been configured for this setting". Matches
#: the config router, which makes the same distinction for the same reason.
_UNSET_SOURCES = frozenset({"default", "inherited"})

#: The file extension downstream readers look for.
SOURCE_SUFFIX = ".source"

#: Meltano's own extras are stored alongside a connector's real settings and
#: are named with a leading underscore: `_select`, `_metadata`, `_state` and
#: friends. They configure *Meltano's* handling of the connector rather than
#: the connector itself, so a document meant for another runner leaves them
#: out. `_select` in particular is reported as the document's `select` field.
_EXTRA_PREFIX = "_"


def _describe(ctx: AppContext, plugin: ProjectPlugin) -> SourceDocument:
    """Build the `.source` document for one configured connector.

    Args:
        ctx: The application context.
        plugin: The plugin to describe.

    Returns:
        The document, with no secret values in it.
    """
    from meltano.core.plugin.settings_service import PluginSettingsService

    service = PluginSettingsService(ctx.project, plugin)
    metadata = service.config_with_metadata(redacted=True)

    settings = []
    for definition in service.definitions():
        if definition.name.startswith(_EXTRA_PREFIX):
            continue
        entry = metadata.get(definition.name, {})
        source = str(getattr(entry.get("source"), "value", entry.get("source")))
        sensitive = bool(definition.is_redacted)
        value = entry.get("value")
        settings.append(
            SourceSetting(
                name=definition.name,
                kind=(
                    "password"
                    if sensitive
                    else str(
                        getattr(definition.kind, "value", definition.kind) or "string",
                    )
                ),
                required=bool(getattr(definition, "required", False)),
                sensitive=sensitive,
                # `definition.env` is only an explicit override; Meltano
                # derives a name for every setting, and that derived one is
                # what a reader must set to supply the value.
                env=service.setting_env(definition),
                # Belt and braces: `redacted=True` already replaces the value,
                # but a document that leaked one would be hard to notice.
                value=None if sensitive or value == REDACTED_VALUE else value,
                is_set=source not in _UNSET_SOURCES,
            ),
        )

    select: list[str] = []
    if plugin.type is PluginType.EXTRACTORS:
        select = list(SelectService(ctx.project, plugin.name).current_select)

    return SourceDocument(
        name=plugin.name,
        type=str(plugin.type),
        label=getattr(plugin, "label", None),
        variant=getattr(plugin, "variant", None),
        namespace=getattr(plugin, "namespace", None),
        pip_url=getattr(plugin, "pip_url", None),
        executable=getattr(plugin, "executable", None),
        capabilities=[str(c) for c in (getattr(plugin, "capabilities", None) or [])],
        environment=ctx.environment_name,
        settings=sorted(settings, key=lambda item: item.name),
        select=select,
        meltano_version=get_meltano_version(),
    )


def _resolve_types(values: t.Iterable[str]) -> list[PluginType]:
    """Turn plural type names from a request into plugin types.

    Args:
        values: The plural names, e.g. "extractors".

    Returns:
        The resolved types.

    Raises:
        HTTPException: 422 when a name is not a plugin type.
    """
    known = {str(kind): kind for kind in PluginType} | {
        kind.singular: kind for kind in PluginType
    }
    unknown = [value for value in values if value not in known]
    if unknown:
        raise HTTPException(
            HTTP_422_UNPROCESSABLE,
            detail=f"Unknown plugin type {unknown[0]!r}",
        )
    return [known[value] for value in values]


@router.get("/sources", response_model=list[SourceDocument])
def list_sources(ctx: CtxDep) -> list[SourceDocument]:
    """Return a `.source` document for every connector in the project.

    Args:
        ctx: The application context.

    Returns:
        The documents, ordered by type then name.
    """
    wanted = {PluginType.EXTRACTORS, PluginType.LOADERS}
    plugins = [p for p in ctx.project.plugins.plugins() if p.type in wanted]
    documents = [_describe(ctx, plugin) for plugin in plugins]
    return sorted(documents, key=lambda doc: (doc.type, doc.name))


@router.get("/sources/{plugin_type}/{name}", response_model=SourceDocument)
def get_source(plugin_type: str, name: str, ctx: CtxDep) -> SourceDocument:
    """Return the `.source` document for one connector.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The connector's name.
        ctx: The application context.

    Returns:
        The document.
    """
    return _describe(ctx, resolve_plugin(ctx.project, plugin_type, name))


@router.post(
    "/sources/export",
    response_model=ExportSourcesResponse,
    status_code=status.HTTP_201_CREATED,
)
async def export_sources(
    payload: ExportSourcesRequest,
    ctx: CtxDep,
) -> ExportSourcesResponse:
    """Write a `.source` file per connector, for another system to read.

    Args:
        payload: Where to write, and which types to include.
        ctx: The application context.

    Returns:
        The directory and the files written.

    Raises:
        HTTPException: 400 when the directory cannot be written.
    """
    types = _resolve_types(payload.plugin_types)
    plugins = [p for p in ctx.project.plugins.plugins() if p.type in set(types)]
    documents = [(p, _describe(ctx, p)) for p in plugins]

    target: Path = ctx.project.root.joinpath(payload.path)

    def write() -> list[ExportedSource]:
        target.mkdir(parents=True, exist_ok=True)
        written = []
        for plugin, document in documents:
            path = target / f"{plugin.name}{SOURCE_SUFFIX}"
            path.write_text(
                json.dumps(document.model_dump(mode="json"), indent=2) + "\n",
                encoding="utf-8",
            )
            written.append(
                ExportedSource(
                    name=plugin.name,
                    type=str(plugin.type),
                    path=str(path),
                ),
            )
        return written

    try:
        # Writes files rather than `meltano.yml`, so no project write lock is
        # needed; the threadpool is only to keep the blocking IO off the loop.
        written = await anyio.to_thread.run_sync(write)
    except OSError as err:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Could not write to {target}: {err}",
        ) from err

    return ExportSourcesResponse(
        directory=str(target),
        written=sorted(written, key=lambda item: (item.type, item.name)),
    )
