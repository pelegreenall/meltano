"""Models for the plugin inventory endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PluginInfo(BaseModel):
    """A plugin declared in the project's `meltano.yml`."""

    name: str
    type: str = Field(description="Plural plugin type, e.g. 'extractors'.")
    label: str | None = Field(
        default=None,
        description="Human-readable name from the Hub definition.",
    )
    variant: str | None = None
    docs: str | None = Field(
        default=None,
        description="Link to the plugin's documentation on Meltano Hub.",
    )
    is_installed: bool = Field(
        description="Whether the plugin's virtual environment exists on disk.",
    )


class PluginTaskRequest(BaseModel):
    """Options for a plugin-scoped background task."""

    clean: bool = Field(
        default=False,
        description="Reinstall from scratch rather than upgrading in place.",
    )


class PluginTaskAccepted(BaseModel):
    """A started task the caller can follow on the runs stream."""

    run_id: str
    kind: str
    warning: str | None = Field(
        default=None,
        description="Something the user should know about what this task does.",
    )
