"""Tests for the `.source` documents downstream readers consume."""

from __future__ import annotations

import json
import typing as t

import pytest

from meltano.core.select_service import SelectService
from meltano.core.settings_service import REDACTED_VALUE
from meltano.ui.schemas.sources import SOURCE_SCHEMA_VERSION

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project

SOURCES = "/api/v1/sources"

#: A value that would be unmistakable if it ever reached a document.
SECRET = "hunter2-should-never-be-written"  # noqa: S105


@pytest.mark.usefixtures("tap", "target")
class TestSourceDocuments:
    """What a `.source` says about a configured connector."""

    def test_lists_extractors_and_loaders(self, ui_client: TestClient) -> None:
        """Both halves of a pipeline are connectors a reader may need."""
        body = ui_client.get(SOURCES).json()

        types = {doc["type"] for doc in body}
        assert types <= {"extractors", "loaders"}
        assert "extractors" in types

    def test_a_document_carries_its_schema_version(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A reader can refuse a document it does not understand.

        Without this a format change would be discovered by misreading the
        file rather than by rejecting it.
        """
        body = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").json()

        assert body["schema_version"] == SOURCE_SCHEMA_VERSION
        assert body["meltano_version"]

    def test_a_document_describes_how_to_run_the_connector(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Name, variant and namespace are what identify it downstream."""
        body = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").json()

        assert body["name"] == tap.name
        assert body["type"] == "extractors"
        assert body["namespace"] == tap.namespace
        assert isinstance(body["settings"], list)

    def test_extractors_carry_their_select_patterns(
        self,
        ui_client: TestClient,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """Selection is part of what a connector *is*, not a runtime flag."""
        service = SelectService(project, tap.name)
        service.update("orders", "*", False)  # noqa: FBT003

        try:
            body = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").json()
            assert "orders.*" in body["select"]
        finally:
            service.clear()

    def test_loaders_carry_no_select(
        self,
        ui_client: TestClient,
        target: ProjectPlugin,
    ) -> None:
        """Only extractors select; an empty list is not an oversight."""
        body = ui_client.get(f"{SOURCES}/{target.type}/{target.name}").json()

        assert body["select"] == []

    def test_meltano_extras_are_excluded(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """`_select`, `_metadata` and friends configure Meltano, not the tap.

        A reader running the connector itself has no use for them, and
        `_select` would contradict the document's own `select` field.
        """
        body = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").json()

        assert not [s for s in body["settings"] if s["name"].startswith("_")]

    def test_an_unknown_connector_is_404(self, ui_client: TestClient) -> None:
        """Resolution is shared with the config endpoints."""
        assert ui_client.get(f"{SOURCES}/extractors/nope").status_code == 404


@pytest.mark.usefixtures("target")
class TestSecretsAreNeverWritten:
    """The guarantee that makes a `.source` safe to commit."""

    def test_a_sensitive_value_is_absent(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A configured secret is reported as set, but never with its value.

        This is the one property of the format that cannot regress quietly: a
        `.source` is a file, often a committed one.
        """
        settings = ui_client.get(
            f"/api/v1/plugins/{tap.type}/{tap.name}/config",
        ).json()["settings"]
        sensitive = next((s for s in settings if s["sensitive"]), None)
        if sensitive is None:
            pytest.skip("the fixture extractor declares no sensitive setting")

        ui_client.put(
            f"/api/v1/plugins/{tap.type}/{tap.name}/config/{sensitive['name']}",
            json={"value": SECRET},
        )

        body = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").json()

        entry = next(s for s in body["settings"] if s["name"] == sensitive["name"])
        assert entry["sensitive"] is True
        assert entry["value"] is None
        assert entry["is_set"] is True
        # The env var is what a reader injects the real value through.
        assert entry["env"]

    def test_no_secret_appears_anywhere_in_the_document(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Checked against the serialized document, not field by field.

        A field-by-field assertion would miss a secret that leaked into some
        other part of the structure.
        """
        settings = ui_client.get(
            f"/api/v1/plugins/{tap.type}/{tap.name}/config",
        ).json()["settings"]
        sensitive = next((s for s in settings if s["sensitive"]), None)
        if sensitive is None:
            pytest.skip("the fixture extractor declares no sensitive setting")

        ui_client.put(
            f"/api/v1/plugins/{tap.type}/{tap.name}/config/{sensitive['name']}",
            json={"value": SECRET},
        )

        raw = ui_client.get(f"{SOURCES}/{tap.type}/{tap.name}").text

        assert SECRET not in raw
        assert REDACTED_VALUE not in raw


@pytest.mark.usefixtures("tap", "target")
class TestExport:
    """Writing `.source` files for another system to read."""

    def test_export_writes_one_file_per_connector(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """The files land where the request asked, named for the connector."""
        response = ui_client.post(f"{SOURCES}/export", json={"path": "sources"})

        assert response.status_code == 201
        body = response.json()
        assert body["written"]

        for entry in body["written"]:
            path = project.root / "sources" / f"{entry['name']}.source"
            assert path.is_file()
            document = json.loads(path.read_text())
            assert document["name"] == entry["name"]
            assert document["schema_version"] == SOURCE_SCHEMA_VERSION

    def test_export_writes_valid_json(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """A reader parses these; a trailing newline and valid JSON matter."""
        ui_client.post(f"{SOURCES}/export", json={"path": "sources"})

        written = sorted((project.root / "sources").glob("*.source"))
        assert written
        for path in written:
            text = path.read_text()
            assert text.endswith("\n")
            json.loads(text)

    def test_export_can_be_narrowed_to_one_type(
        self,
        ui_client: TestClient,
    ) -> None:
        """Exporting only extractors is a reasonable thing to want."""
        body = ui_client.post(
            f"{SOURCES}/export",
            json={"path": "sources-taps", "plugin_types": ["extractors"]},
        ).json()

        assert {entry["type"] for entry in body["written"]} == {"extractors"}

    def test_export_rejects_an_unknown_type(self, ui_client: TestClient) -> None:
        """A typo should not silently export nothing."""
        response = ui_client.post(
            f"{SOURCES}/export",
            json={"path": "sources", "plugin_types": ["nonsense"]},
        )

        assert response.status_code == 422

    def test_export_is_repeatable(
        self,
        ui_client: TestClient,
        project: Project,
    ) -> None:
        """Re-exporting overwrites rather than accumulating or failing."""
        first = ui_client.post(f"{SOURCES}/export", json={"path": "sources"}).json()
        second = ui_client.post(f"{SOURCES}/export", json={"path": "sources"}).json()

        assert first["written"] == second["written"]
        files = list((project.root / "sources").glob("*.source"))
        assert len(files) == len(second["written"])


class TestSourcesAuth:
    """Admission control applies here like anywhere else."""

    def test_anonymous_read_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Connector configs are not public, even without secrets in them."""
        assert ui_client_anonymous.get(SOURCES).status_code == 401

    def test_anonymous_export_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Nor can an unauthenticated caller write files into the project."""
        response = ui_client_anonymous.post(f"{SOURCES}/export", json={})
        assert response.status_code == 401


@pytest.fixture(autouse=True)
def _clean_exports(project: Project) -> t.Iterator[None]:
    """Remove exported files afterwards.

    The `project` fixture is class-scoped, so directories written by one test
    would otherwise be counted by the next.

    Args:
        project: The test project.

    Yields:
        None.
    """
    import shutil

    yield
    for name in ("sources", "sources-taps"):
        shutil.rmtree(project.root / name, ignore_errors=True)
