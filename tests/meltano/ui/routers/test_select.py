"""Tests for the extractor selection endpoints."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.core.plugin.error import PluginExecutionError
from meltano.core.plugin.singer.catalog import (
    ListSelectedExecutor,
    SelectedNode,
    SelectionType,
)
from meltano.core.select_service import SelectService

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project


def select_url(plugin: ProjectPlugin) -> str:
    """Build the select URL for a plugin.

    Args:
        plugin: The plugin to address.

    Returns:
        The URL, without a trailing slash.
    """
    return f"/api/v1/plugins/{plugin.type}/{plugin.name}/select"


def current(project: Project, tap: ProjectPlugin) -> list[str]:
    """Read an extractor's patterns through a freshly built service.

    `SelectService` caches the `ProjectPlugin` it was constructed with, so a
    service held across a write keeps reporting the pre-write patterns. Tests
    assert through this rather than through the fixture's own service.

    Args:
        project: The test project.
        tap: The extractor to read.

    Returns:
        The patterns in effect.
    """
    return SelectService(project, tap.name).current_select


@pytest.fixture
def selecting(project: Project, tap: ProjectPlugin) -> t.Iterator[SelectService]:
    """Yield a select service over an extractor with no patterns.

    The `project` fixture is class-scoped, so a pattern left behind would be
    visible to the next test under `pytest-randomly`'s shuffling.

    Args:
        project: The test project.
        tap: The extractor under test.

    Yields:
        The service.
    """
    service = SelectService(project, tap.name)
    service.clear()
    try:
        yield service
    finally:
        service.clear()


def fake_catalog() -> ListSelectedExecutor:
    """Build a listed catalog without running an extractor.

    Discovery invokes the tap against a real source, which a unit test cannot
    do; the endpoint's own logic is the part worth covering.

    Returns:
        A populated executor.
    """
    listed = ListSelectedExecutor()
    listed.streams = {
        SelectedNode(key="orders", selection=SelectionType.SELECTED),
        SelectedNode(key="secrets", selection=SelectionType.EXCLUDED),
    }
    listed.properties = {
        "orders": {
            SelectedNode(key="id", selection=SelectionType.AUTOMATIC),
            SelectedNode(key="total", selection=SelectionType.SELECTED),
        },
        "secrets": {SelectedNode(key="token", selection=SelectionType.SELECTED)},
    }
    return listed


@pytest.mark.usefixtures("tap")
class TestSelectPatterns:
    """Reading and writing the globs stored in `meltano.yml`."""

    def test_get_reads_without_running_the_extractor(
        self,
        ui_client: TestClient,
        selecting: SelectService,
        tap: ProjectPlugin,
    ) -> None:
        """Patterns come from the project file, never from discovery.

        The whole point of splitting this from the catalog endpoint is that it
        answers even when the extractor cannot run.
        """
        selecting.update("orders", "*", False)  # noqa: FBT003

        with mock.patch.object(
            SelectService,
            "list_all",
            side_effect=AssertionError("discovery must not run"),
        ):
            response = ui_client.get(select_url(tap))

        assert response.status_code == 200
        assert "orders.*" in [p["raw"] for p in response.json()["patterns"]]

    def test_add_writes_the_pattern(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """A posted pattern is stored, not merely echoed back."""
        response = ui_client.post(
            select_url(tap),
            json={"streams": "orders", "properties": "total"},
        )

        assert response.status_code == 201
        assert "orders.total" in current(project, tap)

    def test_add_can_exclude(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """An exclusion is stored with the leading '!' core expects."""
        ui_client.post(
            select_url(tap),
            json={"streams": "secrets", "properties": "*", "exclude": True},
        )

        assert "!secrets.*" in current(project, tap)

    def test_patterns_are_parsed_for_the_client(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """The two halves are reported separately, negation included."""
        ui_client.post(
            select_url(tap),
            json={"streams": "secrets", "properties": "token", "exclude": True},
        )

        body = ui_client.get(select_url(tap)).json()

        entry = next(p for p in body["patterns"] if p["raw"] == "!secrets.token")
        assert entry["stream_pattern"] == "secrets"
        assert entry["property_pattern"] == "token"
        assert entry["negated"] is True

    def test_remove_deletes_one_pattern(
        self,
        ui_client: TestClient,
        selecting: SelectService,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """Removing addresses a pattern by its raw text and leaves the rest."""
        selecting.update("orders", "*", False)  # noqa: FBT003
        selecting.update("customers", "*", False)  # noqa: FBT003

        response = ui_client.delete(f"{select_url(tap)}/orders.*")

        assert response.status_code == 200
        remaining = current(project, tap)
        assert "orders.*" not in remaining
        assert "customers.*" in remaining

    def test_remove_handles_an_exclusion(
        self,
        ui_client: TestClient,
        selecting: SelectService,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """A negated pattern round-trips through the URL."""
        selecting.update("secrets", "*", True)  # noqa: FBT003

        response = ui_client.delete(f"{select_url(tap)}/!secrets.*")

        assert response.status_code == 200
        assert "!secrets.*" not in current(project, tap)

    def test_removing_an_absent_pattern_is_404(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """Core raises `ValueError` here; that is a 404, not a 500."""
        response = ui_client.delete(f"{select_url(tap)}/never.added")
        assert response.status_code == 404

    def test_clear_removes_every_pattern(
        self,
        ui_client: TestClient,
        selecting: SelectService,
        project: Project,
        tap: ProjectPlugin,
    ) -> None:
        """Clearing resets to Meltano's default rather than selecting nothing."""
        selecting.update("orders", "*", False)  # noqa: FBT003

        response = ui_client.delete(select_url(tap))

        assert response.status_code == 200
        assert "orders.*" not in current(project, tap)

    def test_a_loader_cannot_select(
        self,
        ui_client: TestClient,
        target: ProjectPlugin,
    ) -> None:
        """Only extractors have a catalog to select from."""
        response = ui_client.get(select_url(target))

        assert response.status_code == 422
        assert "only extractors select" in response.json()["detail"]

    def test_an_unknown_plugin_is_404(self, ui_client: TestClient) -> None:
        """A nonexistent extractor is a 404, from the shared resolver."""
        response = ui_client.get("/api/v1/plugins/extractors/nope/select")
        assert response.status_code == 404


@pytest.mark.usefixtures("tap")
class TestSelectCatalog:
    """Reading what the extractor says it can produce."""

    def test_catalog_reports_streams_and_properties(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """Streams and their properties come back sorted and annotated."""
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(return_value=fake_catalog()),
        ):
            body = ui_client.get(f"{select_url(tap)}/catalog").json()

        assert [stream["name"] for stream in body["streams"]] == ["orders", "secrets"]
        orders = body["streams"][0]
        assert [prop["name"] for prop in orders["properties"]] == ["id", "total"]

    def test_property_selection_is_combined_with_its_stream(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """A selected property in an excluded stream is not selected.

        The CLI combines the two the same way; reporting the property's own
        mark alone would tell the user the opposite of what will happen.
        """
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(return_value=fake_catalog()),
        ):
            body = ui_client.get(f"{select_url(tap)}/catalog").json()

        secrets = next(s for s in body["streams"] if s["name"] == "secrets")
        assert secrets["properties"][0]["name"] == "token"
        assert secrets["properties"][0]["selection"] == str(SelectionType.EXCLUDED)

    def test_catalog_carries_the_selection_legend(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """Every possible selection value is listed, for the UI's legend."""
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(return_value=fake_catalog()),
        ):
            body = ui_client.get(f"{select_url(tap)}/catalog").json()

        assert set(body["selection_types"]) == {str(v) for v in SelectionType}

    def test_refresh_is_passed_through(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """`refresh` must reach core, or the cached catalog is never bypassed."""
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(return_value=fake_catalog()),
        ) as list_all:
            ui_client.get(f"{select_url(tap)}/catalog", params={"refresh": "true"})

        assert list_all.await_args.kwargs["refresh"] is True

    def test_a_failing_extractor_is_a_400(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """Discovery failing is the extractor's problem, not a server error.

        It usually means the plugin is not installed, not configured, or does
        not support discovery, so the message has to survive to the client.
        """
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(
                side_effect=PluginExecutionError("does not support discovery"),
            ),
        ):
            response = ui_client.get(f"{select_url(tap)}/catalog")

        assert response.status_code == 400
        assert "does not support discovery" in response.json()["detail"]

    def test_a_hanging_extractor_times_out(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """A tap that never answers must not hold the request open forever."""
        with (
            mock.patch(
                "meltano.ui.routers.select._CATALOG_TIMEOUT_SECONDS",
                0.05,
            ),
            mock.patch.object(
                SelectService,
                "list_all",
                new=mock.AsyncMock(side_effect=_never),
            ),
        ):
            response = ui_client.get(f"{select_url(tap)}/catalog")

        assert response.status_code == 504
        assert "did not describe itself" in response.json()["detail"]


async def _never(*_args: object, **_kwargs: object) -> t.NoReturn:
    """Sleep longer than any test would wait.

    Args:
        _args: Ignored.
        _kwargs: Ignored.

    Raises:
        AssertionError: If the sleep is ever allowed to finish.
    """
    import anyio

    await anyio.sleep(30)
    msg = "should have been cancelled"
    raise AssertionError(msg)


class TestSelectAuth:
    """Admission control applies to these endpoints like any other."""

    def test_anonymous_read_is_refused(
        self,
        ui_client_anonymous: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Without a token, selection is not readable."""
        assert ui_client_anonymous.get(select_url(tap)).status_code == 401


@pytest.mark.usefixtures("tap")
class TestRemovablePatterns:
    """Telling a declared pattern from Meltano's default."""

    def test_the_default_pattern_is_not_removable(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """With nothing declared, `*.*` is reported but cannot be deleted.

        `current_select` returns the effective setting, which includes the
        default. Offering a delete control for it would produce a 404 on a
        pattern the user can see.
        """
        body = ui_client.get(select_url(tap)).json()

        assert [p["raw"] for p in body["patterns"]] == ["*.*"]
        assert body["patterns"][0]["removable"] is False

    def test_a_declared_pattern_is_removable(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """A pattern written by this API is marked as deletable."""
        ui_client.post(select_url(tap), json={"streams": "orders", "properties": "*"})

        body = ui_client.get(select_url(tap)).json()

        entry = next(p for p in body["patterns"] if p["raw"] == "orders.*")
        assert entry["removable"] is True

    def test_removability_survives_to_the_catalog(
        self,
        ui_client: TestClient,
        selecting: SelectService,  # noqa: ARG002
        tap: ProjectPlugin,
    ) -> None:
        """The catalog reports patterns the same way the list endpoint does."""
        with mock.patch.object(
            SelectService,
            "list_all",
            new=mock.AsyncMock(return_value=fake_catalog()),
        ):
            body = ui_client.get(f"{select_url(tap)}/catalog").json()

        assert all(p["removable"] is False for p in body["patterns"])
