"""Correlation of supervised runs with the system database's run history.

`RunManager` only knows about subprocesses *this* server started, and only for
as long as its in-memory registry and sidecar files survive. The authoritative
record of every pipeline execution - including those launched from a terminal
or by a previous server - is the `runs` table behind
:class:`~meltano.core.job.job.Job`.

The two are joined on `run_id`: the runs router generates one up front and
passes it to `meltano run --run-id`, so a supervised process and the rows it
produces already share a key. One invocation may produce *several* rows, since
`BlockParser` assigns the run ID to every ExtractLoadBlock it builds, so the
relationship is one-to-many and callers get a list per run.
"""

from __future__ import annotations

import typing as t
import uuid
from collections import defaultdict

from sqlalchemy import func, not_

from meltano.core.job import Job, State
from meltano.ui.services.run_manager import RunStatus

if t.TYPE_CHECKING:
    from collections.abc import Collection, Sequence

    from sqlalchemy import ColumnElement
    from sqlalchemy.orm import Session

#: `Job` rows are also written by `meltano state set`, which is a state edit
#: rather than a pipeline execution. The model's own docstring calls this out;
#: every query here excludes them.
#:
#: Cast because `Job.state` is a hybrid property whose comparator declares the
#: return of `==` as `Any`, which `not_` will not accept; the expression really
#: is a SQL clause.
_NOT_A_STATE_EDIT = not_(
    t.cast("ColumnElement[bool]", Job.state == State.STATE_EDIT),
)

#: States that mean the row is still in flight. `IDLE` is included because a
#: row is created before it transitions to `RUNNING`.
_IN_FLIGHT = frozenset({State.IDLE, State.RUNNING})

#: States that mean the row did not finish successfully. `DEAD` is a run whose
#: heartbeat stopped, which the UI should not present as a success.
_UNSUCCESSFUL = frozenset({State.FAIL, State.DEAD})


def recent_run_ids(session: Session, *, limit: int) -> list[str]:
    """Return the most recently started run IDs, newest first.

    Grouping happens in the database rather than over fetched rows so that the
    limit counts *runs* and not blocks: limiting rows instead would silently
    return fewer runs for pipelines with more blocks.

    Args:
        session: A system-database session.
        limit: The maximum number of runs to return.

    Returns:
        Run IDs in canonical string form, newest first.
    """
    started = func.max(Job.started_at).label("started_at")
    rows = (
        session.query(Job.run_id, started)
        .filter(_NOT_A_STATE_EDIT)
        .group_by(Job.run_id)
        .order_by(started.desc())
        .limit(limit)
        .all()
    )
    return [str(row.run_id) for row in rows if row.run_id is not None]


def _as_uuid(value: str) -> uuid.UUID | None:
    """Parse a run ID, tolerating values that cannot be one.

    `run_id` is a UUID column, and `GUID` coerces every bound parameter with
    `uuid.UUID(...)`. A caller-supplied string that is not a UUID - a path
    segment typed by hand, say - would therefore raise from inside the driver
    rather than simply matching nothing.

    Args:
        value: The candidate run ID.

    Returns:
        The parsed UUID, or None when the value cannot be one.
    """
    try:
        return uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None


def jobs_by_run_id(
    session: Session,
    run_ids: Collection[str],
) -> dict[str, list[Job]]:
    """Group the rows belonging to each of `run_ids`.

    Args:
        session: A system-database session.
        run_ids: The run IDs to fetch rows for.

    Returns:
        A mapping of run ID to its rows, oldest first within each run. Run IDs
        with no rows - and those that are not valid UUIDs - are absent rather
        than mapped to an empty list.
    """
    keys = {parsed for value in run_ids if (parsed := _as_uuid(value)) is not None}
    if not keys:
        return {}

    rows = (
        session.query(Job)
        .filter(_NOT_A_STATE_EDIT)
        .filter(Job.run_id.in_(keys))
        .order_by(Job.started_at.asc())
        .all()
    )

    grouped: dict[str, list[Job]] = defaultdict(list)
    for row in rows:
        grouped[str(row.run_id)].append(row)
    return dict(grouped)


def status_from_jobs(jobs: Sequence[Job]) -> RunStatus | None:
    """Derive a single run status from the rows one invocation produced.

    A run is only successful when every one of its blocks succeeded, so a
    failure anywhere outweighs successes elsewhere.

    Args:
        jobs: The rows sharing one run ID.

    Returns:
        The aggregate status, or None when the rows do not determine one.
    """
    if not jobs:
        return None

    states = {job.state for job in jobs}
    if states & _IN_FLIGHT:
        return RunStatus.running
    if states & _UNSUCCESSFUL:
        return RunStatus.failed
    if states == {State.SUCCESS}:
        return RunStatus.success
    return None
