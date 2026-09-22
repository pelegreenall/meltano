"""Meltano's local web UI.

This package is only importable when the ``ui`` extra is installed. Nothing here
may be imported from :mod:`meltano.cli` at module scope - see
:mod:`meltano.ui._deps` for the guard that enforces a friendly error instead of
an :exc:`ImportError`.
"""

from __future__ import annotations

__all__ = ["MissingUIExtraError", "check_ui_extra"]

from meltano.ui._deps import MissingUIExtraError, check_ui_extra
