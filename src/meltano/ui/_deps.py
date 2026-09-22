"""Dependency guard for the optional ``ui`` extra.

This module must stay importable with only Meltano's core dependencies
installed, because :mod:`meltano.cli` imports the ``ui`` command at CLI import
time. It therefore must not import FastAPI, uvicorn or anything else from the
extra at module scope.
"""

from __future__ import annotations

import importlib.util

from meltano.core.error import MeltanoError

#: Modules provided by the ``ui`` extra that the server cannot start without.
REQUIRED_MODULES: tuple[str, ...] = ("fastapi", "uvicorn")


class MissingUIExtraError(MeltanoError):
    """Raised when the Meltano UI is launched without its extra installed."""

    def __init__(self, missing: tuple[str, ...]) -> None:
        """Initialize the error.

        Args:
            missing: Names of the modules that could not be imported.
        """
        self.missing = missing
        super().__init__(
            reason=(
                "The Meltano UI requires additional dependencies that are not "
                f"installed: {', '.join(missing)}"
            ),
            instruction="Install them with `pip install 'meltano[ui]'`",
        )


def missing_modules() -> tuple[str, ...]:
    """Return the names of any required UI modules that are not importable.

    Returns:
        The missing module names, in declaration order.
    """
    return tuple(
        name for name in REQUIRED_MODULES if importlib.util.find_spec(name) is None
    )


def check_ui_extra() -> None:
    """Verify that the ``ui`` extra is installed.

    Raises:
        MissingUIExtraError: If any required module is unavailable.
    """
    if missing := missing_modules():
        raise MissingUIExtraError(missing)
