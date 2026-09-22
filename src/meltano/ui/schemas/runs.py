"""Models for run supervision endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RunRequest(BaseModel):
    """A request to start a pipeline run.

    Only block *names* are accepted. They are validated against the project by
    `BlockParser` before anything is spawned, so this endpoint cannot be used
    to execute an arbitrary command.
    """

    blocks: list[str] = Field(
        min_length=1,
        description="Block names, e.g. ['tap-github', 'target-jsonl'].",
    )
    full_refresh: bool = False
    no_state_update: bool = False
    force: bool = False


class RunJobInfo(BaseModel):
    """One `Job` row in the system database produced by a run.

    A single `meltano run` writes one row per ExtractLoadBlock, so a run with
    several blocks reports several of these.
    """

    job_name: str = Field(description="The block set's state ID.")
    state: str = Field(description="Core's job state, e.g. 'SUCCESS'.")
    started_at: str | None = None
    ended_at: str | None = None
    trigger: str | None = Field(
        default=None,
        description="What launched the run, from `MELTANO_JOB_TRIGGER`.",
    )


class RunInfo(BaseModel):
    """One run, whether this server supervised it or merely recorded it.

    Runs launched from a terminal, or by a previous server process, are known
    only from the system database and so carry no `argv` or log.
    """

    run_id: str
    kind: str
    status: str
    started_at: str
    argv: list[str] = Field(
        default_factory=list,
        description="Empty for runs this server did not launch.",
    )
    log_path: str | None = Field(
        default=None,
        description="Null for runs this server did not launch.",
    )
    environment: str | None = None
    pid: int | None = None
    finished_at: str | None = None
    exit_code: int | None = None
    jobs: list[RunJobInfo] = Field(
        default_factory=list,
        description="The system-database rows this run produced.",
    )
    has_log: bool = Field(
        default=False,
        description="Whether this server can serve the run's output.",
    )


class RunLog(BaseModel):
    """A run's captured output."""

    run_id: str
    lines: list[str]
