"""Endpoints for reading and writing a plugin's configuration.

Secrets are handled by Meltano's own `AUTO` store, which routes any setting
marked sensitive to `.env` and refuses to write it anywhere that could be
committed. This layer therefore never chooses a store itself; it reports back
where the value landed so the UI can tell the user.

There is deliberately no endpoint that returns a secret's value. Reads go
through `redacted=True`, so a configured password comes back as the redaction
marker plus `is_set`. Anyone who needs the real value can read `.env`.
"""

from __future__ import annotations

import typing as t

import anyio
from fastapi import APIRouter, Depends, HTTPException, status

from meltano.core.plugin import PluginType
from meltano.core.plugin.error import PluginNotFoundError
from meltano.core.plugin.settings_service import PluginSettingsService
from meltano.core.settings_service import REDACTED_VALUE
from meltano.core.settings_store import SettingValueStore
from meltano.ui.deps import CtxDep, require_auth
from meltano.ui.schemas.config import (
    PluginConfig,
    SetSettingRequest,
    SetSettingResponse,
    SettingInfo,
)

if t.TYPE_CHECKING:
    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project
    from meltano.core.setting_definition import SettingDefinition
    from meltano.ui.context import AppContext

router = APIRouter(tags=["config"], dependencies=[Depends(require_auth)])

#: Sources that mean "nothing has been configured for this setting".
_UNSET_SOURCES = frozenset({"default", "inherited"})


def resolve_plugin(project: Project, plugin_type: str, name: str) -> ProjectPlugin:
    """Look up a plugin by its URL segments.

    Args:
        project: The project to search.
        plugin_type: Plural plugin type from the path, e.g. "extractors".
        name: The plugin's name.

    Returns:
        The matching plugin.

    Raises:
        HTTPException: 404 when the type or plugin is unknown.
    """
    try:
        resolved_type = PluginType.from_cli_argument(plugin_type)
    except ValueError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown plugin type {plugin_type!r}",
        ) from err

    try:
        return project.plugins.find_plugin(name, plugin_type=resolved_type)
    except PluginNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No {plugin_type} named {name!r} in this project",
        ) from err


def _kind_of(definition: SettingDefinition) -> str:
    """Return a widget hint for a setting.

    Args:
        definition: The setting definition.

    Returns:
        The kind as a plain string, defaulting to "string".
    """
    if definition.is_redacted:
        return "password"
    kind = definition.kind
    return str(getattr(kind, "value", kind)) if kind else "string"


def _describe(
    definition: SettingDefinition,
    metadata: dict[str, t.Any],
) -> SettingInfo:
    """Build the API view of one setting.

    Args:
        definition: The setting definition.
        metadata: The entry from `config_with_metadata` for this setting.

    Returns:
        The rendered setting.
    """
    source = str(getattr(metadata.get("source"), "value", metadata.get("source")))
    return SettingInfo(
        name=definition.name,
        label=getattr(definition, "label", None),
        description=(str(definition.description) if definition.description else None),
        kind=_kind_of(definition),
        sensitive=bool(definition.is_redacted),
        required=bool(getattr(definition, "required", False)),
        options=list(getattr(definition, "options", None) or []),
        env=getattr(definition, "env", None),
        value=metadata.get("value"),
        source=source,
        is_set=source not in _UNSET_SOURCES,
    )


def _settings_service(ctx: AppContext, plugin: ProjectPlugin) -> PluginSettingsService:
    return PluginSettingsService(ctx.project, plugin)


@router.get("/plugins/{plugin_type}/{name}/config", response_model=PluginConfig)
def read_config(plugin_type: str, name: str, ctx: CtxDep) -> PluginConfig:
    """Return every setting for a plugin, with sensitive values redacted.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        ctx: The application context.

    Returns:
        The plugin's settings and their current values.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    service = _settings_service(ctx, plugin)

    metadata = service.config_with_metadata(redacted=True)
    settings = [
        _describe(definition, metadata.get(definition.name, {}))
        for definition in service.definitions()
    ]

    return PluginConfig(
        name=plugin.name,
        type=str(plugin.type),
        settings=sorted(settings, key=lambda item: (not item.required, item.name)),
    )


@router.put(
    "/plugins/{plugin_type}/{name}/config/{setting}",
    response_model=SetSettingResponse,
)
async def set_setting(
    plugin_type: str,
    name: str,
    setting: str,
    payload: SetSettingRequest,
    ctx: CtxDep,
) -> SetSettingResponse:
    """Store a value for one setting.

    The store is always `AUTO`: Meltano sends sensitive settings to `.env` and
    will not place them anywhere they could be committed. The response reports
    the chosen store so the UI can show where the value went.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        setting: The setting's name.
        payload: The new value.
        ctx: The application context.

    Returns:
        The write's outcome, with the value redacted when sensitive.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    service = _settings_service(ctx, plugin)

    def write() -> tuple[t.Any, dict[str, t.Any]]:
        return service.set_with_metadata(
            setting,
            payload.value,
            store=SettingValueStore.AUTO,
        )

    # Serialized against every other project mutation: a write may touch
    # `meltano.yml`, whose reload invalidates caches shared by all requests.
    async with ctx.write_lock:
        _, metadata = await anyio.to_thread.run_sync(write)

    return _write_response(service, setting, metadata)


@router.delete(
    "/plugins/{plugin_type}/{name}/config/{setting}",
    response_model=SetSettingResponse,
)
async def unset_setting(
    plugin_type: str,
    name: str,
    setting: str,
    ctx: CtxDep,
) -> SetSettingResponse:
    """Remove a stored value, falling back to the setting's default.

    Args:
        plugin_type: Plural plugin type from the path.
        name: The plugin's name.
        setting: The setting's name.
        ctx: The application context.

    Returns:
        The setting's state after removal.
    """
    plugin = resolve_plugin(ctx.project, plugin_type, name)
    service = _settings_service(ctx, plugin)

    def write() -> dict[str, t.Any]:
        return service.unset(setting, store=SettingValueStore.AUTO)

    async with ctx.write_lock:
        metadata = await anyio.to_thread.run_sync(write)

    return _write_response(service, setting, metadata)


def _write_response(
    service: PluginSettingsService,
    setting: str,
    metadata: dict[str, t.Any],
) -> SetSettingResponse:
    """Build the response for a write, re-reading through the redacting path.

    Args:
        service: The settings service for the plugin.
        setting: The setting's name.
        metadata: Metadata returned by the write.

    Returns:
        The setting's state, with sensitive values redacted.
    """
    store = str(getattr(metadata.get("store"), "value", metadata.get("store")))

    definition = next(
        (item for item in service.definitions() if item.name == setting),
        None,
    )
    value, read_metadata = service.get_with_metadata(setting, redacted=True)
    source = str(
        getattr(read_metadata.get("source"), "value", read_metadata.get("source")),
    )

    return SetSettingResponse(
        name=setting,
        store=store,
        source=source,
        is_set=source not in _UNSET_SOURCES,
        # Belt and braces: the read above already redacts, but a sensitive
        # value must never reach the browser even if that changes.
        value=REDACTED_VALUE
        if (definition is not None and definition.is_redacted and value is not None)
        else value,
    )
