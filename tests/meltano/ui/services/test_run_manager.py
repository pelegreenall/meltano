"""Tests for subprocess supervision.

These drive `RunManager` with trivial Python commands rather than real
pipelines: the behaviour under test is process handling, not ELT.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import typing as t
from contextlib import suppress

import psutil
import pytest

from meltano.ui.services.run_manager import (
    RunKind,
    RunManager,
    RunRecord,
    RunStatus,
)

if t.TYPE_CHECKING:
    from collections.abc import Iterator

    from meltano.core.project import Project

#: A child that exits immediately with a known code.
EXIT_WITH = "import sys; sys.exit({code})"
#: A child that emits lines then exits.
EMIT_LINES = (
    "import sys\nfor i in range({count}):\n    print(f'line-{{i}}', flush=True)"
)
#: A child that never exits on its own.
SLEEP_FOREVER = "import time\nwhile True:\n    time.sleep(0.1)"


def python_argv(source: str) -> list[str]:
    """Build an argv running the given Python source.

    Args:
        source: The program text.

    Returns:
        The argv.
    """
    return [sys.executable, "-c", source]


async def wait_for_status(
    manager: RunManager,
    run_id: str,
    *,
    timeout: float = 30.0,
) -> RunStatus:
    """Wait until a run reaches a terminal status.

    Args:
        manager: The manager supervising the run.
        run_id: The run to await.
        timeout: Seconds to wait before failing.

    Returns:
        The terminal status.

    Raises:
        AssertionError: If the run does not settle in time.
    """
    async with asyncio.timeout(timeout):
        while True:
            record = manager.get(run_id)
            assert record is not None
            if record.status.is_terminal:
                return record.status
            await asyncio.sleep(0.05)


@pytest.fixture
def manager(project: Project) -> Iterator[RunManager]:
    """Return a manager bound to the test project.

    Tears down any process the test left running. Without this, a test that
    fails before reaching its own `cancel()` call orphans the child it spawned.

    Args:
        project: The test project.

    Yields:
        The manager.
    """
    instance = RunManager(project)
    try:
        yield instance
    finally:
        for record in instance.list_runs():
            if not record.status.is_terminal and record.pid:
                with suppress(OSError, psutil.Error):
                    process = psutil.Process(record.pid)
                    for child in process.children(recursive=True):
                        with suppress(psutil.Error):
                            child.kill()
                    process.kill()


class TestArgvConstruction:
    """`meltano run` invocations built for the CLI."""

    def test_includes_run_id_and_json_logging(self, manager: RunManager) -> None:
        """The run id is passed through so the UI can correlate the Job row."""
        argv = manager.build_run_argv(
            ["tap-mock", "target-mock"],
            run_id="abc-123",
            environment="dev",
        )
        assert "--run-id=abc-123" in argv
        assert "--log-format=json" in argv
        assert "--environment=dev" in argv
        assert argv[-2:] == ["tap-mock", "target-mock"]

    def test_omits_environment_when_absent(self, manager: RunManager) -> None:
        """No environment means no `--environment` flag."""
        argv = manager.build_run_argv(["tap-mock"], run_id="x", environment=None)
        assert not any(arg.startswith("--environment") for arg in argv)

    @pytest.mark.parametrize(
        ("kwarg", "flag"),
        (
            ("full_refresh", "--full-refresh"),
            ("no_state_update", "--no-state-update"),
            ("force", "--force"),
        ),
    )
    def test_flags_are_forwarded(
        self,
        manager: RunManager,
        kwarg: str,
        flag: str,
    ) -> None:
        """Each supported option maps to its CLI flag."""
        argv = manager.build_run_argv(["tap-mock"], run_id="x", **{kwarg: True})
        assert flag in argv


class TestLifecycle:
    """Starting, completing and failing."""

    async def test_successful_run_is_recorded(self, manager: RunManager) -> None:
        """A zero exit becomes `success`."""
        record = await manager.start(
            python_argv(EXIT_WITH.format(code=0)),
            kind=RunKind.run,
        )
        assert record.status is RunStatus.running
        assert await wait_for_status(manager, record.run_id) is RunStatus.success
        assert manager.get(record.run_id).exit_code == 0

    async def test_failed_run_is_recorded(self, manager: RunManager) -> None:
        """A non-zero exit becomes `failed` and preserves the code."""
        record = await manager.start(
            python_argv(EXIT_WITH.format(code=3)),
            kind=RunKind.run,
        )
        assert await wait_for_status(manager, record.run_id) is RunStatus.failed
        assert manager.get(record.run_id).exit_code == 3

    async def test_output_is_written_to_the_log_file(
        self,
        manager: RunManager,
    ) -> None:
        """Captured output lands in the UI's own per-run log."""
        record = await manager.start(
            python_argv(EMIT_LINES.format(count=3)),
            kind=RunKind.run,
        )
        await wait_for_status(manager, record.run_id)

        contents = manager.log_path(record.run_id).read_text()
        assert "line-0" in contents
        assert "line-2" in contents

    async def test_job_trigger_marks_the_ui(self, manager: RunManager) -> None:
        """Runs are attributed to the UI, not the CLI."""
        record = await manager.start(
            python_argv(
                "import os; print(os.environ['MELTANO_JOB_TRIGGER'], flush=True)",
            ),
            kind=RunKind.run,
        )
        await wait_for_status(manager, record.run_id)
        assert "ui" in manager.log_path(record.run_id).read_text()

    async def test_listing_is_newest_first(self, manager: RunManager) -> None:
        """`list()` orders runs so the UI can show recent activity."""
        first = await manager.start(
            python_argv(EXIT_WITH.format(code=0)),
            kind=RunKind.run,
        )
        await wait_for_status(manager, first.run_id)
        second = await manager.start(
            python_argv(EXIT_WITH.format(code=0)),
            kind=RunKind.install,
        )
        await wait_for_status(manager, second.run_id)

        assert next(record.run_id for record in manager.list_runs()) == second.run_id


class TestSubscription:
    """Fan-out of output lines to SSE clients."""

    async def test_replays_buffered_lines(self, manager: RunManager) -> None:
        """A client attaching after the fact still sees earlier output."""
        record = await manager.start(
            python_argv(EMIT_LINES.format(count=5)),
            kind=RunKind.run,
        )
        await wait_for_status(manager, record.run_id)

        received = [text async for _, text in manager.subscribe(record.run_id)]
        assert received == [f"line-{index}" for index in range(5)]

    async def test_resumes_after_a_given_line(self, manager: RunManager) -> None:
        """`after_id` supports SSE reconnection via `Last-Event-ID`."""
        record = await manager.start(
            python_argv(EMIT_LINES.format(count=5)),
            kind=RunKind.run,
        )
        await wait_for_status(manager, record.run_id)

        received = [
            text async for _, text in manager.subscribe(record.run_id, after_id=3)
        ]
        assert received == ["line-3", "line-4"]

    async def test_unknown_run_yields_nothing(self, manager: RunManager) -> None:
        """Subscribing to a run that does not exist terminates cleanly."""
        assert [item async for item in manager.subscribe("nope")] == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
class TestCancellation:
    """Termination of the whole process tree."""

    async def test_cancel_stops_a_running_process(
        self,
        manager: RunManager,
    ) -> None:
        """Cancelling marks the run and reaps the child."""
        record = await manager.start(
            python_argv(SLEEP_FOREVER),
            kind=RunKind.run,
        )
        cancelled = await manager.cancel(record.run_id)

        assert cancelled is not None
        assert cancelled.status is RunStatus.cancelled
        assert manager.get(record.run_id).status is RunStatus.cancelled

    async def test_cancel_kills_grandchildren(self, manager: RunManager) -> None:
        """Plugins are grandchildren, so the group must be signalled.

        The child spawns its own child and reports its PID; after cancelling,
        that grandchild must be gone rather than orphaned.
        """
        source = (
            "import subprocess, sys, time\n"
            "child = subprocess.Popen([sys.executable, '-c',"
            " 'import time\\nwhile True: time.sleep(0.1)'])\n"
            "print(child.pid, flush=True)\n"
            "while True:\n"
            "    time.sleep(0.1)\n"
        )
        record = await manager.start(python_argv(source), kind=RunKind.run)

        async with asyncio.timeout(30):
            while not manager.log_path(record.run_id).read_text().strip():  # noqa: ASYNC110
                await asyncio.sleep(0.05)

        grandchild_pid = int(manager.log_path(record.run_id).read_text().split()[0])
        await manager.cancel(record.run_id)
        await asyncio.sleep(0.5)

        with pytest.raises(ProcessLookupError):
            os.kill(grandchild_pid, 0)

    async def test_cancelling_an_unknown_run_returns_none(
        self,
        manager: RunManager,
    ) -> None:
        """Cancelling something that does not exist is not an error."""
        assert await manager.cancel("nope") is None


class TestRecovery:
    """Re-adoption of runs that outlived a previous server process."""

    async def test_finished_run_is_recovered_verbatim(
        self,
        manager: RunManager,
        project: Project,
    ) -> None:
        """A terminal sidecar survives a restart unchanged."""
        record = await manager.start(
            python_argv(EXIT_WITH.format(code=0)),
            kind=RunKind.run,
        )
        await wait_for_status(manager, record.run_id)

        # Asserted by run id rather than by list position: the `project`
        # fixture is class-scoped, so sidecars from sibling tests persist and
        # `pytest-randomly` makes their order arbitrary.
        recovered = {entry.run_id: entry for entry in RunManager(project).recover()}
        assert recovered[record.run_id].status is RunStatus.success

    def test_dead_process_becomes_unknown_not_failed(
        self,
        manager: RunManager,
        project: Project,
    ) -> None:
        """A vanished process is `unknown`: the Job row is authoritative."""
        record = RunRecord(
            run_id="ghost",
            kind=RunKind.run,
            argv=["meltano", "run"],
            status=RunStatus.running,
            started_at="2026-01-01T00:00:00+00:00",
            log_path=str(manager.log_path("ghost")),
            # A PID that is essentially certain not to be running.
            pid=2**22,
            create_time=1.0,
        )
        (manager.state_dir / "ghost.json").write_text(json.dumps(record.to_dict()))

        recovered = {entry.run_id: entry for entry in RunManager(project).recover()}
        assert recovered["ghost"].status is RunStatus.unknown

    def test_unreadable_sidecar_is_skipped(
        self,
        manager: RunManager,
        project: Project,
    ) -> None:
        """A corrupt sidecar must not prevent the server from starting."""
        (manager.state_dir / "broken.json").write_text("{not json")

        # The corrupt file is skipped rather than raising, so a single bad
        # sidecar cannot stop the server from starting.
        recovered = RunManager(project).recover()
        assert "broken" not in {entry.run_id for entry in recovered}
