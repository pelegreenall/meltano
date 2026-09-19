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
#:
#: 2 added `connection`: the database endpoint a connector points at, so a
#: reader can register the source without parsing Meltano's settings itself.
SOURCE_SCHEMA_VERSION = 2


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


class SourceConnection(BaseModel):
    """The database endpoint a connector points at.

    This is what makes a `.source` usable by something that is not Meltano:
    a reader learns where the data lives without having to know which of a
    connector's settings mean "host" this week.

    Present only when the connector is configured against a reachable
    database. An API extractor has no host, and a warehouse addressed by
    account name rather than host:port cannot be described this way either -
    both report null rather than an invented endpoint.

    The endpoint's *identity* only. Credentials stay in `settings`, where the
    sensitive ones are already withheld.
    """

    engine: str | None = Field(
        default=None,
        description="Database dialect, e.g. 'postgres', from the namespace.",
    )
    host: str
    port: int | None = None
    database: str | None = None
    derived_from: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Which setting supplied each field. The mapping from settings to "
            "an endpoint is by convention, not declaration, so a reader that "
            "disagrees can see exactly what was used."
        ),
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
    connection: SourceConnection | None = Field(
        default=None,
        description="The database endpoint, when this connector has one.",
    )
    select: list[str] = Field(
        default_factory=list,
        description="Select patterns. Extractors only; empty for other types.",
    )
    meltano_version: str = Field(description="The Meltano that produced this.")


class SourceProjection(BaseModel):
    """A `.source` expressed in the consuming system's vocabulary.

    The document is Meltano's own view of a connector; this is the same
    connection as the registry downstream expects to store. They are kept
    apart deliberately - folding one into the other would make the document
    claim a vocabulary it does not own, and the mapping between them is
    exactly the thing worth being able to read.

    Only the fields Meltano is authoritative for. A registry row also records
    how the database is *reached* - which agent serves it, the credential
    that agent authenticates with, whether it is live - and none of that is
    knowable here. Those fields stay absent rather than being sent as nulls
    that would overwrite what the gateway knows.
    """

    path: str = Field(
        description="Identity within the project, e.g. 'target-postgres.source'.",
    )
    name: str = Field(
        description=(
            "Display name. The consumer constrains this to 120 characters; "
            "it is reported as configured rather than truncated here."
        ),
    )
    engine: str | None = Field(
        default=None,
        description="Database dialect, e.g. 'postgres'.",
    )
    target_host: str = Field(
        description=(
            "The database's address as reached from its own network - what "
            "an agent beside it would dial, not a tunnel endpoint."
        ),
    )
    target_port: int | None = Field(
        default=None,
        description="The port on that host. Consumers expect 1-65535.",
    )


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
