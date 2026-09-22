"""Tests for compiling table-level steps into SQL and running them."""

from __future__ import annotations

import os
import typing as t

import pytest

from meltano.ui.services.tables import (
    TableStepError,
    compile_table_steps,
    preview_table_steps,
)
from tests.meltano.ui.table_cases import ORDERS, TABLE_CASES, TableCase


def canonical(rows: t.Sequence[t.Mapping[str, t.Any]]) -> list[t.Any]:
    """Order rows so an unordered result can be compared.

    `GROUP BY` promises nothing about row order, so a case that does not end
    in a sort is a claim about which rows come back, not about their
    sequence.

    Args:
        rows: The rows.

    Returns:
        Something comparable.
    """
    return sorted(
        [dict(sorted(row.items())) for row in rows],
        key=lambda row: repr(row),
    )


def compare(
    produced: t.Sequence[t.Mapping[str, t.Any]],
    case: TableCase,
) -> None:
    """Assert a result matches a case, respecting whether order matters.

    Args:
        produced: What the engine returned.
        case: The case.
    """
    if case.ordered:
        assert [dict(row) for row in produced] == case.rows
    else:
        assert canonical(produced) == canonical(case.rows)


class TestCompiledSql:
    """The shape of the SQL, where that shape is the point.

    Only the structural claims are pinned here. Asserting the exact text of
    every case would mostly be testing sqlglot's formatting, which is its
    business rather than this compiler's.
    """

    def test_a_filter_before_grouping_is_a_where(self) -> None:
        """It restricts the rows going in."""
        sql = compile_table_steps(
            [
                {
                    "kind": "filter",
                    "column": "status",
                    "operator": "eq",
                    "value": "paid",
                },
                {
                    "kind": "group_by",
                    "by": ["customer"],
                    "aggregates": [{"fn": "sum", "column": "amount", "as": "revenue"}],
                },
            ],
            "orders",
            pretty=False,
        )

        assert "WHERE" in sql
        assert "GROUP BY" in sql
        # One query, not a subquery: the filter had no aggregate to run after.
        assert sql.count("SELECT") == 1

    def test_a_filter_after_grouping_wraps_the_query(self) -> None:
        """It restricts the groups coming out.

        Expressed as a subquery rather than `HAVING`, so that the step list
        reads the same way whatever follows it.
        """
        sql = compile_table_steps(
            [
                {
                    "kind": "group_by",
                    "by": ["customer"],
                    "aggregates": [{"fn": "sum", "column": "amount", "as": "revenue"}],
                },
                {"kind": "filter", "column": "revenue", "operator": "gt", "value": 100},
            ],
            "orders",
            pretty=False,
        )

        assert sql.count("SELECT") == 2
        assert "GROUP BY" in sql

    def test_identifiers_are_quoted(self) -> None:
        """A column named after a reserved word must not change the query."""
        sql = compile_table_steps(
            [{"kind": "sort", "column": "order", "desc": False}],
            "orders",
            pretty=False,
        )

        assert '"order"' in sql

    def test_a_filter_value_cannot_escape_its_literal(self) -> None:
        """Values are bound as expressions, never formatted into text.

        This is the reason the compiler builds an AST rather than strings: a
        value arriving from a browser must not be able to end the literal it
        sits in and continue as SQL.
        """
        import sqlglot

        hostile = "paid'; DROP TABLE orders; --"
        sql = compile_table_steps(
            [
                {
                    "kind": "filter",
                    "column": "status",
                    "operator": "eq",
                    "value": hostile,
                },
            ],
            "orders",
            pretty=False,
        )

        # Parsed back rather than matched as text: what matters is that the
        # database will read one statement, and that the value survives as a
        # value rather than becoming syntax.
        statements = sqlglot.parse(sql, dialect="postgres")

        assert len(statements) == 1
        assert statements[0].find(sqlglot.exp.Literal).this == hostile

    def test_the_same_steps_render_for_another_dialect(self) -> None:
        """The preview and the production model are one step list, twice."""
        steps: list[dict[str, t.Any]] = [
            {"kind": "sort", "column": "amount", "desc": True},
            {"kind": "top_n", "n": 3},
        ]

        postgres = compile_table_steps(steps, "orders", dialect="postgres")
        duckdb = compile_table_steps(steps, "orders", dialect="duckdb")

        assert "LIMIT 3" in postgres
        assert "LIMIT 3" in duckdb


class TestRefusals:
    """Step lists that cannot mean anything."""

    def test_grouping_without_an_aggregate_is_refused(self) -> None:
        """It would silently be a `DISTINCT`, which is a different step."""
        with pytest.raises(TableStepError, match="use 'distinct'"):
            compile_table_steps(
                [{"kind": "group_by", "by": ["customer"], "aggregates": []}],
                "orders",
            )

    def test_an_aggregate_needing_a_column_is_refused_without_one(self) -> None:
        """`SUM()` of nothing is not a question with an answer."""
        with pytest.raises(TableStepError, match="needs a column"):
            compile_table_steps(
                [
                    {
                        "kind": "group_by",
                        "by": ["customer"],
                        "aggregates": [{"fn": "sum", "as": "revenue"}],
                    },
                ],
                "orders",
            )

    def test_an_unknown_step_kind_names_its_position(self) -> None:
        """A step list is edited as a list, so the index is how it is found."""
        with pytest.raises(TableStepError, match="step 2"):
            compile_table_steps(
                [
                    {"kind": "sort", "column": "amount"},
                    {"kind": "pivot", "column": "amount"},
                ],
                "orders",
            )

    def test_top_n_needs_a_positive_count(self) -> None:
        """Zero rows is not a preview, it is a mistake."""
        with pytest.raises(TableStepError, match="positive row count"):
            compile_table_steps([{"kind": "top_n", "n": 0}], "orders")


@pytest.mark.parametrize(
    "case",
    TABLE_CASES,
    ids=[case.name for case in TABLE_CASES],
)
class TestDuckDbConformance:
    """Every case, run in-process over the previewed rows.

    This is the engine the shaper previews with, so these are the rows a
    person actually sees while editing.
    """

    def test_the_previewed_rows_are_the_recorded_ones(
        self,
        case: TableCase,
    ) -> None:
        """What the shaper shows, for these steps."""
        compare(preview_table_steps(ORDERS, case.steps), case)

    def test_the_records_are_not_mutated(self, case: TableCase) -> None:
        """A preview runs repeatedly while someone edits."""
        before = [dict(row) for row in ORDERS]

        preview_table_steps(ORDERS, case.steps)

        assert before == ORDERS


class TestPreviewEdges:
    """Previewing when there is little to preview."""

    def test_no_records_is_an_empty_result(self) -> None:
        """A tap that produced nothing is not a failure."""
        assert preview_table_steps([], [{"kind": "top_n", "n": 5}]) == []

    def test_no_steps_returns_the_rows_unchanged(self) -> None:
        """An empty step list is the identity, not an error."""
        assert canonical(preview_table_steps(ORDERS, [])) == canonical(ORDERS)


#: A DSN to run the conformance cases against a real Postgres. Absent, those
#: tests skip: the suite must not require a database to be standing.
_POSTGRES_ENV = "MELTANO_UI_TEST_POSTGRES"


@pytest.fixture(scope="module")
def postgres_orders() -> t.Iterator[t.Any]:
    """Load the case records into a real Postgres table.

    Skips unless `MELTANO_UI_TEST_POSTGRES` names a database. This is the
    other half of the conformance claim - DuckDB previews the steps, and
    something like this runs them for real - so it is worth having, and worth
    not requiring.

    Yields:
        A connection with an `orders` table holding the case records.
    """
    dsn = os.environ.get(_POSTGRES_ENV)
    if not dsn:
        pytest.skip(f"set {_POSTGRES_ENV} to a DSN to run these")

    psycopg = pytest.importorskip("psycopg")

    with psycopg.connect(dsn) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE IF EXISTS orders")
            cursor.execute(
                "CREATE TABLE orders ("
                "id integer, customer text, amount double precision, "
                "status text, region text)",
            )
            cursor.executemany(
                "INSERT INTO orders VALUES (%(id)s, %(customer)s, "
                "%(amount)s, %(status)s, %(region)s)",
                ORDERS,
            )
        connection.commit()
        try:
            yield connection
        finally:
            with connection.cursor() as cursor:
                cursor.execute("DROP TABLE IF EXISTS orders")
            connection.commit()


@pytest.mark.slow
@pytest.mark.parametrize(
    "case",
    TABLE_CASES,
    ids=[case.name for case in TABLE_CASES],
)
class TestPostgresConformance:
    """Every case again, against the engine that will really run it.

    The preview and the production model are the same steps rendered for
    different dialects. Nothing makes those agree by construction, and the
    places they disagree - null ordering, what an aggregate over nothing
    returns - are exactly the ones a person would not think to check.
    """

    def test_postgres_produces_the_same_rows_as_the_preview(
        self,
        case: TableCase,
        postgres_orders: t.Any,
    ) -> None:
        """The claim the whole table-level shaper rests on."""
        sql = compile_table_steps(case.steps, "orders", dialect="postgres")

        with postgres_orders.cursor() as cursor:
            cursor.execute(sql)
            columns = [description[0] for description in cursor.description]
            produced = [
                dict(zip(columns, row, strict=True)) for row in cursor.fetchall()
            ]

        compare(produced, case)
