"""Tests for keeping the served project in step with `meltano.yml`."""

from __future__ import annotations

import typing as t

import pytest
import yaml

from meltano.ui.services.project_watch import ProjectWatcher

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.project import Project


class TestProjectWatcher:
    """Cache invalidation driven by the file's mtime and size.

    These use the function-scoped `project_function` fixture because they
    mutate and delete `meltano.yml`; the shared class-scoped `project` would
    carry that damage into sibling tests under `pytest-randomly`.
    """

    def test_unchanged_file_is_not_reloaded(self, project_function: Project) -> None:
        """A quiet project does no work."""
        watcher = ProjectWatcher(project_function)
        assert watcher.refresh_if_stale() is False

    def test_changed_file_is_reloaded(self, project_function: Project) -> None:
        """Touching the file invalidates the parsed-config caches."""
        watcher = ProjectWatcher(project_function)
        # Populate the caches so there is something to drop.
        _ = project_function.config_service
        assert "config_service" in project_function.__dict__

        project_function.meltanofile.write_text(
            project_function.meltanofile.read_text() + "\n# external edit\n",
        )

        assert watcher.refresh_if_stale() is True
        assert "config_service" not in project_function.__dict__

    def test_reload_is_reported_once(self, project_function: Project) -> None:
        """A single change triggers exactly one invalidation."""
        watcher = ProjectWatcher(project_function)
        project_function.meltanofile.write_text(
            project_function.meltanofile.read_text() + "\n# external edit\n",
        )

        assert watcher.refresh_if_stale() is True
        assert watcher.refresh_if_stale() is False

    def test_missing_file_does_not_raise(self, project_function: Project) -> None:
        """A transiently absent file must not take the server down."""
        watcher = ProjectWatcher(project_function)
        project_function.meltanofile.unlink()
        # Reports a change (the stamp went to None) rather than raising.
        assert watcher.refresh_if_stale() is True


class TestEndpointsSeeExternalEdits:
    """The behaviour this exists for, exercised through HTTP."""

    @pytest.mark.usefixtures("tap")
    def test_plugin_added_outside_the_server_appears(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """A plugin written to `meltano.yml` shows up without a restart."""
        before = {plugin["name"] for plugin in ui_client.get("/api/v1/plugins").json()}
        assert "tap-mock" in before

        # Simulate an edit made outside the server: write `meltano.yml`
        # directly, which is what `meltano add` in another terminal amounts to
        # as far as this process can observe.
        document = yaml.safe_load(project.meltanofile.read_text())
        document["plugins"]["extractors"].append(
            {
                "name": "tap-added-externally",
                "namespace": "tap_added_externally",
                "pip_url": "tap-added-externally",
            },
        )
        project.meltanofile.write_text(yaml.safe_dump(document))

        after = {plugin["name"] for plugin in ui_client.get("/api/v1/plugins").json()}
        assert "tap-added-externally" in after
