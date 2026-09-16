"""Shared server state for the Meltano UI.

The UI server owns exactly one activated :class:`~meltano.core.project.Project`
for its whole lifetime. ``Project`` carries process-global mutable state
(``Project._default``, class-level locks, and ``refresh()`` mutating
``__dict__`` in place), so it is resolved once by the CLI command and never
re-activated or refreshed per request.
"""

from __future__ import annotations

import typing as t
from dataclasses import dataclass, field

import anyio

from meltano.core.db import project_engine
from meltano.ui.services.project_watch import ProjectWatcher
from meltano.ui.services.run_manager import RunManager

if t.TYPE_CHECKING:
    from sqlalchemy.orm import Session, sessionmaker

    from meltano.core.project import Project
    from meltano.ui.settings import UIServerSettings


@dataclass(kw_only=True)
class AppContext:
    """Everything a request handler may need, resolved once at startup."""

    project: Project
    settings: UIServerSettings
    session_factory: sessionmaker[Session]
    run_manager: RunManager
    watcher: ProjectWatcher

    #: Created lazily inside the event loop: depending on the anyio version,
    #: `anyio.Lock()` resolves the async backend in `__new__` and so cannot be
    #: constructed at import time.
    _write_lock: anyio.Lock | None = field(default=None, init=False, repr=False)

    @property
    def write_lock(self) -> anyio.Lock:
        """Serializes every mutation of `meltano.yml`.

        `meltano_update()` triggers `Project.refresh()`, which invalidates
        `cached_property` caches on the shared project object and would
        otherwise race concurrent readers.

        Returns:
            The process-wide write lock.
        """
        if self._write_lock is None:
            self._write_lock = anyio.Lock()
        return self._write_lock

    @classmethod
    def create(cls, project: Project, settings: UIServerSettings) -> AppContext:
        """Build a context for an already-activated project.

        Args:
            project: The project the server will serve, already activated by the
                CLI's `pass_project` decorator.
            settings: Launch-time server settings.

        Returns:
            The assembled application context.
        """
        _, session_factory = project_engine(project)
        return cls(
            project=project,
            settings=settings,
            session_factory=session_factory,
            run_manager=RunManager(project),
            watcher=ProjectWatcher(project),
        )

    @property
    def readonly(self) -> bool:
        """Whether mutating requests should be refused.

        Read-only mode is forced on when the project itself is read-only, so
        `MELTANO_PROJECT_READONLY` cannot be bypassed via the UI.

        Returns:
            True if no mutation is permitted.
        """
        return self.settings.readonly or self.project.readonly

    @property
    def environment_name(self) -> str | None:
        """The active Meltano environment's name, if any.

        Returns:
            The environment name, or None when no environment is active.
        """
        return self.project.environment.name if self.project.environment else None
