"""Turning a list of edit steps into Meltano stream maps, and previewing them.

The model is Power Query's: an ordered list of steps over one stream, each one
small and inspectable, with the result visible after every change.

Steps are *structured* - a kind, a column, maybe an operator and a value -
rather than expressions typed by the user. That matters twice over. It lets the
same step list be applied locally to preview rows, and it means this server
never evaluates a string the user wrote. Meltano's stream maps are expressions,
so the compiler below writes them; nothing here reads them back.

What this cannot express is anything needing more than one record at a time:
joins, aggregates, pivots. A stream map sees one record, so those belong in dbt
after the data has landed. Presenting them here would be a lie.
"""

from __future__ import annotations

import typing as t

#: Kinds of step, in the vocabulary the UI presents.
StepKind = t.Literal["drop", "rename", "cast", "filter"]

#: Comparisons a filter step can make. Deliberately closed: each maps to one
#: Python expression the compiler writes and one callable applied locally.
Operator = t.Literal[
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

#: Types a cast step can produce, named as Singer names them.
CastType = t.Literal["integer", "number", "string", "boolean"]


class TransformError(Exception):
    """A step list that cannot be compiled or applied."""


def _literal(value: t.Any) -> str:  # noqa: ANN401
    """Render a Python literal for an expression.

    `repr` is used rather than string formatting so that quotes, newlines and
    non-ASCII in a filter value cannot terminate the expression early.

    Args:
        value: The value to render.

    Returns:
        Its literal form.
    """
    return repr(value)


#: Each operator's expression template, given a source expression and a value.
_COMPARISONS: dict[str, str] = {
    "eq": "{src} == {val}",
    "ne": "{src} != {val}",
    "gt": "{src} > {val}",
    "gte": "{src} >= {val}",
    "lt": "{src} < {val}",
    "lte": "{src} <= {val}",
    "contains": "{val} in ({src} or '')",
    "is_null": "{src} is None",
    "not_null": "{src} is not None",
}

#: The Python callable each cast maps to, in expressions and locally.
_CASTS: dict[str, str] = {
    "integer": "int",
    "number": "float",
    "string": "str",
    "boolean": "bool",
}


def _source(column: str) -> str:
    """Return the expression reading a column straight off the record.

    Args:
        column: The column name.

    Returns:
        A stream-map expression.
    """
    return f"record[{_literal(column)}]"


def compile_steps(steps: t.Sequence[t.Mapping[str, t.Any]]) -> dict[str, t.Any]:
    """Compile an ordered step list into one stream map.

    Meltano's stream maps are declarative: a mapping of output column to the
    expression producing it, applied in one pass. An ordered list of steps is
    not, so the steps are folded into that mapping here - renaming a column and
    then casting it yields a single expression over the original field, rather
    than two passes that would not compose.

    Args:
        steps: The steps, in the order the user arranged them.

    Returns:
        A stream map: column names to expressions, plus `__filter__` when any
        filter step is present.

    Raises:
        TransformError: If a step is malformed or names an unknown operator.
    """
    # Output column -> the expression producing it. A column absent from here
    # passes through untouched, which is what stream maps do by default.
    expressions: dict[str, str | None] = {}
    filters: list[str] = []

    for index, step in enumerate(steps):
        kind = step.get("kind")
        column = step.get("column")
        position = f"step {index + 1}"

        if kind in {"drop", "rename", "cast"} and not column:
            msg = f"{position}: {kind!r} needs a column"
            raise TransformError(msg)

        current = expressions.get(t.cast("str", column), _source(t.cast("str", column)))

        if kind == "drop":
            expressions[t.cast("str", column)] = None

        elif kind == "rename":
            target = step.get("to")
            if not target:
                msg = f"{position}: 'rename' needs a new name"
                raise TransformError(msg)
            expressions[target] = current
            # Only hide the original if the rename actually moved it.
            if target != column:
                expressions[t.cast("str", column)] = None

        elif kind == "cast":
            cast = _CASTS.get(t.cast("str", step.get("type")))
            if cast is None:
                msg = f"{position}: unknown cast type {step.get('type')!r}"
                raise TransformError(msg)
            # Null-safe, and parenthesised so the whole conditional can be
            # embedded in a filter without `else` swallowing the comparison.
            # Without the guard, `str(None)` writes the literal text "None"
            # into the destination while the preview shows a null - wrong
            # data, and quiet about it.
            expressions[t.cast("str", column)] = (
                f"({cast}({current}) if {current} is not None else None)"
            )

        elif kind == "filter":
            filters.append(_compile_filter(step, position, expressions))

        else:
            msg = f"{position}: unknown step kind {kind!r}"
            raise TransformError(msg)

    stream_map: dict[str, t.Any] = dict(expressions)
    if filters:
        # Every filter must hold, which is how a stack of Power Query filters
        # reads too.
        stream_map["__filter__"] = " and ".join(f"({f})" for f in filters)
    return stream_map


def _compile_filter(
    step: t.Mapping[str, t.Any],
    position: str,
    expressions: t.Mapping[str, str | None],
) -> str:
    """Compile one filter step into a boolean expression.

    The filter reads the column as the steps before it left it, not as it
    arrived. A preview applies steps in order, so a filter written after a
    cast sees the cast value and one written after a rename sees the new name;
    a filter compiled against the raw record would do neither. Both failures
    are silent - comparing an int to a string simply keeps nothing, and
    reading a renamed column looks for a field the record does not have.

    Args:
        step: The step.
        position: Where it sits, for error messages.
        expressions: The columns rewritten so far, as folded by the caller.

    Returns:
        The expression.

    Raises:
        TransformError: If the step is malformed, or filters on a column an
            earlier step removed.
    """
    column = step.get("column")
    operator = step.get("operator")

    if not column:
        msg = f"{position}: 'filter' needs a column"
        raise TransformError(msg)

    template = _COMPARISONS.get(t.cast("str", operator))
    if template is None:
        msg = f"{position}: unknown operator {operator!r}"
        raise TransformError(msg)

    source = expressions.get(t.cast("str", column), _source(t.cast("str", column)))
    if source is None:
        msg = f"{position}: cannot filter on {column!r}, which an earlier step removed"
        raise TransformError(msg)

    return template.format(
        src=source,
        val=_literal(step.get("value")),
    )


#: How each cast reads in a message, so an error names what was asked for
#: rather than the Python callable that implements it.
_CAST_NAMES: dict[str, str] = {
    "int": "a whole number",
    "float": "a decimal number",
    "str": "text",
    "bool": "true or false",
}


def _cast_value(value: t.Any, cast: str, column: str) -> t.Any:  # noqa: ANN401
    """Apply one cast locally, exactly as the compiled expression would.

    A value that cannot convert is an error rather than a value left alone.
    The stream map compiles to a bare `int(...)`, which raises inside the
    mapper and fails the whole run; a preview that quietly showed the
    original value would promise a run that cannot happen.

    Args:
        value: The value to convert.
        cast: The name of the Python callable.
        column: The column being cast, for the error message.

    Returns:
        The converted value, or None for a null.

    Raises:
        TransformError: If the value cannot convert.
    """
    if value is None:
        return None
    functions: dict[str, t.Callable[[t.Any], t.Any]] = {
        "int": int,
        "float": float,
        "str": str,
        "bool": bool,
    }
    try:
        return functions[cast](value)
    except (TypeError, ValueError) as err:
        msg = (
            f"cannot read {value!r} in column {column!r} as "
            f"{_CAST_NAMES[cast]}. A run would fail on this row; filter it "
            "out or drop the cast."
        )
        raise TransformError(msg) from err


def _matches(
    value: t.Any,  # noqa: ANN401
    operator: str,
    expected: t.Any,  # noqa: ANN401
    column: str,
) -> bool:
    """Evaluate one comparison locally, exactly as the expression would.

    Mirrors `_COMPARISONS`, including where it fails. A comparison Python
    cannot make raises here rather than quietly dropping the row, because the
    compiled expression raises inside the mapper and fails the whole run -
    and a preview showing a tidy filtered table would be describing a run
    that cannot happen.

    Args:
        value: The record's value.
        operator: The comparison.
        expected: The step's value.
        column: The column being compared, for the error message.

    Returns:
        Whether the record passes.

    Raises:
        TransformError: If the two values cannot be compared.
    """
    if operator == "is_null":
        return value is None
    if operator == "not_null":
        return value is not None
    if operator == "eq":
        return bool(value == expected)
    if operator == "ne":
        return bool(value != expected)

    comparisons: dict[str, t.Callable[[t.Any, t.Any], bool]] = {
        "contains": lambda a, b: b in (a or ""),
        "gt": lambda a, b: bool(a > b),
        "gte": lambda a, b: bool(a >= b),
        "lt": lambda a, b: bool(a < b),
        "lte": lambda a, b: bool(a <= b),
    }
    compare = comparisons.get(operator)
    if compare is None:
        return False

    try:
        return compare(value, expected)
    except TypeError as err:
        msg = (
            f"cannot compare {value!r} in column {column!r} with "
            f"{expected!r}. A run would fail on this row; filter it out or "
            "cast the column first."
        )
        raise TransformError(msg) from err


def apply_steps(
    records: t.Sequence[t.Mapping[str, t.Any]],
    steps: t.Sequence[t.Mapping[str, t.Any]],
) -> list[dict[str, t.Any]]:
    """Apply a step list to records, as the compiled stream map would.

    Used to preview an edit before it is saved. Filtered-out records are
    removed rather than blanked, which is what a stream map's `__filter__`
    does.

    Args:
        records: The rows to transform.
        steps: The steps, in order.

    Returns:
        The transformed rows.

    Raises:
        TransformError: If a step is malformed.
    """
    # Validated up front so a malformed step is reported once, not per row.
    compile_steps(steps)

    out: list[dict[str, t.Any]] = []
    for record in records:
        row: dict[str, t.Any] | None = dict(record)
        for step in steps:
            if row is None:
                break
            row = _apply_one(row, step)
        if row is not None:
            out.append(row)
    return out


def _apply_one(
    row: dict[str, t.Any],
    step: t.Mapping[str, t.Any],
) -> dict[str, t.Any] | None:
    """Apply one step to one row.

    Args:
        row: The row so far.
        step: The step to apply.

    Returns:
        The updated row, or None when a filter removed it.
    """
    kind = step.get("kind")
    column = t.cast("str", step.get("column"))

    if kind == "drop":
        row.pop(column, None)
    elif kind == "rename":
        target = t.cast("str", step.get("to"))
        if column in row:
            row[target] = row.pop(column)
        elif target not in row:
            # Renaming something the record never had should leave a hole
            # rather than inventing a value.
            row[target] = None
    elif kind == "cast":
        if column in row:
            row[column] = _cast_value(
                row[column],
                _CASTS[t.cast("str", step.get("type"))],
                column,
            )
    elif kind == "filter" and not _matches(
        row.get(column),
        t.cast("str", step.get("operator")),
        step.get("value"),
        column,
    ):
        return None

    return row
