"""Models for `.source` documents - connector configs for downstream readers.

A `.source` describes one configured connector completely enough for another
system to run it, without being a Meltano project itself.

Secrets are deliberately absent. Every sensitive setting is emitted as the
*name of the environment variable* that supplies it, never its value: a
`.source` is a file on disk, often in version control, and the rest of this
server already refuses to hand back a secret it has stored.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field

#: Bumped when the document's shape changes in a way a reader must notice.
#: Emitted in every document so a consumer can refuse one it does not
#: understand rather than silently misreading it.
SOURCE_SCHEMA_VERSION = 1


class SourceSetting(BaseModel):
    """One setting of a configured connector."""

    name: str
    kind: str = Field(description="string, password, boolean, integer, array, object…")
    required: bool
    sensitive: bool = Field(
        description="When true, `value` is null and `env` names the variable.",
    )
    env: str | None = Field(
        default=None,
        description="Environment variable this setting reads from at runtime.",
    )
    value: t.Any = Field(
        default=None,
        description="The configured value. Always null when `sensitive`.",
    )
    is_set: bool = Field(
        description="False when only the connector's own default applies.",
    )


class SourceDocument(BaseModel):
    """A `.source` document: one connector, as configured in this project."""

    schema_version: int = Field(
        default=SOURCE_SCHEMA_VERSION,
        description="The `.source` format version this document conforms to.",
    )
    name: str
    type: str = Field(description="Plural plugin type, e.g. 'extractors'.")
    label: str | None = None
    variant: str | None = None
    namespace: str | None = None
    pip_url: str | None = Field(
        default=None,
        description="How the connector is installed; null for a local executable.",
    )
    executable: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    environment: str | None = Field(
        default=None,
        description="The Meltano environment these values were read from.",
    )
    settings: list[SourceSetting] = Field(default_factory=list)
    select: list[str] = Field(
        default_factory=list,
        description="Select patterns. Extractors only; empty for other types.",
    )
    meltano_version: str = Field(description="The Meltano that produced this.")


class ExportSourcesRequest(BaseModel):
    """A request to write `.source` files to disk."""

    path: str = Field(
        default="sources",
        description=(
            "Directory to write into, absolute or relative to the project "
            "root. Created if missing."
        ),
    )
    plugin_types: list[str] = Field(
        default_factory=lambda: ["extractors", "loaders"],
        description="Which plugin types to export.",
    )


class ExportedSource(BaseModel):
    """One file written by an export."""

    name: str
    type: str
    path: str


class ExportSourcesResponse(BaseModel):
    """The outcome of an export."""

    directory: str
    written: list[ExportedSource]
