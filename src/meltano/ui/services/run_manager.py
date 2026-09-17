"""Supervision of Meltano subprocesses launched from the UI.

Everything the UI starts - pipeline runs, plugin installs, stream discovery,
plugin tests - is a child ``meltano`` process rather than in-process work.
That is not a stylistic choice:

* :class:`~meltano.core.block.extract_load.ExtractLoadBlocks` mutates
  ``os.environ`` through ``PluginInvoker``, so two concurrent runs in one
  process would corrupt each other's configuration.
* ``OutputLogger`` installs a handler on the *root* logger, so an in-process
  run would capture the web server's own log output into the pipeline log.

Children are started in their own process group. Meltano plugins are
grandchildren of this process, so signalling only the direct child would orphan
running taps and targets.
"""

from __future__ import annotations

import asyncio
import json
import os
import signal
import sys
import typing as t
from collections import deque
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum

import psutil
import structlog

from meltano.core.utils import new_run_id

if t.TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
    from pathlib import Path
    from uuid import UUID

    from meltano.core.project import Project

logger = structlog.stdlib.get_logger(__name__)

#: How many recent lines are replayed to a client that attaches mid-run.
RING_BUFFER_SIZE = 2000
#: Grace period between SIGTERM and SIGKILL when cancelling.
TERMINATE_GRACE_SECONDS = 30.0

_IS_WINDOWS = os.name == "nt"


class RunStatus(str, Enum):
    """Lifecycle of a supervised subprocess."""

    starting = "starting"
    running = "running"
    success = "success"
    failed = "failed"
    cancelled = "cancelled"
    #: The server restarted and the process is no longer alive, but we cannot
    #: tell whether it succeeded. The `Job` row in the system database is
    #: authoritative in this case.
    unknown = "unknown"

    @property
    def is_terminal(self) -> bool:
        """Whether no further transitions are expected.

        Returns:
            True if the task has finished.
        """
        return self in {
            RunStatus.success,
            RunStatus.failed,
            RunStatus.cancelled,
            RunStatus.unknown,
        }


class RunKind(str, Enum):
    """What a supervised subprocess is doing."""

    run = "run"
    install = "install"
    test = "test"
    discover = "discover"
    scaffold = "scaffold"


@dataclass(kw_only=True)
class RunRecord:
    """Serializable description of one supervised subprocess."""

    run_id: str
    kind: RunKind
    argv: list[str]
    status: RunStatus
    started_at: str
    log_path: str
    environment: str | None = None
    pid: int | None = None
    #: Process creation time, used to detect PID reuse after a server restart.
    create_time: float | None = None
    finished_at: str | None = None
    exit_code: int | None = None

    def to_dict(self) -> dict[str, t.Any]:
        """Return a JSON-serializable mapping.

        Returns:
            The record as plain data.
        """
        data = asdict(self)
        data["kind"] = self.kind.value
        data["status"] = self.status.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, t.Any]) -> RunRecord:
        """Rebuild a record from its serialized form.

        Args:
            data: A mapping previously produced by `to_dict`.

        Returns:
            The reconstructed record.
        """
        return cls(
            run_id=data["run_id"],
            kind=RunKind(data["kind"]),
            argv=list(data["argv"]),
            status=RunStatus(data["status"]),
            started_at=data["started_at"],
            log_path=data["log_path"],
            environment=data.get("environment"),
            pid=data.get("pid"),
            create_time=data.get("create_time"),
            finished_at=data.get("finished_at"),
            exit_code=data.get("exit_code"),
        )


@dataclass
class _LiveRun:
    """In-memory state for a single supervised subprocess."""

    record: RunRecord
    buffer: deque[tuple[int, str]] = field(
        default_factory=lambda: deque(maxlen=RING_BUFFER_SIZE),
    )
    subscribers: set[asyncio.Queue[tuple[int, str] | None]] = field(
        default_factory=set,
    )
    process: asyncio.subprocess.Process | None = None
    task: asyncio.Task[None] | None = None
    next_line_id: int = 1

    def publish(self, line: str) -> None:
        """Append a line and fan it out to attached subscribers.

        Args:
            line: The raw output line, without a trailing newline.
        """
        item = (self.next_line_id, line)
        self.next_line_id += 1
        self.buffer.append(item)
        for queue in self.subscribers:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(item)

    def close(self) -> None:
        """Signal end-of-stream to every subscriber."""
        for queue in self.subscribers:
            with suppress(asyncio.QueueFull):
                queue.put_nowait(None)


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class RunManager:
    """Starts, tracks, cancels and recovers Meltano subprocesses."""

    def __init__(self, project: Project) -> None:
        """Initialize the manager.

        Args:
            project: The project whose subprocesses are supervised.
        """
        self.project = project
        self._runs: dict[str, _LiveRun] = {}

    # -- Filesystem layout ------------------------------------------------

    @property
    def state_dir(self) -> Path:
        """Directory holding one sidecar JSON file per supervised run.

        Returns:
            The sidecar directory, created if absent.
        """
        path = self.project.dirs.meltano("run", "ui", "runs")
        path.mkdir(parents=True, exist_ok=True)
        return path

    def log_path(self, run_id: str) -> Path:
        """Return the UI's own log file for a run.

        The UI keeps its own log rather than reading `elt.log`, whose path
        depends on a `state_id` generated inside the run, and which does not
        exist at all for plain plugin-command blocks.

        Args:
            run_id: The run's identifier.

        Returns:
            The log file path, with its parent created.
        """
        path = self.project.dirs.meltano("logs", "ui", run_id) / "run.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def _sidecar_path(self, run_id: str) -> Path:
        return self.state_dir / f"{run_id}.json"

    def _write_sidecar(self, record: RunRecord) -> None:
        self._sidecar_path(record.run_id).write_text(
            json.dumps(record.to_dict(), indent=2),
        )

    # -- Command construction ---------------------------------------------

    def _base_argv(self, environment: str | None) -> list[str]:
        argv = [sys.executable, "-m", "meltano.cli", "--log-format=json"]
        if environment:
            argv.append(f"--environment={environment}")
        return argv

    def build_run_argv(
        self,
        blocks: Sequence[str],
        *,
        run_id: UUID | str,
        environment: str | None = None,
        full_refresh: bool = False,
        no_state_update: bool = False,
        force: bool = False,
    ) -> list[str]:
        """Build the argv for a `meltano run` invocation.

        Args:
            blocks: Block names, already validated against the project.
            run_id: Pre-generated identifier, so the caller holds a handle
                before the process exists and can later correlate it to the
                `Job` row the run creates.
            environment: Meltano environment to run in.
            full_refresh: Whether to pass `--full-refresh`.
            no_state_update: Whether to pass `--no-state-update`.
            force: Whether to pass `--force`.

        Returns:
            The complete argv.
        """
        argv = [*self._base_argv(environment), "run", f"--run-id={run_id}"]
        if full_refresh:
            argv.append("--full-refresh")
        if no_state_update:
            argv.append("--no-state-update")
        if force:
            argv.append("--force")
        argv.extend(blocks)
        return argv

    def build_install_argv(
        self,
        plugin_type: str,
        name: str,
        *,
        environment: str | None = None,
        clean: bool = False,
    ) -> list[str]:
        """Build the argv for installing one plugin.

        Args:
            plugin_type: Plural plugin type, e.g. "extractors".
            name: The plugin's name.
            environment: Meltano environment to run in.
            clean: Whether to reinstall from scratch.

        Returns:
            The complete argv.
        """
        argv = [
            *self._base_argv(environment),
            "install",
            f"--plugin-type={plugin_type}",
        ]
        if clean:
            argv.append("--clean")
        argv.append(name)
        return argv

    def build_test_argv(
        self,
        plugin_type: str,
        name: str,
        *,
        environment: str | None = None,
    ) -> list[str]:
        """Build the argv for testing a plugin's configuration.

        Args:
            plugin_type: Plural plugin type, e.g. "extractors".
            name: The plugin's name.
            environment: Meltano environment to run in.

        Returns:
            The complete argv.
        """
        return [
            *self._base_argv(environment),
            "config",
            f"--plugin-type={plugin_type}",
            "test",
            name,
        ]

    # -- Lifecycle ---------------------------------------------------------

    async def start(
        self,
        argv: Sequence[str],
        *,
        kind: RunKind,
        run_id: str | None = None,
        environment: str | None = None,
        env: Mapping[str, str] | None = None,
    ) -> RunRecord:
        """Spawn a supervised subprocess.

        Args:
            argv: The command to run.
            kind: What the command is doing.
            run_id: Identifier to use; generated when omitted.
            environment: The Meltano environment in effect.
            env: Extra environment variables for the child, such as the `env`
                a schedule declares. Layered over the server's own environment.

        Returns:
            The record for the started process.
        """
        run_id = run_id or str(new_run_id())
        log_path = self.log_path(run_id)
        # Created up front so the record's `log_path` is valid the moment it is
        # returned, rather than only once the child writes its first line.
        log_path.touch(exist_ok=True)

        record = RunRecord(
            run_id=run_id,
            kind=kind,
            argv=list(argv),
            status=RunStatus.starting,
            started_at=_utcnow(),
            log_path=str(log_path),
            environment=environment,
        )
        live = _LiveRun(record=record)
        self._runs[run_id] = live

        child_env = {
            **os.environ,
            **(env or {}),
            # Last so that it cannot be overridden: the resulting `Job` row is
            # attributed to the UI rather than the CLI whatever the caller
            # passes.
            "MELTANO_JOB_TRIGGER": "ui",
        }

        creation: dict[str, t.Any] = (
            {"creationflags": _windows_process_group_flag()}
            if _IS_WINDOWS
            else {"start_new_session": True}
        )

        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=self.project.root,
            env=child_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            **creation,
        )

        live.process = process
        record.pid = process.pid
        record.status = RunStatus.running
        with suppress(psutil.Error):
            record.create_time = psutil.Process(process.pid).create_time()
        self._write_sidecar(record)

        live.task = asyncio.create_task(self._supervise(live, log_path))
        logger.info(
            "Started UI subprocess",
            run_id=run_id,
            kind=kind.value,
            pid=process.pid,
        )
        return record

    async def _supervise(self, live: _LiveRun, log_path: Path) -> None:
        """Pump the child's output to disk and to subscribers, then reap it."""
        record = live.record
        process = live.process
        assert process is not None  # noqa: S101
        assert process.stdout is not None  # noqa: S101

        try:
            with log_path.open("a", encoding="utf-8") as log_file:
                async for raw in process.stdout:
                    line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                    log_file.write(f"{line}\n")
                    log_file.flush()
                    live.publish(line)

            record.exit_code = await process.wait()
        except asyncio.CancelledError:
            record.status = RunStatus.cancelled
            raise
        finally:
            record.finished_at = _utcnow()
            if record.status is not RunStatus.cancelled:
                record.status = (
                    RunStatus.success if record.exit_code == 0 else RunStatus.failed
                )
            self._write_sidecar(record)
            live.close()
            logger.info(
                "UI subprocess finished",
                run_id=record.run_id,
                status=record.status.value,
                exit_code=record.exit_code,
            )

    async def cancel(self, run_id: str) -> RunRecord | None:
        """Terminate a running subprocess and everything it spawned.

        Escalates from SIGTERM to SIGKILL. Meltano's own SIGTERM handling
        marks the `Job` row failed cleanly, so the graceful path is worth
        waiting for.

        Args:
            run_id: The run to cancel.

        Returns:
            The updated record, or None if the run is unknown.
        """
        live = self._runs.get(run_id)
        if live is None or live.process is None:
            return None

        record = live.record
        if record.status.is_terminal:
            return record

        record.status = RunStatus.cancelled
        _signal_process_group(live.process.pid, signal.SIGTERM)

        try:
            await asyncio.wait_for(
                live.process.wait(),
                timeout=TERMINATE_GRACE_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Subprocess ignored SIGTERM; killing",
                run_id=run_id,
                pid=live.process.pid,
            )
            _signal_process_group(live.process.pid, signal.SIGKILL)
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(live.process.wait(), timeout=10)

        self._write_sidecar(record)
        return record

    # -- Queries -----------------------------------------------------------

    def get(self, run_id: str) -> RunRecord | None:
        """Return a single run's record.

        Args:
            run_id: The run to look up.

        Returns:
            The record, or None if unknown.
        """
        live = self._runs.get(run_id)
        return live.record if live else None

    def list_runs(self) -> list[RunRecord]:
        """Return every known run, newest first.

        Returns:
            The records held in memory.
        """
        return sorted(
            (live.record for live in self._runs.values()),
            key=lambda record: record.started_at,
            reverse=True,
        )

    def buffered_lines(self, run_id: str) -> Iterable[tuple[int, str]]:
        """Return the lines currently held in a run's ring buffer.

        Args:
            run_id: The run to inspect.

        Returns:
            The buffered `(line_id, text)` pairs.
        """
        live = self._runs.get(run_id)
        return tuple(live.buffer) if live else ()

    async def subscribe(
        self,
        run_id: str,
        *,
        after_id: int = 0,
    ) -> AsyncIterator[tuple[int, str]]:
        """Yield a run's output lines, replaying buffered ones first.

        Args:
            run_id: The run to follow.
            after_id: Only yield lines with a greater id. Fed from the SSE
                `Last-Event-ID` header so a reconnecting browser resumes
                rather than restarting.

        Yields:
            `(line_id, text)` pairs, ending when the process exits.
        """
        live = self._runs.get(run_id)
        if live is None:
            return

        queue: asyncio.Queue[tuple[int, str] | None] = asyncio.Queue()
        live.subscribers.add(queue)
        try:
            for item in tuple(live.buffer):
                if item[0] > after_id:
                    yield item

            if live.record.status.is_terminal and queue.empty():
                return

            while True:
                received = await queue.get()
                if received is None:
                    return
                if received[0] > after_id:
                    yield received
        finally:
            live.subscribers.discard(queue)

    # -- Recovery ----------------------------------------------------------

    def recover(self) -> list[RunRecord]:
        """Re-adopt runs recorded by a previous server process.

        Children are started in their own session, so they survive the server
        exiting. A record whose process is gone is marked `unknown` rather
        than `failed`: the run may well have succeeded, and the `Job` row is
        the authority.

        Returns:
            The records recovered from disk.
        """
        recovered: list[RunRecord] = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                record = RunRecord.from_dict(json.loads(path.read_text()))
            except (OSError, ValueError, KeyError):
                logger.warning("Ignoring unreadable run sidecar", path=str(path))
                continue

            if not record.status.is_terminal and not _process_alive(record):
                record.status = RunStatus.unknown
                record.finished_at = record.finished_at or _utcnow()
                self._write_sidecar(record)

            self._runs.setdefault(record.run_id, _LiveRun(record=record))
            recovered.append(record)

        return recovered


def _process_alive(record: RunRecord) -> bool:
    """Whether a recorded process is still running, accounting for PID reuse."""
    if record.pid is None:
        return False
    try:
        process = psutil.Process(record.pid)
        if record.create_time is not None:
            # A recycled PID belongs to a different process entirely.
            return abs(process.create_time() - record.create_time) < 1.0
    except psutil.Error:
        return False
    return process.is_running()


def _windows_process_group_flag() -> int:
    """Return the Windows flag that puts a child in its own process group."""
    return getattr(__import__("subprocess"), "CREATE_NEW_PROCESS_GROUP", 0)


def _signal_process_group(pid: int, sig: signal.Signals) -> None:
    """Signal a child's whole process group, falling back to the child alone.

    Args:
        pid: The direct child's PID.
        sig: The signal to send.
    """
    if _IS_WINDOWS:
        # Windows has no process groups in the POSIX sense; terminate the tree
        # through psutil instead.
        with suppress(psutil.Error):
            process = psutil.Process(pid)
            for child in process.children(recursive=True):
                with suppress(psutil.Error):
                    child.kill()
            process.kill()
        return

    try:
        os.killpg(os.getpgid(pid), sig)
    except (ProcessLookupError, PermissionError):
        with suppress(ProcessLookupError, PermissionError):
            os.kill(pid, sig)
