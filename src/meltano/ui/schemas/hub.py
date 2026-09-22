"""Models for browsing Meltano Hub and adding what it lists to the project."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HubVariant(BaseModel):
    """One packaging of a Hub plugin.

    A plugin often has several, maintained by different people; the default is
    the one `meltano add` picks when none is named.
    """

    name: str
    is_default: bool


class HubPlugin(BaseModel):
    """A plugin listed on Meltano Hub."""

    name: str
    plugin_type: str = Field(description="Plural plugin type, e.g. 'extractors'.")
    default_variant: str
    variants: list[HubVariant]
    logo_url: str | None = None
    is_added: bool = Field(
        description="Whether this project already declares a plugin of this name.",
    )


class AddPluginRequest(BaseModel):
    """A request to add a Hub plugin to the project."""

    plugin_type: str = Field(description="Plural plugin type, e.g. 'extractors'.")
    name: str = Field(min_length=1)
    variant: str | None = Field(
        default=None,
        description="Defaults to the Hub's default variant.",
    )
    install: bool = Field(
        default=True,
        description=(
            "Install the plugin after adding it, as `meltano add` does. The "
            "install runs as a supervised task; see `run_id` in the response."
        ),
    )


class AddedPlugin(BaseModel):
    """The outcome of adding a plugin."""

    name: str
    type: str
    variant: str | None = None
    pip_url: str | None = None
    run_id: str | None = Field(
        default=None,
        description=(
            "The install task, followable at `/runs/{run_id}/events`. Null "
            "when the caller asked not to install."
        ),
    )
