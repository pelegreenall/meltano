"""Turning table-level shaping steps into SQL.

The row-level steps in `transforms.py` compile to Meltano stream maps, which a
mapper applies to one record at a time. Everything that needs to see more than
one record at once - grouping, sorting, deduplicating, taking the first N -
cannot be expressed there at all, and belongs in SQL after the data has landed.

The model is the same as `transforms.py`: an ordered list of small, inspectable
steps. What differs is where they run and what they compile to. These produce a
query, rendered by sqlglot, which becomes a dbt model in production and is run
over previewed rows by DuckDB while someone is still editing.

Ordering is the whole difficulty. Steps read sequentially - each applies to the
result of the one before - while SQL is declarative and has its own opinion
about what runs when. A filter written before a grouping restricts the rows
going in; the same filter written after restricts the groups coming out. Rather
than deciding between `WHERE` and `HAVING` by inspection, an aggregation closes
the current query and everything after it operates on that result as a
subquery. The SQL is more nested than a person would write, and it means
exactly what the step list says.
"""

from __future__ import annotations

import json
import tempfile
import typing as t
from pathlib import Path

from sqlglot import exp, select
from sqlglot.errors import SqlglotError

#: Kinds of table-level step, in the vocabulary the UI presents.
TableStepKind = t.Literal["filter", "group_by", "sort", "top_n", "distinct"]

#: Aggregations a grouping step can apply. Deliberately closed: each maps to
#: one sqlglot expression, so nothing here interpolates a caller's text into
#: SQL.
Aggregate = t.Literal["sum", "count", "count_distinct", "avg", "min", "max"]

#: Comparisons a table-level filter can make. Mirrors `transforms.Operator` so
#: the two step lists read the same way, minus `contains`, which is `LIKE`
#: here and worth spelling differently.
TableOperator = t.Literal[
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "contains",
    "is_null",
    "not_null",
]

#: The dialect a compiled query is rendered for when none is named. Matches
#: the loader this project is most often pointed at.
DEFAULT_DIALECT = "postgres"


class TableStepError(Exception):
    """A table-level step list that cannot be compiled."""


def _column(name: str) -> exp.Column:
    """Return a quoted column reference.

    Quoted so a column named after a reserved word, or one containing a
    space, cannot change the shape of the query.

    Args:
        name: The column name.

    Returns:
        The column expression.
    """
    return exp.column(name, quoted=True)


def _literal(value: t.Any) -> exp.Expr:  # noqa: ANN401
    """Return a bound literal for a filter value.

    Built as an expression rather than formatted into SQL text: a value
    arriving from a browser must not be able to terminate the string it sits
    in.

    Args:
        value: The value.

    Returns:
        The literal expression.
    """
    if value is None:
        return exp.null()
    if isinstance(value, bool):
        return exp.true() if value else exp.false()
    if isinstance(value, (int, float)):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


#: Each aggregate's sqlglot expression, given the column it applies to.
_AGGREGATES: dict[str, t.Callable[[exp.Expr], exp.Expr]] = {
    "sum": lambda column: exp.Sum(this=column),
    "count": lambda column: exp.Count(this=column),
    "count_distinct": lambda column: exp.Count(this=exp.Distinct(expressions=[column])),
    "avg": lambda column: exp.Avg(this=column),
    "min": lambda column: exp.Min(this=column),
    "max": lambda column: exp.Max(this=column),
}


def _comparison(
    column: exp.Expr,
    operator: str,
    value: t.Any,  # noqa: ANN401
    position: str,
) -> exp.Expr:
    """Build one filter condition.

    Args:
        column: The column expression.
        operator: The comparison.
        value: The value to compare against.
        position: Where the step sits, for error messages.

    Returns:
        The condition.

    Raises:
        TableStepError: If the operator is unknown.
    """
    if operator == "is_null":
        return exp.Is(this=column, expression=exp.null())
    if operator == "not_null":
        return exp.Not(this=exp.Is(this=column, expression=exp.null()))
    if operator == "contains":
        # `LIKE '%value%'`, with the pattern built as a literal so a value
        # containing a wildcard matches literally rather than widening the
        # filter.
        return exp.Like(
            this=column,
            expression=exp.Literal.string(f"%{value}%"),
        )

    binary: dict[str, type[exp.Binary]] = {
        "eq": exp.EQ,
        "ne": exp.NEQ,
        "gt": exp.GT,
        "gte": exp.GTE,
        "lt": exp.LT,
        "lte": exp.LTE,
    }
    kind = binary.get(operator)
    if kind is None:
        msg = f"{position}: unknown operator {operator!r}"
        raise TableStepError(msg)

    return kind(this=column, expression=_literal(value))


def _grouping(
    query: exp.Select,
    step: t.Mapping[str, t.Any],
    position: str,
) -> exp.Select:
    """Apply a grouping step, replacing the query's projection.

    Args:
        query: The query so far.
        step: The step.
        position: Where it sits, for error messages.

    Returns:
        The grouped query.

    Raises:
        TableStepError: If the step names no aggregates or an unknown one.
    """
    by = list(step.get("by") or [])
    aggregates = list(step.get("aggregates") or [])

    if not aggregates:
        msg = (
            f"{position}: 'group_by' needs at least one aggregate. Grouping "
            "without one only removes duplicates - use 'distinct' for that."
        )
        raise TableStepError(msg)

    projection: list[exp.Expr] = [_column(name) for name in by]
    for aggregate in aggregates:
        function = _AGGREGATES.get(t.cast("str", aggregate.get("fn")))
        if function is None:
            msg = f"{position}: unknown aggregate {aggregate.get('fn')!r}"
            raise TableStepError(msg)

        column = aggregate.get("column")
        # `count` with no column is `COUNT(*)`, which is the only aggregate
        # that means anything without one.
        if not column:
            if aggregate.get("fn") != "count":
                msg = (
                    f"{position}: {aggregate.get('fn')!r} needs a column; only "
                    "'count' can be applied to every row"
                )
                raise TableStepError(msg)
            inner: exp.Expr = exp.Star()
        else:
            inner = _column(t.cast("str", column))

        alias = aggregate.get("as") or f"{aggregate.get('fn')}_{column or 'all'}"
        projection.append(
            exp.alias_(function(inner), alias, quoted=True),
        )

    return (
        query.select(*projection, append=False)
        .group_by(*[_column(name) for name in by], append=False)
        .copy()
    )


def compile_table_steps(
    steps: t.Sequence[t.Mapping[str, t.Any]],
    source: str,
    *,
    dialect: str = DEFAULT_DIALECT,
    pretty: bool = True,
) -> str:
    """Compile an ordered table-level step list into a SQL query.

    Args:
        steps: The steps, in the order the user arranged them.
        source: What to select from - a table name, or a dbt `ref`.
        dialect: The SQL dialect to render for.
        pretty: Whether to format the result across lines.

    Returns:
        The query.

    Raises:
        TableStepError: If a step is malformed, or the dialect is unknown.
    """
    query = select(exp.Star()).from_(source)
    # Once an aggregation has run, later steps see its output rather than the
    # original rows, so the query so far becomes a subquery.
    grouped = False

    for index, step in enumerate(steps):
        kind = step.get("kind")
        position = f"step {index + 1}"

        if grouped and kind != "sort":
            query = select(exp.Star()).from_(query.subquery(alias="grouped"))
            grouped = False

        if kind == "filter":
            column = step.get("column")
            if not column:
                msg = f"{position}: 'filter' needs a column"
                raise TableStepError(msg)
            query = query.where(
                _comparison(
                    _column(t.cast("str", column)),
                    t.cast("str", step.get("operator")),
                    step.get("value"),
                    position,
                ),
            )

        elif kind == "group_by":
            query = _grouping(query, step, position)
            grouped = True

        elif kind == "sort":
            column = step.get("column")
            if not column:
                msg = f"{position}: 'sort' needs a column"
                raise TableStepError(msg)
            query = query.order_by(
                exp.Ordered(
                    this=_column(t.cast("str", column)),
                    desc=bool(step.get("desc")),
                ),
            )

        elif kind == "top_n":
            count = step.get("n")
            if not isinstance(count, int) or count < 1:
                msg = f"{position}: 'top_n' needs a positive row count"
                raise TableStepError(msg)
            query = query.limit(count)

        elif kind == "distinct":
            # An explicit column list deduplicates on those columns and keeps
            # only them; without one, on the whole row.
            columns = list(step.get("columns") or [])
            if columns:
                query = query.select(
                    *[_column(name) for name in columns],
                    append=False,
                )
            query = query.distinct().copy()

        else:
            msg = f"{position}: unknown step kind {kind!r}"
            raise TableStepError(msg)

    try:
        return query.sql(dialect=dialect, pretty=pretty)
    except SqlglotError as err:
        msg = f"cannot render this query for {dialect!r}: {err}"
        raise TableStepError(msg) from err


#: The table a preview's records are loaded into. Fixed rather than derived
#: from the stream so the compiled query is identical in preview and in
#: production apart from what it selects from.
PREVIEW_SOURCE = "source"


def preview_table_steps(
    records: t.Sequence[t.Mapping[str, t.Any]],
    steps: t.Sequence[t.Mapping[str, t.Any]],
) -> list[dict[str, t.Any]]:
    """Run table-level steps over previewed records, in this process.

    The rows have already been read for the preview, so grouping or sorting
    them needs no warehouse and no pipeline run - DuckDB executes the same
    compiled query in memory. This is what lets an aggregate be shown while
    someone is still deciding whether they want it.

    The query is rendered for DuckDB rather than for the destination, which is
    the one place the two can disagree. That is what the conformance cases
    exist to check.

    Args:
        records: The rows read for the preview.
        steps: The steps, in order.

    Returns:
        The shaped rows.

    Raises:
        TableStepError: If the steps do not compile, or the query cannot run.
    """
    import duckdb

    sql = compile_table_steps(
        steps,
        PREVIEW_SOURCE,
        dialect="duckdb",
        pretty=False,
    )

    if not records:
        return []

    connection = duckdb.connect()
    try:
        # Loaded as JSON rather than through pandas or arrow: it keeps nulls
        # and mixed types honest without adding a dependency that exists only
        # to move a handful of rows across process memory.
        with tempfile.NamedTemporaryFile(
            "w",
            suffix=".json",
            delete=False,
            encoding="utf-8",
        ) as handle:
            json.dump([dict(record) for record in records], handle, default=str)
            path = handle.name

        try:
            connection.execute(
                f"CREATE TABLE {PREVIEW_SOURCE} AS "  # noqa: S608
                "SELECT * FROM read_json_auto(?)",
                [path],
            )
            cursor = connection.execute(sql)
            columns = [description[0] for description in cursor.description or []]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]
        finally:
            Path(path).unlink(missing_ok=True)
    except duckdb.Error as err:
        msg = f"cannot apply these steps to the previewed rows: {err}"
        raise TableStepError(msg) from err
    finally:
        connection.close()
