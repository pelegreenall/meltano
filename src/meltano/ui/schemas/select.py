"""Models for extractor selection.

Two things live here and they are deliberately kept apart:

* the **patterns**, which are globs stored in `meltano.yml` and are always
  readable; and
* the **catalog**, which is what the extractor reports when asked what it can
  produce, and which requires actually running it.

Meltano's model is patterns, not per-entity flags: selecting a stream writes
`stream.*`, and what that ends up selecting depends on the catalog. Reporting
both is what lets the UI show a pattern's effect rather than just its text.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SelectPatternInfo(BaseModel):
    """One select pattern, parsed."""

    raw: str = Field(description="The pattern as stored, e.g. '!orders.card_*'.")
    stream_pattern: str
    property_pattern: str | None = None
    negated: bool = Field(
        description="True for an exclusion, written with a leading '!'.",
    )
    removable: bool = Field(
        default=True,
        description=(
            "False for a pattern that is not declared in the project - "
            "Meltano's own default, or one inherited from a parent plugin. "
            "There is nothing to delete, so removing it would fail."
        ),
    )


class SelectPatterns(BaseModel):
    """Every select pattern in effect for an extractor."""

    extractor: str
    patterns: list[SelectPatternInfo]
    environment: str | None = Field(
        default=None,
        description=(
            "The environment writes land in. When an environment is active, "
            "core stores select patterns on its plugin config rather than on "
            "the top-level plugin, so this says where an edit will go."
        ),
    )


class AddPatternRequest(BaseModel):
    """A request to add one select pattern.

    The two halves are kept separate rather than taking a raw pattern string,
    so that a '.' inside a name cannot be mistaken for the delimiter.
    """

    streams: str = Field(default="*", min_length=1)
    properties: str = Field(default="*", min_length=1)
    exclude: bool = Field(
        default=False,
        description="Write the pattern as an exclusion.",
    )


class SelectedProperty(BaseModel):
    """One property in the extractor's catalog."""

    name: str
    selection: str = Field(
        description=(
            "Effective selection: the stream's selection combined with the "
            "property's, matching what `meltano select --list` shows."
        ),
    )


class SelectedStream(BaseModel):
    """One stream in the extractor's catalog."""

    name: str
    selection: str
    properties: list[SelectedProperty] = Field(default_factory=list)


class SelectCatalog(BaseModel):
    """What an extractor can produce, and what is currently selected."""

    extractor: str
    streams: list[SelectedStream]
    patterns: list[SelectPatternInfo] = Field(
        description="The patterns that produced these selections.",
    )
    selection_types: list[str] = Field(
        description="Every selection value that can appear, for the UI's legend.",
    )
