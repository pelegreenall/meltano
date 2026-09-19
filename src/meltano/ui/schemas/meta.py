"""Models for the meta and project endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class Health(BaseModel):
    """Liveness response."""

    status: str = Field(description="Always 'ok' when the server is serving.")


class ServerMeta(BaseModel):
    """Describes the running server and the project it is bound to."""

    meltano_version: str = Field(description="Version of the running Meltano.")
    project_root: str = Field(description="Absolute path to the project root.")
    environment: str | None = Field(
        default=None,
        description=(
            "Active Meltano environment. Fixed for the lifetime of the server "
            "process; changing it requires a restart."
        ),
    )
    readonly: bool = Field(
        description="Whether mutating requests are refused by this server.",
    )
    host: str
    port: int


class ProjectInfo(BaseModel):
    """Summary of the project being served."""

    root: str
    readonly: bool
    environment: str | None = None
    environments: list[str] = Field(
        default_factory=list,
        description="Names of every environment defined in meltano.yml.",
    )
