"""Tests for compiling edit steps into stream maps and applying them."""

from __future__ import annotations

import pytest

from meltano.ui.services.transforms import (
    TransformError,
    apply_steps,
    compile_steps,
)
from tests.meltano.ui.transform_cases import CASES, Case
from tests.meltano.ui.transform_cases import RECORDS as CASE_RECORDS

RECORDS = [
    {"id": 1, "name": "Ada", "email": "ada@x.com", "status": "active", "spend": 120.5},
    {"id": 2, "name": "Bo", "email": "bo@x.com", "status": "churned", "spend": 0},
    {"id": 3, "name": "Cy", "email": None, "status": "active", "spend": 99.0},
]


class TestCompile:
    """Folding an ordered step list into one declarative stream map."""

    def test_drop_hides_a_column(self) -> None:
        """Meltano hides a field by mapping it to null."""
        assert compile_steps([{"kind": "drop", "column": "email"}]) == {"email": None}

    def test_rename_moves_and_hides(self) -> None:
        """A rename is a new key plus a null on the old one."""
        result = compile_steps(
            [{"kind": "rename", "column": "name", "to": "full_name"}],
        )

        assert result == {"full_name": "record['name']", "name": None}

    def test_rename_to_the_same_name_does_not_hide_it(self) -> None:
        """Otherwise the column would be renamed to itself and then dropped."""
        result = compile_steps([{"kind": "rename", "column": "name", "to": "name"}])

        assert result == {"name": "record['name']"}

    def test_cast_wraps_the_source(self) -> None:
        """A cast is a call around whatever produces the column.

        Guarded against nulls: a bare `str(record['id'])` turns a null into
        the literal text "None" in the destination.
        """
        result = compile_steps(
            [{"kind": "cast", "column": "id", "type": "string"}],
        )

        assert result == {
            "id": "(str(record['id']) if record['id'] is not None else None)",
        }

    def test_steps_compose_in_order(self) -> None:
        """Rename then cast must read from the *original* field.

        This is the whole reason the compiler folds steps rather than emitting
        one entry each: stream maps apply in a single pass, so a second entry
        keyed on the new name would read a column that does not exist yet.
        """
        result = compile_steps(
            [
                {"kind": "rename", "column": "id", "to": "customer_id"},
                {"kind": "cast", "column": "customer_id", "type": "string"},
            ],
        )

        assert result == {
            "customer_id": "(str(record['id']) if record['id'] is not None else None)",
            "id": None,
        }

    def test_filters_are_combined(self) -> None:
        """Stacked filters all have to hold, as they do in Power Query."""
        result = compile_steps(
            [
                {"kind": "filter", "column": "status", "operator": "eq", "value": "a"},
                {"kind": "filter", "column": "spend", "operator": "gt", "value": 10},
            ],
        )

        assert result["__filter__"] == (
            "(record['status'] == 'a') and (record['spend'] > 10)"
        )

    def test_a_filter_value_cannot_escape_the_expression(self) -> None:
        """Values are rendered with `repr`, not interpolated.

        The compiled expression is evaluated by the mapper at run time, so a
        value carrying a quote must not be able to close the string and
        continue with something else.
        """
        result = compile_steps(
            [
                {
                    "kind": "filter",
                    "column": "name",
                    "operator": "eq",
                    "value": "' or 1==1 or '",
                },
            ],
        )

        assert result["__filter__"] == ("(record['name'] == \"' or 1==1 or '\")")

    def test_a_column_name_cannot_escape_the_expression(self) -> None:
        """Column names reach the expression too, and get the same treatment."""
        result = compile_steps([{"kind": "drop", "column": "we're"}])

        assert result == {"we're": None}

    @pytest.mark.parametrize(
        ("step", "expected"),
        (
            ({"kind": "drop"}, "needs a column"),
            ({"kind": "rename", "column": "a"}, "needs a new name"),
            ({"kind": "cast", "column": "a", "type": "date"}, "unknown cast type"),
            ({"kind": "filter", "column": "a", "operator": "like"}, "unknown operator"),
            ({"kind": "explode", "column": "a"}, "unknown step kind"),
        ),
    )
    def test_malformed_steps_are_rejected(self, step: dict, expected: str) -> None:
        """A bad step is named by position so the UI can point at it."""
        with pytest.raises(TransformError, match=expected):
            compile_steps([step])


class TestFilterAfterDrop:
    """Filtering on something an earlier step removed."""

    def test_it_is_refused_rather_than_compiled(self) -> None:
        """There is no value left to compare.

        The alternative is an expression reading a column the stream map has
        already set to null, which would filter every record out without
        saying why.
        """
        with pytest.raises(TransformError, match="an earlier step removed"):
            compile_steps(
                [
                    {"kind": "drop", "column": "email"},
                    {
                        "kind": "filter",
                        "column": "email",
                        "operator": "not_null",
                    },
                ],
            )

    def test_the_position_is_named(self) -> None:
        """A step list is edited as a list, so the index is how it is found."""
        with pytest.raises(TransformError, match="step 2"):
            compile_steps(
                [
                    {"kind": "drop", "column": "email"},
                    {"kind": "filter", "column": "email", "operator": "is_null"},
                ],
            )


class TestApply:
    """Applying the same steps locally, for a preview."""

    def test_drop_removes_the_column(self) -> None:
        """The column is gone, not blanked."""
        rows = apply_steps(RECORDS, [{"kind": "drop", "column": "email"}])

        assert all("email" not in row for row in rows)

    def test_rename_moves_the_value(self) -> None:
        """The value follows the new name."""
        rows = apply_steps(
            RECORDS,
            [{"kind": "rename", "column": "name", "to": "full_name"}],
        )

        assert rows[0]["full_name"] == "Ada"
        assert "name" not in rows[0]

    def test_cast_converts(self) -> None:
        """A cast changes the value's type."""
        rows = apply_steps(
            RECORDS, [{"kind": "cast", "column": "id", "type": "string"}]
        )

        assert rows[0]["id"] == "1"

    def test_an_impossible_cast_is_an_error(self) -> None:
        """The run would die on this row, so the preview must not look fine.

        The compiled stream map is a bare `int(...)`, which raises inside the
        mapper and fails the whole pipeline. Showing the original value here
        would promise a run that cannot happen.
        """
        with pytest.raises(TransformError, match="not-a-number"):
            apply_steps(
                [{"id": "not-a-number"}],
                [{"kind": "cast", "column": "id", "type": "integer"}],
            )

    def test_an_impossible_cast_names_the_column_and_a_way_out(self) -> None:
        """Someone has to be able to act on it."""
        with pytest.raises(TransformError, match=r"'id'.*filter it out"):
            apply_steps(
                [{"id": "not-a-number"}],
                [{"kind": "cast", "column": "id", "type": "integer"}],
            )

    def test_casting_a_null_leaves_it_null(self) -> None:
        """A null is not an unparseable value; it stays a null.

        The compiled expression guards on this, so the preview must too -
        otherwise a nullable column reads as the text "None" after a run.
        """
        rows = apply_steps(
            [{"id": None}],
            [{"kind": "cast", "column": "id", "type": "string"}],
        )

        assert rows[0]["id"] is None

    def test_filter_removes_rows(self) -> None:
        """A filtered row is dropped, as `__filter__` drops it."""
        rows = apply_steps(
            RECORDS,
            [
                {
                    "kind": "filter",
                    "column": "status",
                    "operator": "eq",
                    "value": "active",
                }
            ],
        )

        assert [row["id"] for row in rows] == [1, 3]

    def test_filter_handles_nulls_without_raising(self) -> None:
        """Real data has holes; a comparison against one must not explode."""
        rows = apply_steps(
            RECORDS,
            [
                {
                    "kind": "filter",
                    "column": "email",
                    "operator": "contains",
                    "value": "x",
                }
            ],
        )

        assert [row["id"] for row in rows] == [1, 2]

    def test_incomparable_types_are_an_error(self) -> None:
        """Comparing a string to a number raises in Python, and in the mapper.

        Quietly dropping the row here would show a tidy filtered table for a
        run that dies the moment it reaches that record.
        """
        with pytest.raises(TransformError, match="cannot compare"):
            apply_steps(
                [{"spend": "lots"}, {"spend": 50}],
                [
                    {
                        "kind": "filter",
                        "column": "spend",
                        "operator": "gt",
                        "value": 10,
                    },
                ],
            )

    def test_steps_apply_in_order(self) -> None:
        """Renaming then filtering on the new name works, as a user expects."""
        rows = apply_steps(
            RECORDS,
            [
                {"kind": "rename", "column": "status", "to": "state"},
                {
                    "kind": "filter",
                    "column": "state",
                    "operator": "eq",
                    "value": "active",
                },
            ],
        )

        assert [row["id"] for row in rows] == [1, 3]
        assert "status" not in rows[0]

    def test_a_malformed_step_is_rejected_before_any_row(self) -> None:
        """Reported once, not once per row."""
        with pytest.raises(TransformError):
            apply_steps(RECORDS, [{"kind": "nonsense", "column": "id"}])

    def test_the_original_records_are_not_mutated(self) -> None:
        """A preview is re-run constantly; it must not corrupt its input."""
        before = [dict(record) for record in RECORDS]

        apply_steps(RECORDS, [{"kind": "drop", "column": "email"}])

        assert before == RECORDS


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[case.name for case in CASES],
)
class TestConformance:
    """The table both the compiler and the preview must satisfy.

    Kept as data rather than as prose so the same cases can be replayed
    through a real mapper, and so a reimplementation of this compiler has
    something to be checked against rather than a description to interpret.
    """

    def test_the_compiled_stream_map_is_exact(self, case: Case) -> None:
        """What gets written to `meltano.yml`, pinned character for character.

        Not a loose check: this text is evaluated by someone else's code, so
        an expression that merely looks right is not evidence.
        """
        assert compile_steps(case.steps) == case.stream_map

    def test_the_preview_produces_the_recorded_rows(self, case: Case) -> None:
        """What the shaper shows, for the same steps."""
        assert apply_steps(CASE_RECORDS, case.steps) == case.rows

    def test_the_records_are_not_mutated(self, case: Case) -> None:
        """A preview is run repeatedly while someone edits.

        Applying steps to the caller's rows rather than to copies would make
        the second preview of an unchanged step list disagree with the first.
        """
        before = [dict(record) for record in CASE_RECORDS]

        apply_steps(CASE_RECORDS, case.steps)

        assert before == CASE_RECORDS
