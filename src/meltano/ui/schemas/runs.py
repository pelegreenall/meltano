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


class RunInfo(BaseModel):
    """The state of one supervised subprocess."""

    run_id: str
    kind: str
    status: str
    argv: list[str]
    started_at: str
    log_path: str
    environment: str | None = None
    pid: int | None = None
    finished_at: str | None = None
    exit_code: int | None = None


class RunLog(BaseModel):
    """A run's captured output."""

    run_id: str
    lines: list[str]
