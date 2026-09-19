"""Keeps the server's single `Project` in step with `meltano.yml` on disk.

The UI server activates one `Project` and never re-activates it, because
activation and `refresh()` mutate process-global state. That is the right call
for safety, but it means edits made outside the server - someone running
`meltano add` in a terminal, or editing `meltano.yml` by hand - would otherwise
stay invisible until a restart.

This drops only the caches that hold parsed `meltano.yml` content, and only
when the file's mtime or size actually changed. It never re-activates the
project, never changes the active environment, and never touches
`Project._default`.
"""

from __future__ import annotations

import typing as t

import structlog

if t.TYPE_CHECKING:
    from meltano.core.project import Project

logger = structlog.stdlib.get_logger(__name__)

#: `cached_property` names on `Project` that hold parsed `meltano.yml` state.
#: `hub_service` and `dirs` are deliberately absent - neither caches file
#: content, and rebuilding the Hub client would throw away its response cache.
_CACHED_ATTRS = ("config_service", "project_files", "plugins", "settings")


class ProjectWatcher:
    """Invalidates parsed-config caches when `meltano.yml` changes."""

    def __init__(self, project: Project) -> None:
        """Initialize the watcher.

        Args:
            project: The project to keep current.
        """
        self.project = project
        self._stamp = self._read_stamp()

    def _read_stamp(self) -> tuple[float, int] | None:
        try:
            stat = self.project.meltanofile.stat()
        except OSError:
            return None
        return (stat.st_mtime, stat.st_size)

    def refresh_if_stale(self) -> bool:
        """Drop cached config if `meltano.yml` changed since the last check.

        Returns:
            True if caches were invalidated.
        """
        stamp = self._read_stamp()
        if stamp == self._stamp:
            return False

        self._stamp = stamp
        for attr in _CACHED_ATTRS:
            self.project.__dict__.pop(attr, None)

        logger.debug("Reloaded meltano.yml after an external change")
        return True
