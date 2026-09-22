"""Reading a few records out of an extractor without loading them anywhere.

Choosing streams and writing transformations is guesswork without seeing the
data first, which is the loop a tool like Power Query gets right: look at rows,
change something, look again.

This runs the extractor *alone* - `meltano invoke`, no loader, no state - and
reads its Singer output until it has enough records. The tap is then killed:
a preview must be bounded, because the alternative is pulling a full table
into a browser.

Nothing here writes to the project, the state backend, or a destination.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import sys
import typing as t
from dataclasses import dataclass, field

import structlog

if t.TYPE_CHECKING:
    from meltano.core.project import Project

logger = structlog.stdlib.get_logger(__name__)

_IS_WINDOWS = sys.platform == "win32"

#: Singer lines can be large - a record with an embedded document, say - and
#: the default 64KiB reader limit raises rather than truncating.
_STREAM_LIMIT = 8 * 1024 * 1024

#: How much of the tap's stderr to keep for a failure message. Enough to show
#: a traceback's last frames without returning a whole log.
_STDERR_TAIL = 4000


@dataclass
class PreviewResult:
    """What one preview run observed."""

    #: Stream name -> column names, taken from SCHEMA messages. Present even
    #: when a stream produced no records, which is itself worth showing.
    schemas: dict[str, list[str]] = field(default_factory=dict)
    records: list[dict[str, t.Any]] = field(default_factory=list)
    #: True when the tap was stopped early because the cap was reached, as
    #: opposed to having genuinely run out of records.
    truncated: bool = False
    timed_out: bool = False
    #: Set when the tap failed before producing anything useful.
    error: str | None = None


def _columns(schema: dict[str, t.Any]) -> list[str]:
    """List a Singer schema's top-level properties.

    Args:
        schema: The `schema` object from a SCHEMA message.

    Returns:
        The property names, in declaration order.
    """
    properties = schema.get("properties")
    return list(properties) if isinstance(properties, dict) else []


def _terminate(process: asyncio.subprocess.Process) -> None:
    """Stop a tap and anything it spawned.

    A tap mid-extraction is often a parent to HTTP or database clients, so the
    whole group is signalled rather than just the process Meltano started.

    Args:
        process: The process to stop.
    """
    if process.returncode is not None:
        return

    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
        if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
            process.terminate()
        else:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)


async def preview_records(
    project: Project,
    plugin_name: str,
    *,
    stream: str | None = None,
    limit: int = 20,
    timeout: float = 120.0,
    environment: str | None = None,
) -> PreviewResult:
    """Run an extractor and return the first records it emits.

    Args:
        project: The project the extractor belongs to.
        plugin_name: The extractor's name.
        stream: Only collect records for this stream; all streams when None.
        limit: How many records to collect before stopping the tap.
        timeout: How long to wait before giving up on the tap entirely.
        environment: The Meltano environment to invoke in.

    Returns:
        The records seen, the schemas announced, and why collection stopped.
    """
    argv = [sys.executable, "-m", "meltano.cli", "--log-format=json"]
    if environment:
        argv.append(f"--environment={environment}")
    argv += ["invoke", plugin_name]

    result = PreviewResult()

    # Its own session, so the whole tree can be stopped once we have enough.
    # Spelled out per platform rather than unpacked from a dict, which hides
    # the argument's type from the checker.
    if _IS_WINDOWS:  # pragma: no cover - exercised on Windows only
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=project.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
        )
    else:
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=project.root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=_STREAM_LIMIT,
            start_new_session=True,
        )

    try:
        async with asyncio.timeout(timeout):
            await _collect(process, result, stream=stream, limit=limit)
    except TimeoutError:
        result.timed_out = True
        logger.info("Preview timed out", plugin=plugin_name, timeout=timeout)
    finally:
        _terminate(process)
        stderr = b""
        with contextlib.suppress(Exception):
            stderr = await process.stderr.read() if process.stderr else b""
        with contextlib.suppress(Exception):
            async with asyncio.timeout(10):
                await process.wait()

    # A tap that produced nothing and exited non-zero failed; one that was
    # killed after giving us records did exactly what was asked of it.
    if not result.records and not result.schemas and not result.truncated:
        code = process.returncode
        if code not in {0, None} and not result.timed_out:
            result.error = _tail(stderr.decode("utf-8", "replace"))

    return result


async def _collect(
    process: asyncio.subprocess.Process,
    result: PreviewResult,
    *,
    stream: str | None,
    limit: int,
) -> None:
    """Read Singer messages until the cap is reached or the tap ends.

    Args:
        process: The running tap.
        result: Accumulates what is seen.
        stream: Only collect records for this stream, if given.
        limit: How many records to collect.
    """
    assert process.stdout is not None  # noqa: S101 - guaranteed by the caller

    while True:
        try:
            line = await process.stdout.readline()
        except (ValueError, asyncio.LimitOverrunError):
            # One oversized line should not discard the records already read.
            logger.warning("Skipped an oversized Singer message")
            continue

        if not line:
            return

        try:
            message = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            # Taps that log to stdout are common enough that a non-JSON line
            # is not worth failing the preview over.
            continue

        kind = message.get("type")
        if kind == "SCHEMA":
            name = message.get("stream")
            if name:
                result.schemas[name] = _columns(message.get("schema") or {})
        elif kind == "RECORD":
            if stream is not None and message.get("stream") != stream:
                continue
            record = message.get("record")
            if isinstance(record, dict):
                result.records.append(record)
                if len(result.records) >= limit:
                    result.truncated = True
                    return


def _tail(text: str) -> str:
    """Return the end of a failure message.

    Args:
        text: The captured stderr.

    Returns:
        The last part of it, which is where the cause usually is.
    """
    cleaned = text.strip()
    return cleaned[-_STDERR_TAIL:] if cleaned else "The extractor produced no output"
