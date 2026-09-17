"""Tests for saving step lists as named mappings."""

from __future__ import annotations

import contextlib
import typing as t

import pytest

from meltano.core.plugin import PluginType
from meltano.core.plugin.project_plugin import ProjectPlugin
from meltano.core.project_plugins_service import PluginAlreadyAddedException

if t.TYPE_CHECKING:
    from collections.abc import Iterator

    from fastapi.testclient import TestClient

    from meltano.core.project import Project

MAPPINGS = "/api/v1/mappings"

STEPS = [
    {"kind": "drop", "column": "email"},
    {"kind": "rename", "column": "name", "to": "label"},
]


def mapper_entries(project: Project, name: str) -> list[dict]:
    """Read a mapper's stored mappings out of `meltano.yml` itself.

    Read from the file rather than through `project.plugins`, which caches:
    an assertion against the cache can pass while the file says otherwise, and
    it is the file these endpoints exist to write.

    Args:
        project: The test project.
        name: The mapper's name.

    Returns:
        The mapping entries.
    """
    import yaml

    document = yaml.safe_load((project.root / "meltano.yml").read_text()) or {}
    mappers = (document.get("plugins") or {}).get("mappers") or []
    for entry in mappers:
        if entry.get("name") == name:
            return list(entry.get("mappings") or [])
    return []


@pytest.fixture
def mapper(project: Project) -> Iterator[ProjectPlugin]:
    """Add a mapper to the project, and take it away afterwards.

    The `project` fixture is class-scoped, so a mapper left behind would be
    visible to whatever runs next.

    Args:
        project: The test project.

    Yields:
        The mapper plugin.
    """
    plugin = ProjectPlugin(
        PluginType.MAPPERS,
        "test-mapper",
        namespace="test_mapper",
        executable="test-mapper",
    )
    # Added once per class-scoped project and then left in place. Adding and
    # removing it per test fought the plugin cache: a mapper removed from the
    # file was still reported as present by the next `add_to_file`.
    with contextlib.suppress(PluginAlreadyAddedException):
        project.plugins.add_to_file(plugin)

    _clear_mappings(project)
    try:
        yield plugin
    finally:
        _clear_mappings(project)


def _clear_mappings(project: Project) -> None:
    """Empty the test mapper's mappings.

    Resetting what the mapper carries is enough isolation, and avoids removing
    and re-adding the plugin itself between every test.

    Args:
        project: The test project.
    """
    with project.config_service.update_meltano_yml() as meltano_yml:
        for plugin in meltano_yml["plugins"]["mappers"]:
            if plugin.name == "test-mapper" and not plugin.is_mapping():
                plugin.extras["mappings"] = []
                return


@pytest.mark.usefixtures("mapper")
class TestSaveMapping:
    """Turning a step list into something a pipeline can run."""

    def test_saving_writes_a_mapping_to_the_project(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """The compiled stream map lands under the mapper, keyed by stream."""
        response = ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        assert response.status_code == 201, response.text

        entries = mapper_entries(project, "test-mapper")
        assert [entry["name"] for entry in entries] == ["tidy"]
        assert entries[0]["config"]["stream_maps"]["customers"] == {
            "email": None,
            "label": "record['name']",
            "name": None,
        }

    def test_a_saved_mapping_resolves_as_a_run_block(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """`meltano run tap tidy target` has to find it by name.

        That resolution is the entire point of saving, and it goes through
        Meltano's own lookup rather than anything this server controls.
        """
        ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        found = project.plugins.find_plugins_by_mapping_name("tidy")

        assert [plugin.is_mapping() for plugin in found] == [True]
        assert found[0].config["stream_maps"]["customers"]["email"] is None

    def test_saving_does_not_add_a_mapper(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """Meltano expands each mapping into a synthetic mapper plugin.

        Writing those back would quietly add mappers to the project, so the
        real ones are filtered by `is_mapping()` before anything is stored.
        """
        ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        real = [
            plugin
            for plugin in project.plugins.get_plugins_of_type(PluginType.MAPPERS)
            if not plugin.is_mapping()
        ]

        assert [plugin.name for plugin in real] == ["test-mapper"]

    def test_a_duplicate_name_is_refused(self, ui_client: TestClient) -> None:
        """A mapping is addressed by name, so names cannot collide."""
        body = {"name": "tidy", "stream": "customers", "steps": STEPS}
        ui_client.post(MAPPINGS, json=body)

        assert ui_client.post(MAPPINGS, json=body).status_code == 409

    def test_overwrite_replaces_in_place(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """Re-saving updates the mapping rather than appending a second one."""
        ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        response = ui_client.post(
            MAPPINGS,
            json={
                "name": "tidy",
                "stream": "orders",
                "steps": [{"kind": "drop", "column": "secret"}],
                "overwrite": True,
            },
        )

        assert response.status_code == 201
        entries = mapper_entries(project, "test-mapper")
        assert len(entries) == 1
        assert entries[0]["config"]["stream_maps"] == {"orders": {"secret": None}}

    def test_malformed_steps_are_refused_before_writing(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """Nothing reaches the project file if the steps do not compile."""
        response = ui_client.post(
            MAPPINGS,
            json={
                "name": "broken",
                "stream": "customers",
                "steps": [{"kind": "rename", "column": "a"}],
            },
        )

        assert response.status_code == 422
        assert mapper_entries(project, "test-mapper") == []

    def test_naming_an_unknown_mapper_is_refused(
        self,
        ui_client: TestClient,
    ) -> None:
        """Better than silently storing it somewhere else."""
        response = ui_client.post(
            MAPPINGS,
            json={
                "name": "tidy",
                "stream": "customers",
                "steps": STEPS,
                "mapper": "nope",
            },
        )

        assert response.status_code == 422
        assert "No mapper named" in response.json()["detail"]


class TestWithoutAMapper:
    """What happens in a project that has no mapper at all."""

    def test_saving_explains_what_is_missing(self, ui_client: TestClient) -> None:
        """A mapping has nowhere to live without a mapper plugin.

        The message names the plugin to add rather than leaving the user to
        work out that mappings are not free-standing.
        """
        response = ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        assert response.status_code == 422
        assert "meltano-map-transformer" in response.json()["detail"]

    def test_listing_is_empty_rather_than_an_error(
        self,
        ui_client: TestClient,
    ) -> None:
        """No mapper simply means no mappings."""
        assert ui_client.get(MAPPINGS).json() == []


@pytest.mark.usefixtures("mapper")
class TestListAndDelete:
    """Reading and removing what has been saved."""

    def test_listing_reports_streams(
        self,
        ui_client: TestClient,
    ) -> None:
        """Which streams a mapping touches is what identifies it at a glance."""
        ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        body = ui_client.get(MAPPINGS).json()

        assert len(body) == 1
        assert body[0]["name"] == "tidy"
        assert body[0]["mapper"] == "test-mapper"
        assert body[0]["streams"] == ["customers"]

    def test_delete_removes_it(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """A deleted mapping is gone from the project file."""
        ui_client.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )

        assert ui_client.delete(f"{MAPPINGS}/tidy").status_code == 204
        assert mapper_entries(project, "test-mapper") == []

    def test_deleting_an_unknown_mapping_is_404(
        self,
        ui_client: TestClient,
    ) -> None:
        """Nothing to remove is a 404, not a silent success."""
        assert ui_client.delete(f"{MAPPINGS}/nope").status_code == 404


class TestMappingsAuth:
    """Admission control applies here like anywhere else."""

    def test_anonymous_save_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """An unauthenticated caller cannot write to the project."""
        response = ui_client_anonymous.post(
            MAPPINGS,
            json={"name": "tidy", "stream": "customers", "steps": STEPS},
        )
        assert response.status_code == 401
