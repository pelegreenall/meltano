"""The conformance table for transform steps.

One list of cases, checked two ways. Locally, `compile_steps` must produce the
stream map recorded here and `apply_steps` must produce these rows. Against a
real `meltano-map-transformer`, feeding the same records through the same
compiled stream map must produce those same rows.

That second check is the one that matters. The preview applies steps
*sequentially* to an evolving row; the compiled stream map is *declarative*,
one pass in which every expression reads the original record. They are
different evaluation models, so agreement between them is a property to be
demonstrated rather than assumed - and it is the property the shaper's whole
promise rests on, since what the preview shows is what a run is supposed to do.

This module is not collected by pytest. It is imported by the tests that are.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

#: The records every case is applied to. Chosen so one column is null in some
#: rows but not others, one is numeric, and one is text - between them enough
#: for every operator to say something.
RECORDS: list[dict[str, t.Any]] = [
    {"id": 1, "name": "Ada", "email": "ada@example.com", "score": 90, "note": None},
    {"id": 2, "name": "Bo", "email": "bo@example.com", "score": 45, "note": "vip"},
    {"id": 3, "name": "Cy", "email": "cy@example.com", "score": 72, "note": None},
]


@dataclass(frozen=True)
class Case:
    """One step list, with what both sides must produce for it."""

    name: str
    steps: list[dict[str, t.Any]]
    #: What `compile_steps` must emit.
    stream_map: dict[str, t.Any]
    #: The rows both the local preview and the real mapper must produce.
    rows: list[dict[str, t.Any]]
    #: Why this case is here, when that is not obvious from the steps.
    note: str = field(default="")


def _without(*columns: str) -> list[dict[str, t.Any]]:
    """Return the records without some columns.

    Args:
        columns: Columns to omit.

    Returns:
        The rows.
    """
    return [
        {key: value for key, value in record.items() if key not in columns}
        for record in RECORDS
    ]


CASES: list[Case] = [
    Case(
        name="drop",
        steps=[{"kind": "drop", "column": "email"}],
        stream_map={"email": None},
        rows=_without("email"),
    ),
    Case(
        name="rename",
        steps=[{"kind": "rename", "column": "name", "to": "label"}],
        stream_map={"label": "record['name']", "name": None},
        rows=[
            {**{k: v for k, v in r.items() if k != "name"}, "label": r["name"]}
            for r in RECORDS
        ],
    ),
    Case(
        name="rename-onto-itself",
        steps=[{"kind": "rename", "column": "name", "to": "name"}],
        stream_map={"name": "record['name']"},
        rows=[dict(r) for r in RECORDS],
        note="Renaming a column to its own name must not also drop it.",
    ),
    Case(
        name="cast-to-string",
        steps=[{"kind": "cast", "column": "score", "type": "string"}],
        stream_map={
            "score": (
                "(str(record['score']) if record['score'] is not None else None)"
            ),
        },
        rows=[{**r, "score": str(r["score"])} for r in RECORDS],
    ),
    Case(
        name="cast-a-null-column",
        steps=[{"kind": "cast", "column": "note", "type": "string"}],
        stream_map={
            "note": ("(str(record['note']) if record['note'] is not None else None)"),
        },
        rows=[dict(r) for r in RECORDS],
        note="A null cast to text stays null. Compiled without the guard, "
        "`str(None)` writes the literal 'None' into the destination while "
        "the preview shows a null - wrong data, and quiet about it.",
    ),
    Case(
        name="filter-eq",
        steps=[
            {"kind": "filter", "column": "name", "operator": "eq", "value": "Ada"},
        ],
        stream_map={"__filter__": "(record['name'] == 'Ada')"},
        rows=[dict(RECORDS[0])],
    ),
    Case(
        name="filter-gt",
        steps=[
            {"kind": "filter", "column": "score", "operator": "gt", "value": 50},
        ],
        stream_map={"__filter__": "(record['score'] > 50)"},
        rows=[dict(RECORDS[0]), dict(RECORDS[2])],
    ),
    Case(
        name="filter-not-null",
        steps=[
            {"kind": "filter", "column": "note", "operator": "not_null"},
        ],
        stream_map={"__filter__": "(record['note'] is not None)"},
        rows=[dict(RECORDS[1])],
        note="A null-valued column, not a missing one.",
    ),
    Case(
        name="filter-is-null",
        steps=[
            {"kind": "filter", "column": "note", "operator": "is_null"},
        ],
        stream_map={"__filter__": "(record['note'] is None)"},
        rows=[dict(RECORDS[0]), dict(RECORDS[2])],
    ),
    Case(
        name="filter-contains",
        steps=[
            {
                "kind": "filter",
                "column": "email",
                "operator": "contains",
                "value": "ada@",
            },
        ],
        stream_map={"__filter__": "('ada@' in (record['email'] or ''))"},
        rows=[dict(RECORDS[0])],
    ),
    Case(
        name="two-filters-are-and",
        steps=[
            {"kind": "filter", "column": "score", "operator": "gt", "value": 50},
            {"kind": "filter", "column": "note", "operator": "is_null"},
        ],
        stream_map={
            "__filter__": "(record['score'] > 50) and (record['note'] is None)",
        },
        rows=[dict(RECORDS[0]), dict(RECORDS[2])],
        note="A stack of filters narrows, as it does in Power Query.",
    ),
    Case(
        name="rename-twice",
        steps=[
            {"kind": "rename", "column": "name", "to": "label"},
            {"kind": "rename", "column": "label", "to": "title"},
        ],
        stream_map={
            "label": None,
            "name": None,
            "title": "record['name']",
        },
        rows=[
            {**{k: v for k, v in r.items() if k != "name"}, "title": r["name"]}
            for r in RECORDS
        ],
        note="The second rename must follow the first, not read a column "
        "the record never had.",
    ),
    Case(
        name="rename-then-cast",
        steps=[
            {"kind": "rename", "column": "score", "to": "points"},
            {"kind": "cast", "column": "points", "type": "string"},
        ],
        stream_map={
            "points": (
                "(str(record['score']) if record['score'] is not None else None)"
            ),
            "score": None,
        },
        rows=[
            {
                **{k: v for k, v in r.items() if k != "score"},
                "points": str(r["score"]),
            }
            for r in RECORDS
        ],
        note="Two steps fold into one expression; applied as two passes they "
        "would not compose.",
    ),
    Case(
        name="cast-then-filter-the-cast-column",
        steps=[
            {"kind": "cast", "column": "score", "type": "string"},
            {"kind": "filter", "column": "score", "operator": "eq", "value": "90"},
        ],
        stream_map={
            "score": (
                "(str(record['score']) if record['score'] is not None else None)"
            ),
            "__filter__": (
                "((str(record['score']) if record['score'] is not None "
                "else None) == '90')"
            ),
        },
        rows=[{**RECORDS[0], "score": "90"}],
        note="The filter must see the cast value. Reading the raw record here "
        "compares an int to a string and silently keeps nothing.",
    ),
    Case(
        name="rename-then-filter-the-new-name",
        steps=[
            {"kind": "rename", "column": "name", "to": "label"},
            {"kind": "filter", "column": "label", "operator": "eq", "value": "Ada"},
        ],
        stream_map={
            "label": "record['name']",
            "name": None,
            "__filter__": "(record['name'] == 'Ada')",
        },
        rows=[
            {
                **{k: v for k, v in RECORDS[0].items() if k != "name"},
                "label": "Ada",
            },
        ],
        note="The filtered column exists only after the rename. Reading the "
        "raw record looks for a field that is not there.",
    ),
    Case(
        name="drop-then-filter-another-column",
        steps=[
            {"kind": "drop", "column": "email"},
            {"kind": "filter", "column": "score", "operator": "gte", "value": 72},
        ],
        stream_map={
            "email": None,
            "__filter__": "(record['score'] >= 72)",
        },
        rows=[
            {k: v for k, v in r.items() if k != "email"}
            for r in (RECORDS[0], RECORDS[2])
        ],
        note="Filtering on a column that survives, while another is dropped.",
    ),
]
