"""The `meltano ui` command.

This module is imported whenever the CLI is imported, so it must not import
anything from the optional ``ui`` extra at module scope: `meltano --help` has
to keep working for the majority of users, who will never install it.
"""

from __future__ import annotations

import click

from meltano.cli.params import pass_project
from meltano.cli.utils import CliEnvironmentBehavior, InstrumentedCmd
from meltano.core.project import Project  # noqa: TC001
from meltano.core.utils import run_async

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
@pass_project(migrate=True)
def ui(
    project: Project,
    *,
    host: str,
    port: int,
    allow_remote: bool,
    no_browser: bool,
    readonly: bool,
) -> None:
    """Run the Meltano web UI.

    The UI is a local, single-user development server. It serves a browser
    application for inspecting runs, configuring plugins and scaffolding
    connectors.

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

    from meltano.ui.server import serve

    # `run_async` decorates a coroutine *function*; it does not run a coroutine.
    run_async(serve)(project, settings)
