"""Tests for the `meltano ui` command."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.cli import cli
from meltano.ui._deps import MissingUIExtraError, check_ui_extra, missing_modules

if t.TYPE_CHECKING:
    from click.testing import CliRunner

    from fixtures.cli import MeltanoCliRunner


class TestMissingExtra:
    """Behaviour when `meltano[ui]` is not installed."""

    def test_missing_modules_are_reported(self) -> None:
        """Absent modules are named so the error can list them."""
        with mock.patch("importlib.util.find_spec", return_value=None):
            assert missing_modules() == ("fastapi", "uvicorn")

    def test_check_passes_when_installed(self) -> None:
        """Nothing is raised when the extra is present."""
        with mock.patch("importlib.util.find_spec", return_value=object()):
            check_ui_extra()

    def test_error_is_actionable(self) -> None:
        """The error follows Meltano's reason/instruction convention."""
        with (
            mock.patch("importlib.util.find_spec", return_value=None),
            pytest.raises(MissingUIExtraError) as exc_info,
        ):
            check_ui_extra()

        message = str(exc_info.value)
        assert "fastapi" in message
        assert "pip install 'meltano[ui]'" in message

    def test_command_fails_cleanly_without_the_extra(
        self,
        cli_runner: MeltanoCliRunner,
        project: t.Any,  # noqa: ARG002
    ) -> None:
        """`meltano ui` reports the missing extra rather than an ImportError."""
        with mock.patch(
            "meltano.ui._deps.missing_modules",
            return_value=("fastapi",),
        ):
            result = cli_runner.invoke(cli, ["ui"])

        assert result.exit_code != 0
        # `main()` is what renders a MeltanoError into the two-part CLI
        # message; the runner invokes `cli` directly, so the exception itself
        # is what carries the instruction here.
        assert isinstance(result.exception, MissingUIExtraError)
        assert "pip install 'meltano[ui]'" in str(result.exception)


class TestHostGuard:
    """Binding beyond loopback requires an explicit opt-in."""

    def test_non_loopback_host_is_refused(
        self,
        cli_runner: MeltanoCliRunner,
        project: t.Any,  # noqa: ARG002
    ) -> None:
        """A public bind address without `--allow-remote` is a usage error."""
        result = cli_runner.invoke(cli, ["ui", "--host", "0.0.0.0", "--no-browser"])  # noqa: S104

        assert result.exit_code != 0
        assert "--allow-remote" in result.output

    def test_loopback_host_is_accepted(
        self,
        cli_runner: MeltanoCliRunner,
        project: t.Any,  # noqa: ARG002
    ) -> None:
        """A loopback bind proceeds to start the server."""
        with mock.patch("meltano.ui.server.serve", new=mock.AsyncMock()) as serve:
            cli_runner.invoke(
                cli,
                ["ui", "--host", "127.0.0.1", "--no-browser"],
            )

        assert serve.await_count == 1


class TestSystemDatabase:
    """The system database has to be usable before the server answers."""

    def test_the_database_is_migrated_before_serving(
        self,
        cli_runner: MeltanoCliRunner,
        project: t.Any,  # noqa: ARG002
    ) -> None:
        """Serving an unmigrated database fails only at the first query.

        Every other command migrates through `pass_project(migrate=True)`,
        which this one cannot use - running outside a project is the
        interesting case here rather than an error. Doing it by hand is easy
        to drop, and the loss is invisible against a database some other
        command has already migrated: it surfaces only against a fresh one,
        as a 500 from a server that reported itself healthy.
        """
        with (
            mock.patch("meltano.ui.server.serve", new=mock.AsyncMock()) as serve,
            mock.patch("meltano.cli.ui._migrate") as migrate,
        ):
            cli_runner.invoke(cli, ["ui", "--no-browser"])

        assert migrate.call_count == 1
        assert serve.await_count == 1


class TestCommandRegistration:
    """The command must be reachable without the extra installed."""

    def test_ui_is_listed_in_help(self, cli_runner: CliRunner) -> None:
        """`meltano --help` must not fail for users without the extra."""
        result = cli_runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "ui" in result.output
