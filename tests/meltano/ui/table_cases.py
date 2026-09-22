"""The conformance table for table-level shaping steps.

One list of cases, checked against two engines. DuckDB runs them in-process
over previewed rows; Postgres runs them over the same rows loaded into a real
table. Both must produce the same result.

That agreement is the whole promise of the table-level shaper, and nothing
makes it true by construction. The preview and the production model are the
same steps rendered for different dialects, and dialects disagree about the
things that are easiest to overlook - null ordering, what `SUM` of nothing
returns, how `DISTINCT` treats nulls. A case here is a claim that they do not
disagree about this one.

This module is not collected by pytest. It is imported by the tests that are.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

#: The rows every case runs over. One null amount, one customer whose only
#: paid order is that null, and three regions - between them enough to make
#: aggregation, null handling and ordering say something.
ORDERS: list[dict[str, t.Any]] = [
    {"id": 1, "customer": "ada", "amount": 100.0, "status": "paid", "region": "gb"},
    {"id": 2, "customer": "ada", "amount": 50.0, "status": "paid", "region": "gb"},
    {"id": 3, "customer": "bo", "amount": 75.0, "status": "refunded", "region": "us"},
    {"id": 4, "customer": "bo", "amount": 25.0, "status": "paid", "region": "us"},
    {"id": 5, "customer": "cy", "amount": 200.0, "status": "pending", "region": "gb"},
    {"id": 6, "customer": "cy", "amount": None, "status": "paid", "region": "gb"},
]


@dataclass(frozen=True)
class TableCase:
    """One table-level step list, with the rows both engines must produce."""

    name: str
    steps: list[dict[str, t.Any]]
    #: The expected result. Compared order-insensitively unless `ordered`.
    rows: list[dict[str, t.Any]]
    #: True when the steps end in a sort, so row order is part of the claim.
    ordered: bool = field(default=False)
    #: Why this case is here, when that is not obvious from the steps.
    note: str = field(default="")


TABLE_CASES: list[TableCase] = [
    TableCase(
        name="filter-only",
        steps=[
            {"kind": "filter", "column": "status", "operator": "eq", "value": "paid"},
        ],
        rows=[row for row in ORDERS if row["status"] == "paid"],
    ),
    TableCase(
        name="group-by-sum-and-count",
        steps=[
            {
                "kind": "group_by",
                "by": ["customer"],
                "aggregates": [
                    {"fn": "sum", "column": "amount", "as": "revenue"},
                    {"fn": "count", "as": "orders"},
                ],
            },
        ],
        rows=[
            {"customer": "ada", "revenue": 150.0, "orders": 2},
            {"customer": "bo", "revenue": 100.0, "orders": 2},
            {"customer": "cy", "revenue": 200.0, "orders": 2},
        ],
        note="`SUM` skips the null amount rather than poisoning the total, "
        "while `COUNT(*)` still counts the row it sits on.",
    ),
    TableCase(
        name="filter-before-grouping-restricts-rows",
        steps=[
            {"kind": "filter", "column": "status", "operator": "eq", "value": "paid"},
            {
                "kind": "group_by",
                "by": ["customer"],
                "aggregates": [
                    {"fn": "sum", "column": "amount", "as": "revenue"},
                    {"fn": "count", "as": "orders"},
                ],
            },
        ],
        rows=[
            {"customer": "ada", "revenue": 150.0, "orders": 2},
            {"customer": "bo", "revenue": 25.0, "orders": 1},
            {"customer": "cy", "revenue": None, "orders": 1},
        ],
        note="cy's only paid order has a null amount, so summing nothing must "
        "give null rather than zero - a difference engines have opinions about.",
    ),
    TableCase(
        name="filter-after-grouping-restricts-groups",
        steps=[
            {
                "kind": "group_by",
                "by": ["customer"],
                "aggregates": [{"fn": "sum", "column": "amount", "as": "revenue"}],
            },
            {"kind": "filter", "column": "revenue", "operator": "gt", "value": 100},
        ],
        rows=[
            {"customer": "ada", "revenue": 150.0},
            {"customer": "cy", "revenue": 200.0},
        ],
        note="The same filter kind, written after the grouping, means "
        "something else entirely. This is the case the subquery exists for.",
    ),
    TableCase(
        name="count-distinct",
        steps=[
            {
                "kind": "group_by",
                "by": ["region"],
                "aggregates": [
                    {"fn": "count_distinct", "column": "customer", "as": "customers"},
                ],
            },
        ],
        rows=[
            {"region": "gb", "customers": 2},
            {"region": "us", "customers": 1},
        ],
    ),
    TableCase(
        name="min-max-avg",
        steps=[
            {
                "kind": "group_by",
                "by": ["region"],
                "aggregates": [
                    {"fn": "min", "column": "amount", "as": "smallest"},
                    {"fn": "max", "column": "amount", "as": "largest"},
                    {"fn": "avg", "column": "amount", "as": "mean"},
                ],
            },
        ],
        rows=[
            {
                "region": "gb",
                "smallest": 50.0,
                "largest": 200.0,
                "mean": 116.66666666666667,
            },
            {"region": "us", "smallest": 25.0, "largest": 75.0, "mean": 50.0},
        ],
        note="`AVG` over gb divides by the three non-null amounts, not by the "
        "four rows.",
    ),
    TableCase(
        name="sort-descending",
        steps=[{"kind": "sort", "column": "amount", "desc": True}],
        rows=[
            ORDERS[4],
            ORDERS[0],
            ORDERS[2],
            ORDERS[1],
            ORDERS[3],
            ORDERS[5],
        ],
        ordered=True,
        note="Where the null amount lands is the classic dialect "
        "disagreement: Postgres puts nulls first on a descending sort by "
        "default, DuckDB puts them last.",
    ),
    TableCase(
        name="sort-ascending",
        steps=[{"kind": "sort", "column": "amount", "desc": False}],
        rows=[
            ORDERS[3],
            ORDERS[1],
            ORDERS[2],
            ORDERS[0],
            ORDERS[4],
            ORDERS[5],
        ],
        ordered=True,
    ),
    TableCase(
        name="sort-then-take",
        steps=[
            {"kind": "sort", "column": "amount", "desc": True},
            {"kind": "top_n", "n": 3},
        ],
        rows=[ORDERS[4], ORDERS[0], ORDERS[2]],
        ordered=True,
        note="Top N is only meaningful after a sort; without one the rows it "
        "keeps are whichever the engine happened to produce.",
    ),
    TableCase(
        name="distinct-on-columns",
        steps=[{"kind": "distinct", "columns": ["customer", "region"]}],
        rows=[
            {"customer": "ada", "region": "gb"},
            {"customer": "bo", "region": "us"},
            {"customer": "cy", "region": "gb"},
        ],
    ),
    TableCase(
        name="group-sort-and-take",
        steps=[
            {
                "kind": "filter",
                "column": "status",
                "operator": "ne",
                "value": "refunded",
            },
            {
                "kind": "group_by",
                "by": ["customer"],
                "aggregates": [{"fn": "sum", "column": "amount", "as": "revenue"}],
            },
            {"kind": "filter", "column": "revenue", "operator": "not_null"},
            {"kind": "sort", "column": "revenue", "desc": True},
            {"kind": "top_n", "n": 2},
        ],
        rows=[
            {"customer": "cy", "revenue": 200.0},
            {"customer": "ada", "revenue": 150.0},
        ],
        ordered=True,
        note="The whole vocabulary in one list, with filters on both sides of "
        "the grouping.",
    ),
]
