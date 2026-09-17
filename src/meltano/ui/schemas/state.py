"""Models for the state endpoints.

"State" here is *Singer* state - the bookmarks that let an incremental
extraction resume where it left off. It is not the `Job.state` column, which
records whether a run succeeded and is surfaced by the runs endpoints.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field


class StateSummary(BaseModel):
    """One state ID, without its payload."""

    state_id: str
    has_state: bool = Field(
        description="False for a state ID that exists but holds nothing.",
    )
    streams: list[str] = Field(
        default_factory=list,
        description="The bookmarked streams, from the payload's `bookmarks` key.",
    )


class StateDetail(BaseModel):
    """One state ID and the payload a run would resume from."""

    state_id: str
    state: dict[str, t.Any] = Field(
        description="The merged Singer state; empty when nothing is bookmarked.",
    )
    streams: list[str] = Field(default_factory=list)


class SetStateRequest(BaseModel):
    """A request to overwrite a state ID's payload.

    Overwriting is what the CLI prompts before doing, because the previous
    bookmarks are not recoverable afterwards.
    """

    state: dict[str, t.Any] = Field(
        description="Singer state. Must have a top-level `singer_state` key.",
    )
