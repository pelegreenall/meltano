"""Models for reading and writing plugin configuration."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field


class SettingInfo(BaseModel):
    """One configurable setting, with enough metadata to render a form field."""

    name: str
    label: str | None = None
    description: str | None = None
    kind: str = Field(
        default="string",
        description="Widget hint: string, password, boolean, integer, array, object…",
    )
    sensitive: bool = Field(
        description="Whether the value is a secret. Secrets are never returned.",
    )
    required: bool = False
    options: list[dict[str, t.Any]] = Field(default_factory=list)
    env: str | None = Field(
        default=None,
        description="Environment variable that also sets this value.",
    )
    value: t.Any = Field(
        default=None,
        description="Current value, or '(redacted)' when the setting is sensitive.",
    )
    source: str = Field(description="Where the value came from: default, dotenv, …")
    is_set: bool = Field(description="Whether anything other than a default applies.")


class PluginConfig(BaseModel):
    """A plugin's full configuration surface."""

    name: str
    type: str
    settings: list[SettingInfo]


class SetSettingRequest(BaseModel):
    """A new value for one setting."""

    value: t.Any = Field(description="The value to store. Type follows the kind.")


class SetSettingResponse(BaseModel):
    """The outcome of a write, including where the value was placed."""

    name: str
    store: str = Field(description="Where Meltano wrote the value, e.g. 'dotenv'.")
    source: str
    is_set: bool
    value: t.Any = Field(default=None, description="Redacted when sensitive.")
