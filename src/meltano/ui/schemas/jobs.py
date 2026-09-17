"""Models for the job (task set) endpoints.

A "job" here is a `TaskSets`: a named list of run blocks declared in
`meltano.yml`. It is deliberately not the `Job` row in the system database,
which records a single *execution* and is surfaced by the runs endpoints
instead. Core uses the same word for both; these schemas keep them apart.
"""

from __future__ import annotations

import typing as t

from pydantic import BaseModel, Field

#: One task is either a whitespace-separated block string
#: (`"tap-github target-jsonl"`) or an already-split list of block names.
#: Both shapes are what `TASKS_JSON_SCHEMA` permits.
Task = t.Union[str, list[str]]  # noqa: UP007


class JobInfo(BaseModel):
    """A named job declared in the project."""

    name: str
    tasks: list[Task] = Field(description="The job's tasks, as declared.")
    blocks: list[str] = Field(
        description="Every block the job references, flattened and split.",
    )


class JobCreateRequest(BaseModel):
    """A request to declare a new job."""

    name: str = Field(min_length=1)
    tasks: list[Task] = Field(min_length=1)


class JobUpdateRequest(BaseModel):
    """A request to replace an existing job's tasks."""

    tasks: list[Task] = Field(min_length=1)
