"""The `meltano ui` command.

This module is imported whenever the CLI is imported, so it must not import
anything from the optional ``ui`` extra at module scope: `meltano --help` has
to keep working for the majority of users, who will never install it.
"""

from __future__ import annotations

import typing as t
from dataclasses import replace

import click

from meltano.cli.params import database_uri_option
from meltano.cli.utils import CliEnvironmentBehavior, InstrumentedCmd
from meltano.core.project import Project
from meltano.core.utils import run_async

if t.TYPE_CHECKING:
    from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5001


@click.command(
    cls=InstrumentedCmd,
    short_help="Run the Meltano web UI.",
    environment_behavior=CliEnvironmentBehavior.environment_optional_use_default,
)
@click.option(
    "--host",
    default=DEFAULT_HOST,
    show_default=True,
    show_envvar=True,
    envvar="MELTANO_UI_HOST",
    help="Address to bind to. Binding to anything other than a loopback "
    "address additionally requires --allow-remote.",
)
@click.option(
    "--port",
    default=DEFAULT_PORT,
    type=click.IntRange(min=0, max=65535),
    show_default=True,
    show_envvar=True,
    envvar="MELTANO_UI_PORT",
    help="Port to listen on. Use 0 to pick an unused port.",
)
@click.option(
    "--allow-remote",
    is_flag=True,
    envvar="MELTANO_UI_ALLOW_REMOTE",
    help="Permit binding to a non-loopback address. The UI can execute "
    "plugins and read project secrets, so this exposes both to the network.",
)
@click.option(
    "--no-browser",
    is_flag=True,
    help="Do not open a browser window on startup.",
)
@click.option(
    "--readonly",
    is_flag=True,
    envvar="MELTANO_UI_READONLY",
    help="Refuse every request that would modify the project.",
)
@database_uri_option
@click.pass_context
def ui(
    ctx: click.Context,
    *,
    host: str,
    port: int,
    allow_remote: bool,
    no_browser: bool,
    readonly: bool,
) -> None:
    """Run the Meltano web UI.

    The UI is a local, single-user development server. It serves a browser
    application for inspecting runs and configuring plugins.

    Run outside a project, it offers to create one or open an existing one,
    then restarts to serve it.

    \b
    Requires the `ui` extra:
        pip install 'meltano[ui]'
    """  # noqa: D301
    from meltano.ui._deps import check_ui_extra

    check_ui_extra()

    from meltano.ui.settings import LOOPBACK_HOSTS, UIServerSettings

    if host not in LOOPBACK_HOSTS and not allow_remote:
        msg = (
            f"Refusing to bind to {host!r} without --allow-remote. The Meltano "
            "UI can execute plugins and read project secrets, so exposing it "
            "beyond localhost gives anyone who can reach this port the same "
            "abilities."
        )
        raise click.UsageError(msg)

    settings = UIServerSettings(
        host=host,
        port=port,
        open_browser=not no_browser,
        readonly=readonly,
    )

    if not settings.is_loopback:
        click.secho(
            f"WARNING: the Meltano UI is listening on {host}:{port}, which is "
            "reachable from other machines.",
            fg="red",
            bold=True,
            err=True,
        )

    click.echo("Meltano UI is starting. Open:")
    click.secho(f"  {settings.url}", fg="green", bold=True)

    from meltano.ui.server import serve, serve_setup

    # Deliberately not `pass_project`, which refuses to run outside a project.
    # Here that is the interesting case rather than an error.
    project = ctx.obj["project"]

    if project is None:
        # `run_async` decorates a coroutine *function*; it does not run one.
        chosen = run_async(serve_setup)(settings)
        if chosen is None:
            # Interrupted before choosing. Nothing was created that needs
            # undoing, so there is nothing to report.
            return

        project = _open_project(ctx, chosen)
        click.echo(f"Now serving {chosen}. Reload the page if it does not.")
        # The browser is already open on this port, and the token has not
        # changed, so the second server must not open another window.
        settings = replace(settings, open_browser=False)

    run_async(serve)(project, settings)


def _open_project(ctx: click.Context, root: Path) -> Project:
    """Activate a project and bring its system database up to date.

    This is `pass_project`'s work, done by hand because the project is chosen
    at runtime rather than found before the command starts. Activation is safe
    here and only here: the setup server has stopped, and nothing in this
    process has activated a project yet.

    Args:
        ctx: The Click context, for the root group's environment options.
        root: The project's root directory.

    Returns:
        The activated project.
    """
    from meltano.cli.cli import detect_selected_environment
    from meltano.core.db import project_engine
    from meltano.core.migration_service import MigrationService

    project = Project(root)
    Project.activate(project)

    # The group callback picks the environment before the command runs, which
    # for a projectless launch was too early to pick anything. Redone here so
    # that a project chosen in the browser gets the same `default_environment`
    # a project found on disk would, rather than silently running with none.
    root_params = ctx.find_root().params
    if selected := detect_selected_environment(
        cli_environment=root_params.get("environment"),
        cli_no_environment=bool(root_params.get("no_environment")),
        project=project,
    )[0]:
        project.activate_environment(selected)

    engine, _ = project_engine(project, default=True)
    MigrationService(engine).upgrade(silent=True)

    return project
