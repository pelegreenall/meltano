"""Models for shaping a whole table rather than its rows one at a time."""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field

# Imported at run time: pydantic resolves these annotations when the model is
# built.
from meltano.ui.services.tables import (  # noqa: TC001
    Aggregate,
    TableOperator,
    TableStepKind,
)


class AggregateSpec(BaseModel):
    """One aggregation within a grouping step."""

    fn: Aggregate = Field(description="sum, count, count_distinct, avg, min or max.")
    column: str | None = Field(
        default=None,
        description="The column to aggregate. Omitted only for 'count'.",
    )
    alias: str | None = Field(
        default=None,
        serialization_alias="as",
        validation_alias="as",
        description="What to call the result. Derived from the function when omitted.",
    )


class TableStep(BaseModel):
    """One table-level edit, in the shape the UI presents it.

    Structured for the same reason as `TransformStep`: these compile to SQL,
    and nothing a caller writes is interpolated into it.
    """

    kind: TableStepKind = Field(
        description="filter, group_by, sort, top_n or distinct.",
    )
    column: str | None = Field(
        default=None,
        description="The column, for 'filter' and 'sort'.",
    )
    operator: TableOperator | None = Field(
        default=None,
        description="Comparison, for 'filter'.",
    )
    value: t.Any = Field(default=None, description="Value, for 'filter'.")
    by: list[str] = Field(
        default_factory=list,
        description="Columns to group on, for 'group_by'.",
    )
    aggregates: list[AggregateSpec] = Field(
        default_factory=list,
        description="What to compute per group, for 'group_by'.",
    )
    desc: bool = Field(default=False, description="Descending, for 'sort'.")
    n: int | None = Field(default=None, description="Row count, for 'top_n'.")
    columns: list[str] = Field(
        default_factory=list,
        description="Columns to deduplicate on, for 'distinct'.",
    )


class TableCompileRequest(BaseModel):
    """A request to compile table steps into SQL without running anything."""

    steps: list[TableStep] = Field(min_length=1)
    source: str = Field(
        default="source",
        min_length=1,
        description="What the query selects from - a table, or a dbt ref.",
    )
    dialect: str = Field(
        default="postgres",
        min_length=1,
        description="The SQL dialect to render for.",
    )


class TableCompileResponse(BaseModel):
    """The query a table-level step list compiles to."""

    sql: str
    dialect: str
