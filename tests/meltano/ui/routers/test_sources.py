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


@pytest.fixture
def database_target(project: Project) -> t.Iterator[ProjectPlugin]:
    """Declare a loader configured against a database.

    Declared here rather than configuring `target-mock`, which has no host or
    port among its settings: setting one raises `Unknown setting`, and this
    suite treats warnings as errors.

    Args:
        project: The test project.

    Yields:
        The loader.
    """
    from meltano.core.plugin import PluginType
    from meltano.core.plugin.project_plugin import ProjectPlugin as Plugin

    plugin = Plugin(
        PluginType.LOADERS,
        "target-warehouse",
        namespace="target_postgres",
        executable="target-warehouse",
        settings=[
            {"name": "host"},
            {"name": "port", "kind": "integer"},
            {"name": "database"},
            {"name": "sqlalchemy_url"},
        ],
        config={
            "host": "warehouse.internal",
            "port": 5432,
            "database": "analytics",
        },
    )
    project.plugins.add_to_file(plugin)
    try:
        yield plugin
    finally:
        # The `project` fixture is class-scoped, so this would otherwise be
        # visible to whatever runs next.
        project.plugins.remove_from_file(plugin)


class TestConnectionEndpoint:
    """The database a connector points at.

    This is what lets a reader register a source without knowing which of a
    connector's settings happen to mean "host" - the whole reason a `.source`
    is worth writing rather than shipping `meltano.yml`.
    """

    def test_a_database_connector_reports_its_endpoint(
        self,
        ui_client: TestClient,
        database_target: ProjectPlugin,
    ) -> None:
        """Host, port and database come back as an endpoint, not as settings."""
        body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()

        assert body["connection"]["host"] == "warehouse.internal"
        assert body["connection"]["port"] == 5432
        assert body["connection"]["database"] == "analytics"

    def test_the_port_is_a_number(
        self,
        ui_client: TestClient,
        database_target: ProjectPlugin,
    ) -> None:
        """A port set as text is still a port.

        Values arrive as strings or integers depending on whether they came
        from `meltano.yml` or an environment variable, and a reader dialling
        `"5432"` is a bug waiting to happen.
        """
        body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()

        assert body["connection"]["port"] == 5432
        assert isinstance(body["connection"]["port"], int)

    def test_the_derivation_is_recorded(
        self,
        ui_client: TestClient,
        database_target: ProjectPlugin,
    ) -> None:
        """Which setting supplied each field is stated, not implied.

        The mapping from settings to an endpoint is convention rather than
        declaration, so a reader that disagrees can see what was used instead
        of guessing why a host is wrong.
        """
        body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()

        assert body["connection"]["derived_from"] == {
            "host": "host",
            "port": "port",
            "database": "database",
        }

    @pytest.mark.usefixtures("target")
    def test_a_connector_with_no_host_reports_none(
        self,
        ui_client: TestClient,
        target: ProjectPlugin,
    ) -> None:
        """An unconfigured connector has no endpoint to report.

        Null is the honest answer. Inventing `localhost` would send a reader
        to the wrong machine, which is worse than telling it nothing.
        """
        body = ui_client.get(f"{SOURCES}/loaders/{target.name}").json()

        assert body["connection"] is None

    def test_the_dialect_comes_from_the_namespace(
        self,
        ui_client: TestClient,
        database_target: ProjectPlugin,
    ) -> None:
        """`target_postgres` and `tap_postgres` describe the same engine."""
        body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()

        assert body["connection"]["engine"] == "postgres"

    def test_credentials_are_not_part_of_the_endpoint(
        self,
        ui_client: TestClient,
        database_target: ProjectPlugin,
    ) -> None:
        """The endpoint is an address, never a login."""
        connection = ui_client.get(
            f"{SOURCES}/loaders/{database_target.name}",
        ).json()["connection"]

        assert set(connection) == {
            "engine",
            "host",
            "port",
            "database",
            "derived_from",
        }


class TestConnectionUrlsAreNotLeaked:
    """A URL setting can carry a password Meltano does not call sensitive."""

    def test_a_url_with_credentials_is_withheld(
        self,
        ui_client: TestClient,
        project: Project,
        database_target: ProjectPlugin,
    ) -> None:
        """`postgresql://user:pass@host/db` must not reach a committed file.

        Meltano does not mark a connection URL sensitive, and it is right not
        to - a URL without credentials is not a secret. One *with* them is,
        and a `.source` is written to disk and frequently committed.
        """
        from meltano.core.plugin.settings_service import PluginSettingsService

        service = PluginSettingsService(project, database_target)
        service.set("sqlalchemy_url", f"postgresql://admin:{SECRET}@db.internal/x")

        try:
            body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()
        finally:
            service.unset("sqlalchemy_url")

        assert SECRET not in json.dumps(body)
        url = next(s for s in body["settings"] if s["name"] == "sqlalchemy_url")
        assert url["value"] is None

    def test_a_url_without_credentials_is_kept(
        self,
        ui_client: TestClient,
        project: Project,
        database_target: ProjectPlugin,
    ) -> None:
        """Withholding every URL would throw away the useful case."""
        from meltano.core.plugin.settings_service import PluginSettingsService

        service = PluginSettingsService(project, database_target)
        service.set("sqlalchemy_url", "postgresql://db.internal:5432/analytics")

        try:
            body = ui_client.get(f"{SOURCES}/loaders/{database_target.name}").json()
        finally:
            service.unset("sqlalchemy_url")

        url = next(s for s in body["settings"] if s["name"] == "sqlalchemy_url")
        assert url["value"] == "postgresql://db.internal:5432/analytics"
