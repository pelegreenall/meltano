"""Models for the named mappings a mapper plugin carries.

A mapping is a named block of stream maps stored under a mapper in
`meltano.yml`. `meltano run tap my-mapping target` names one, which is why the
name has to be unique across every mapper in the project.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field

# Imported at run time: pydantic resolves this annotation when the model
# is built.
from meltano.ui.schemas.transforms import TransformStep  # noqa: TC001


class MappingInfo(BaseModel):
    """One mapping, as stored."""

    name: str
    mapper: str = Field(description="The mapper plugin that carries it.")
    streams: list[str] = Field(
        default_factory=list,
        description="The streams this mapping has entries for.",
    )
    stream_maps: dict[str, t.Any] = Field(
        default_factory=dict,
        description="Meltano's own format: stream name to column expressions.",
    )


class SaveMappingRequest(BaseModel):
    """A request to store a step list as a mapping.

    The steps are compiled here rather than accepting a stream map directly,
    so what is saved is what the preview showed.
    """

    name: str = Field(min_length=1, description="The mapping's name.")
    stream: str = Field(
        min_length=1,
        description="The stream the steps apply to.",
    )
    steps: list[TransformStep] = Field(min_length=1)
    mapper: str | None = Field(
        default=None,
        description=(
            "Which mapper to store it under. Required only when the project "
            "has more than one."
        ),
    )
    overwrite: bool = Field(
        default=False,
        description="Replace a mapping of this name instead of refusing.",
    )
