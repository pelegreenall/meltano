"""Tests for the plugin inventory endpoint."""

from __future__ import annotations

import shutil
import typing as t
from types import SimpleNamespace
from unittest import mock

import pytest

from meltano.ui.services.run_manager import RunKind

if t.TYPE_CHECKING:
    from pathlib import Path

    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project

PLUGINS = "/api/v1/plugins"


def reset_venv(project: Project, plugin: ProjectPlugin) -> Path:
    """Return a plugin's venv path, guaranteed absent.

    The `project` fixture is class-scoped and `pytest-randomly` shuffles order,
    so these tests would otherwise observe each other's directories.

    Args:
        project: The test project.
        plugin: The plugin whose venv path is wanted.

    Returns:
        The venv path, which does not exist on return.
    """
    venv = project.dirs.plugin(plugin, "venv", make_dirs=False)
    if venv.exists():
        shutil.rmtree(venv)
    return venv


@pytest.mark.usefixtures("tap")
class TestListPlugins:
    """Reading the project's declared plugins."""

    def test_lists_declared_plugins(self, ui_client: TestClient) -> None:
        """Plugins in meltano.yml are returned with their metadata."""
        payload = ui_client.get(PLUGINS).json()
        names = {plugin["name"] for plugin in payload}
        assert "tap-mock" in names

        entry = next(p for p in payload if p["name"] == "tap-mock")
        assert entry["type"] == "extractors"
        assert "is_installed" in entry

    def test_requires_authentication(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """The inventory is not public."""
        assert ui_client_anonymous.get(PLUGINS).status_code == 401

    def test_is_sorted_by_type_then_name(self, ui_client: TestClient) -> None:
        """Ordering is stable so the UI does not shuffle between polls."""
        payload = ui_client.get(PLUGINS).json()
        keys = [(plugin["type"], plugin["name"]) for plugin in payload]
        assert keys == sorted(keys)


@pytest.mark.usefixtures("tap")
class TestInstallDetection:
    """`is_installed` must reflect reality, and must not create it."""

    def test_reports_not_installed_without_an_interpreter(
        self,
        ui_client: TestClient,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """A bare venv directory is not an installation.

        `meltano add --no-install` leaves the directory behind with nothing in
        it, so presence of the directory alone would report every plugin as
        installed.
        """
        venv = reset_venv(project, tap)
        venv.mkdir(parents=True, exist_ok=True)

        entry = next(
            plugin
            for plugin in ui_client.get(PLUGINS).json()
            if plugin["name"] == tap.name
        )
        assert entry["is_installed"] is False

    def test_reports_installed_when_an_interpreter_exists(
        self,
        ui_client: TestClient,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """An interpreter in the venv counts as installed."""
        venv = reset_venv(project, tap)
        for subdir, binary in (("bin", "python"), ("Scripts", "python.exe")):
            (venv / subdir).mkdir(parents=True, exist_ok=True)
            (venv / subdir / binary).touch()

        entry = next(
            plugin
            for plugin in ui_client.get(PLUGINS).json()
            if plugin["name"] == tap.name
        )
        assert entry["is_installed"] is True

    def test_listing_does_not_write_to_disk(
        self,
        ui_client: TestClient,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """A GET must not create plugin directories as a side effect."""
        venv = reset_venv(project, tap)

        ui_client.get(PLUGINS)

        assert not venv.exists(), "listing plugins created a directory"


@pytest.mark.usefixtures("tap")
class TestPluginTasks:
    """Install and test run as supervised background tasks."""

    def test_install_is_accepted_as_a_task(
        self,
        ui_client: TestClient,
        ui_context: t.Any,
        tap: ProjectPlugin,
    ) -> None:
        """Installing returns a run id rather than blocking the request."""
        record = SimpleNamespace(run_id="task-1", kind=RunKind.install)
        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=record),
        ) as start:
            response = ui_client.post(
                f"/api/v1/plugins/{tap.type}/{tap.name}/install",
                json={},
            )

        assert response.status_code == 202
        assert response.json()["run_id"] == "task-1"
        assert start.await_args.kwargs["kind"] is RunKind.install

    def test_install_argv_targets_the_plugin(
        self,
        ui_context: t.Any,
        tap: ProjectPlugin,
    ) -> None:
        """The command names the plugin and its type explicitly."""
        argv = ui_context.run_manager.build_install_argv(str(tap.type), tap.name)
        assert "install" in argv
        assert f"--plugin-type={tap.type}" in argv
        assert argv[-1] == tap.name

    def test_clean_install_is_forwarded(
        self,
        ui_context: t.Any,
        tap: ProjectPlugin,
    ) -> None:
        """`clean` maps to the CLI's reinstall flag."""
        argv = ui_context.run_manager.build_install_argv(
            str(tap.type),
            tap.name,
            clean=True,
        )
        assert "--clean" in argv

    def test_test_is_accepted_as_a_task(
        self,
        ui_client: TestClient,
        ui_context: t.Any,
        tap: ProjectPlugin,
    ) -> None:
        """Testing a plugin returns a followable run id."""
        record = SimpleNamespace(run_id="task-2", kind=RunKind.test)
        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=record),
        ):
            response = ui_client.post(
                f"/api/v1/plugins/{tap.type}/{tap.name}/test",
                json={},
            )

        assert response.status_code == 202
        assert response.json()["run_id"] == "task-2"

    def test_extractor_test_carries_no_warning(
        self,
        ui_client: TestClient,
        ui_context: t.Any,
        tap: ProjectPlugin,
    ) -> None:
        """Testing an extractor is read-only, so there is nothing to warn about."""
        record = SimpleNamespace(run_id="task-3", kind=RunKind.test)
        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=record),
        ):
            response = ui_client.post(
                f"/api/v1/plugins/{tap.type}/{tap.name}/test",
                json={},
            )

        assert response.json()["warning"] is None

    def test_loader_test_warns_about_the_write(
        self,
        ui_client: TestClient,
        ui_context: t.Any,
        target: ProjectPlugin,
    ) -> None:
        """`LoaderTestService` writes a real table, so the UI must be told.

        The warning has to reach the caller before the task is followed, so it
        can be surfaced as a confirmation rather than an after-the-fact notice.
        """
        record = SimpleNamespace(run_id="task-4", kind=RunKind.test)
        with mock.patch.object(
            ui_context.run_manager,
            "start",
            new=mock.AsyncMock(return_value=record),
        ):
            response = ui_client.post(
                f"/api/v1/plugins/{target.type}/{target.name}/test",
                json={},
            )

        assert "meltano_test_stream" in response.json()["warning"]

    def test_unknown_plugin_is_404(self, ui_client: TestClient) -> None:
        """Tasks cannot be started for plugins that do not exist."""
        for action in ("install", "test"):
            response = ui_client.post(
                f"/api/v1/plugins/extractors/tap-nope/{action}",
                json={},
            )
            assert response.status_code == 404


@pytest.fixture
def commanded(project: Project) -> t.Iterator[ProjectPlugin]:
    """Add a plugin that declares commands, and take it away afterwards.

    Most plugins declare none; a transformer is the case this endpoint exists
    for, so the fixture models one.

    Args:
        project: The test project.

    Yields:
        The plugin.
    """
    from meltano.core.plugin import PluginType
    from meltano.core.plugin.project_plugin import ProjectPlugin as Plugin

    plugin = Plugin(
        PluginType.TRANSFORMERS,
        "test-transformer",
        namespace="test_transformer",
        executable="test-transformer",
        commands={
            "run": {"args": "run", "description": "Execute the models."},
            "build": {"args": "build", "description": "Models and tests."},
            "docs-generate": {"args": "docs generate"},
        },
    )
    project.plugins.add_to_file(plugin)
    try:
        yield plugin
    finally:
        # The `project` fixture is class-scoped, so this would otherwise be
        # visible to whatever runs next.
        project.plugins.remove_from_file(plugin)


@pytest.mark.usefixtures("commanded")
class TestPluginCommands:
    """Discovering the things a plugin can be told to do."""

    def test_commands_are_listed_with_their_block_names(
        self,
        ui_client: TestClient,
    ) -> None:
        """The block name is the point of the endpoint.

        `meltano run` takes `plugin:command`, and nothing else in the API
        tells a caller that spelling, so building it in the browser would mean
        the frontend inventing the convention for itself.
        """
        body = ui_client.get(
            f"{PLUGINS}/transformers/test-transformer/commands",
        ).json()

        assert [command["name"] for command in body] == [
            "build",
            "docs-generate",
            "run",
        ]
        assert [command["block"] for command in body] == [
            "test-transformer:build",
            "test-transformer:docs-generate",
            "test-transformer:run",
        ]

    def test_the_underlying_args_are_reported(
        self,
        ui_client: TestClient,
    ) -> None:
        """A command's name is not always what it runs.

        `docs-generate` invokes `docs generate`, which is worth showing rather
        than leaving someone to guess that the two are the same thing.
        """
        body = ui_client.get(
            f"{PLUGINS}/transformers/test-transformer/commands",
        ).json()
        docs = next(c for c in body if c["name"] == "docs-generate")

        assert docs["args"] == "docs generate"
        assert docs["description"] is None

    def test_a_listed_command_is_a_runnable_block(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """What this endpoint offers, `POST /runs` has to accept.

        The two are validated by different code, so a block name that the UI
        advertises and the runs endpoint rejects is a real possibility.
        """
        from meltano.ui.routers.runs import validate_blocks

        body = ui_client.get(
            f"{PLUGINS}/transformers/test-transformer/commands",
        ).json()

        validate_blocks(project, [command["block"] for command in body])


@pytest.fixture
def uncommanded(project: Project) -> t.Iterator[ProjectPlugin]:
    """Add a plugin that declares no commands at all.

    Args:
        project: The test project.

    Yields:
        The plugin.
    """
    from meltano.core.plugin import PluginType
    from meltano.core.plugin.project_plugin import ProjectPlugin as Plugin

    plugin = Plugin(
        PluginType.UTILITIES,
        "quiet-utility",
        namespace="quiet_utility",
        executable="quiet-utility",
    )
    project.plugins.add_to_file(plugin)
    try:
        yield plugin
    finally:
        project.plugins.remove_from_file(plugin)


class TestPluginWithoutCommands:
    """A plugin that declares none."""

    @pytest.mark.usefixtures("uncommanded")
    def test_an_empty_list_rather_than_an_error(
        self,
        ui_client: TestClient,
    ) -> None:
        """Declaring nothing is ordinary, not a failure."""
        response = ui_client.get(f"{PLUGINS}/utilities/quiet-utility/commands")

        assert response.status_code == 200
        assert response.json() == []

    def test_an_unknown_plugin_is_404(self, ui_client: TestClient) -> None:
        """Resolution is shared with the config endpoints."""
        response = ui_client.get(f"{PLUGINS}/extractors/nope/commands")

        assert response.status_code == 404

    def test_anonymous_access_is_refused(
        self,
        ui_client_anonymous: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Admission control applies here like anywhere else."""
        response = ui_client_anonymous.get(
            f"{PLUGINS}/extractors/{tap.name}/commands",
        )

        assert response.status_code == 401
