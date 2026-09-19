"""Models for previewing an extractor's data and shaping it with steps."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field

# Imported at run time, not just for type checking: pydantic resolves
# these annotations when the model is built.
from meltano.ui.services.transforms import (  # noqa: TC001
    CastType,
    Operator,
    StepKind,
)


class TransformStep(BaseModel):
    """One edit in an ordered list, in the shape the UI presents it.

    Structured rather than an expression: the server compiles these into
    Meltano stream maps and applies them to preview rows, and never evaluates
    a string the caller wrote.
    """

    kind: StepKind = Field(description="drop, rename, cast or filter.")
    column: str = Field(min_length=1, description="The column the step acts on.")
    to: str | None = Field(default=None, description="New name, for 'rename'.")
    type: CastType | None = Field(default=None, description="Target, for 'cast'.")
    operator: Operator | None = Field(
        default=None,
        description="Comparison, for 'filter'.",
    )
    value: t.Any = Field(
        default=None,
        description="Value to compare against, for 'filter'.",
    )


class PreviewRequest(BaseModel):
    """A request to look at an extractor's data."""

    stream: str | None = Field(
        default=None,
        description="Only collect this stream. All streams when omitted.",
    )
    limit: int = Field(default=20, ge=1, le=500)
    steps: list[TransformStep] = Field(
        default_factory=list,
        description="Applied to the rows before they are returned.",
    )


class PreviewResponse(BaseModel):
    """Rows read from an extractor, after any steps were applied."""

    extractor: str
    stream: str | None = None
    columns: list[str] = Field(
        description="Columns present in the returned rows, in a stable order.",
    )
    schemas: dict[str, list[str]] = Field(
        default_factory=dict,
        description="Every stream the tap announced, and its declared columns.",
    )
    rows: list[dict[str, t.Any]] = Field(default_factory=list)
    row_count: int = Field(description="Rows returned, after filtering.")
    read_count: int = Field(description="Records read before steps were applied.")
    truncated: bool = Field(
        description="True when the extractor was stopped at the limit.",
    )
    timed_out: bool = False
    stream_map: dict[str, t.Any] = Field(
        default_factory=dict,
        description="The Meltano stream map these steps compile to.",
    )


class CompileRequest(BaseModel):
    """A request to compile steps without running anything."""

    steps: list[TransformStep] = Field(min_length=1)


class CompileResponse(BaseModel):
    """The stream map a step list compiles to."""

    stream_map: dict[str, t.Any]
