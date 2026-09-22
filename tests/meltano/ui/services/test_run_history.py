"""Tests for correlating supervised runs with the system database."""

from __future__ import annotations

import typing as t
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from meltano.core.job import Job, State
from meltano.ui.services import run_history
from meltano.ui.services.run_manager import RunStatus

if t.TYPE_CHECKING:
    from sqlalchemy.orm import Session

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_job(
    session: Session,
    *,
    job_name: str,
    run_id: uuid.UUID,
    state: State = State.SUCCESS,
    minutes: int = 0,
) -> Job:
    """Persist one `Job` row.

    Args:
        session: The session to write through.
        job_name: The row's state ID.
        run_id: The run the row belongs to.
        state: The row's state.
        minutes: Offset from `BASE` for the start time.

    Returns:
        The saved row.
    """
    job = Job(
        job_name=job_name,
        run_id=run_id,
        state=state,
        started_at=BASE + timedelta(minutes=minutes),
        ended_at=BASE + timedelta(minutes=minutes + 1),
    )
    job.save(session)
    return job


class TestStatusFromJobs:
    """Aggregating many block rows into one run status."""

    def test_no_rows_is_undetermined(self) -> None:
        """A run with no rows yields no status rather than a false success."""
        assert run_history.status_from_jobs([]) is None

    def test_all_successful_is_success(self, session: Session) -> None:
        """Every block succeeding means the run succeeded."""
        run_id = uuid.uuid4()
        jobs = [
            make_job(session, job_name="a", run_id=run_id),
            make_job(session, job_name="b", run_id=run_id),
        ]
        assert run_history.status_from_jobs(jobs) is RunStatus.success

    def test_one_failure_outweighs_successes(self, session: Session) -> None:
        """A run is not a success when any of its blocks failed."""
        run_id = uuid.uuid4()
        jobs = [
            make_job(session, job_name="a", run_id=run_id),
            make_job(session, job_name="b", run_id=run_id, state=State.FAIL),
        ]
        assert run_history.status_from_jobs(jobs) is RunStatus.failed

    def test_a_dead_row_is_not_a_success(self, session: Session) -> None:
        """A run whose heartbeat stopped is reported as failed, not succeeded."""
        run_id = uuid.uuid4()
        jobs = [make_job(session, job_name="a", run_id=run_id, state=State.DEAD)]
        assert run_history.status_from_jobs(jobs) is RunStatus.failed

    @pytest.mark.parametrize("state", (State.RUNNING, State.IDLE))
    def test_in_flight_rows_report_running(
        self,
        session: Session,
        state: State,
    ) -> None:
        """A block still in flight keeps the whole run in flight.

        `IDLE` counts because a row exists briefly before it transitions.
        """
        run_id = uuid.uuid4()
        jobs = [
            make_job(session, job_name="a", run_id=run_id),
            make_job(session, job_name="b", run_id=run_id, state=state),
        ]
        assert run_history.status_from_jobs(jobs) is RunStatus.running


class TestJobsByRunId:
    """Grouping rows by the run that produced them."""

    def test_groups_every_block_of_one_run(self, session: Session) -> None:
        """One invocation's several rows come back under a single key."""
        run_id = uuid.uuid4()
        make_job(session, job_name="a", run_id=run_id, minutes=1)
        make_job(session, job_name="b", run_id=run_id, minutes=0)

        grouped = run_history.jobs_by_run_id(session, [str(run_id)])

        # Ordered oldest first within the run, so the UI can show them in the
        # sequence they executed.
        assert [job.job_name for job in grouped[str(run_id)]] == ["b", "a"]

    def test_separates_distinct_runs(self, session: Session) -> None:
        """Rows are not pooled across run IDs."""
        first, second = uuid.uuid4(), uuid.uuid4()
        make_job(session, job_name="a", run_id=first)
        make_job(session, job_name="b", run_id=second)

        grouped = run_history.jobs_by_run_id(session, [str(first), str(second)])

        assert set(grouped) == {str(first), str(second)}

    def test_state_edits_are_excluded(self, session: Session) -> None:
        """`meltano state set` writes a row that is not a pipeline run."""
        run_id = uuid.uuid4()
        make_job(session, job_name="a", run_id=run_id, state=State.STATE_EDIT)

        assert run_history.jobs_by_run_id(session, [str(run_id)]) == {}

    def test_unknown_run_id_is_absent(self, session: Session) -> None:
        """A run with no rows is missing rather than mapped to an empty list."""
        assert run_history.jobs_by_run_id(session, [str(uuid.uuid4())]) == {}

    def test_a_non_uuid_is_not_an_error(self, session: Session) -> None:
        """A hand-typed path segment matches nothing instead of raising.

        `run_id` is a UUID column and the type decorator coerces every bound
        parameter, so an unparseable value would otherwise raise from inside
        the driver.
        """
        assert run_history.jobs_by_run_id(session, ["not-a-uuid"]) == {}

    def test_no_run_ids_short_circuits(self, session: Session) -> None:
        """An empty request does not query at all."""
        assert run_history.jobs_by_run_id(session, []) == {}


class TestRecentRunIds:
    """Selecting the most recent runs."""

    def test_orders_newest_first(self, session: Session) -> None:
        """Runs come back in reverse chronological order."""
        older, newer = uuid.uuid4(), uuid.uuid4()
        make_job(session, job_name="a", run_id=older, minutes=0)
        make_job(session, job_name="b", run_id=newer, minutes=10)

        assert run_history.recent_run_ids(session, limit=10)[:2] == [
            str(newer),
            str(older),
        ]

    def test_limit_counts_runs_not_rows(self, session: Session) -> None:
        """A multi-block run consumes one slot, not one per block.

        Limiting rows instead would silently return fewer runs for pipelines
        with more blocks.
        """
        wide, other = uuid.uuid4(), uuid.uuid4()
        for index in range(5):
            make_job(session, job_name=f"block{index}", run_id=wide, minutes=10)
        make_job(session, job_name="a", run_id=other, minutes=0)

        assert run_history.recent_run_ids(session, limit=2) == [
            str(wide),
            str(other),
        ]

    def test_state_edits_are_excluded(self, session: Session) -> None:
        """A state edit never appears as a run."""
        run_id = uuid.uuid4()
        make_job(session, job_name="a", run_id=run_id, state=State.STATE_EDIT)

        assert str(run_id) not in run_history.recent_run_ids(session, limit=50)
