"""Tests for SSE frame construction and run streaming."""

from __future__ import annotations

import asyncio
import json
import sys
import typing as t

import pytest

from meltano.ui.services import log_stream
from meltano.ui.services.run_manager import RunKind, RunManager

if t.TYPE_CHECKING:
    from meltano.core.project import Project


@pytest.fixture
def manager(project: Project) -> RunManager:
    """Return a manager bound to the test project.

    Args:
        project: The test project.

    Returns:
        The manager.
    """
    return RunManager(project)


async def wait_until_terminal(manager: RunManager, run_id: str) -> None:
    """Block until a run has been fully drained and reaped.

    Args:
        manager: The supervising manager.
        run_id: The run to await.
    """
    async with asyncio.timeout(30):
        while not manager.get(run_id).status.is_terminal:  # noqa: ASYNC110
            await asyncio.sleep(0.05)


def parse_frames(raw: list[str]) -> list[tuple[str, dict[str, t.Any]]]:
    """Parse rendered SSE frames into `(event, data)` pairs.

    Args:
        raw: The frames as emitted.

    Returns:
        Decoded events, ignoring keepalive comments.
    """
    events: list[tuple[str, dict[str, t.Any]]] = []
    for frame in raw:
        if frame.startswith(":"):
            continue
        name = ""
        payload = "{}"
        for line in frame.strip().splitlines():
            if line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                payload = line.removeprefix("data: ")
        events.append((name, json.loads(payload)))
    return events


class TestFormatEvent:
    """Frame rendering."""

    def test_includes_id_event_and_data(self) -> None:
        """A frame carries its id, name and JSON payload."""
        frame = log_stream.format_event("log", {"message": "hi"}, event_id=7)
        assert frame.startswith("id: 7\nevent: log\ndata: ")
        assert frame.endswith("\n\n")

    def test_omits_id_when_absent(self) -> None:
        """Frames without an id do not emit an empty `id:` line."""
        assert "id:" not in log_stream.format_event("status", {})

    def test_payload_is_json(self) -> None:
        """The data line is parseable JSON."""
        frame = log_stream.format_event("log", {"a": 1})
        data = frame.split("data: ", 1)[1].strip()
        assert json.loads(data) == {"a": 1}


class TestParseLastEventId:
    """Resumption via the `Last-Event-ID` header."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        (
            (None, 0),
            ("", 0),
            ("12", 12),
            ("not-a-number", 0),
            ("-5", 0),
        ),
    )
    def test_parsing(self, raw: str | None, expected: int) -> None:
        """Malformed headers degrade to replaying from the start."""
        assert log_stream.parse_last_event_id(raw) == expected


class TestDecodeLine:
    """Structured versus plain output lines."""

    def test_json_lines_are_marked_structured(self) -> None:
        """Meltano and Singer SDK JSON logs keep their fields."""
        payload = log_stream._decode_line(
            '{"event": "Running job", "level": "info", "stream_name": "users"}',
        )
        assert payload["structured"] is True
        assert payload["event"] == "Running job"
        assert payload["stream_name"] == "users"

    def test_plain_lines_pass_through(self) -> None:
        """Non-JSON output is still delivered."""
        payload = log_stream._decode_line("plain text")
        assert payload == {"structured": False, "message": "plain text"}

    def test_malformed_json_is_treated_as_plain(self) -> None:
        """A line that only looks like JSON must not raise."""
        payload = log_stream._decode_line("{not valid}")
        assert payload["structured"] is False


class TestStreamRun:
    """End-to-end streaming of a finished run."""

    async def test_unknown_run_emits_an_error(self, manager: RunManager) -> None:
        """Streaming a nonexistent run reports it rather than hanging."""
        frames = [frame async for frame in log_stream.stream_run(manager, "nope")]
        events = parse_frames(frames)
        assert events[0][0] == "error"

    async def test_emits_status_logs_and_end(self, manager: RunManager) -> None:
        """A completed run yields status, one log per line, then end."""
        record = await manager.start(
            [sys.executable, "-c", "print('alpha', flush=True)"],
            kind=RunKind.run,
        )
        # Let the supervisor drain and reap before streaming.
        await wait_until_terminal(manager, record.run_id)

        events = parse_frames(
            [frame async for frame in log_stream.stream_run(manager, record.run_id)],
        )
        names = [name for name, _ in events]
        assert names[0] == "status"
        assert names[-1] == "end"
        assert ("log", {"structured": False, "message": "alpha"}) in events

    async def test_end_reports_the_exit_code(self, manager: RunManager) -> None:
        """The terminating frame carries the outcome the UI displays."""
        record = await manager.start(
            [sys.executable, "-c", "import sys; sys.exit(4)"],
            kind=RunKind.run,
        )
        await wait_until_terminal(manager, record.run_id)

        events = parse_frames(
            [frame async for frame in log_stream.stream_run(manager, record.run_id)],
        )
        name, payload = events[-1]
        assert name == "end"
        assert payload["exit_code"] == 4
        assert payload["status"] == "failed"
