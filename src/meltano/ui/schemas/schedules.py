"""Models for the schedule endpoints.

Meltano *declares* schedules; it does not run them. An orchestrator - Airflow,
Dagster, or plain cron - is what reads these declarations and triggers them.
The fields here are shaped so the UI can say that plainly rather than implying
a scheduler is running inside this server.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field


class ScheduleInfo(BaseModel):
    """A schedule declared in the project's `meltano.yml`."""

    name: str
    kind: t.Literal["job", "elt"] = Field(
        description="'job' schedules run a named job; 'elt' is the legacy form.",
    )
    interval: str | None = Field(
        description="As declared: a cron expression or an alias like '@daily'.",
    )
    cron_interval: str | None = Field(
        default=None,
        description=(
            "`interval` resolved to a cron expression. Null when the schedule "
            "never fires on its own, which is what '@manual', '@once' and "
            "'@none' mean."
        ),
    )
    env: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables applied when the schedule runs.",
    )
    job: str | None = Field(
        default=None,
        description="The job this runs, for 'job' schedules.",
    )
    extractor: str | None = Field(
        default=None,
        description="Legacy 'elt' schedules only.",
    )
    loader: str | None = Field(
        default=None,
        description="Legacy 'elt' schedules only.",
    )
    transform: str | None = Field(
        default=None,
        description="Legacy 'elt' schedules only.",
    )
    last_successful_run_at: str | None = Field(
        default=None,
        description=(
            "When this schedule last succeeded. Only 'elt' schedules record "
            "this, because their state ID is the schedule's own name; for "
            "'job' schedules it is always null."
        ),
    )
    can_run: bool = Field(
        description=(
            "Whether this server can start the schedule on demand. False for "
            "legacy 'elt' schedules, which must be run from the CLI."
        ),
    )


class ScheduleCreateRequest(BaseModel):
    """A request to declare a new schedule.

    Only job schedules can be created here. The legacy `elt` form is still
    read, updated and removed, but new ones belong in the CLI.
    """

    name: str = Field(min_length=1)
    job: str = Field(min_length=1, description="An existing job's name.")
    interval: str = Field(
        min_length=1,
        description="A cron expression, or an alias such as '@daily'.",
    )
    env: dict[str, str] = Field(default_factory=dict)


class ScheduleUpdateRequest(BaseModel):
    """A request to change an existing schedule.

    Omitted fields are left as they are.
    """

    interval: str | None = None
    job: str | None = None
