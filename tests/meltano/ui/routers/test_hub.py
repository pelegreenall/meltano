"""Tests for browsing Meltano Hub and adding plugins to the project."""

from __future__ import annotations

import typing as t
from unittest import mock

from meltano.core.hub.client import HubConnectionError, MeltanoHubService
from meltano.core.hub.schema import IndexedPlugin, VariantRef
from meltano.core.project_add_service import ProjectAddService
from meltano.core.project_plugins_service import PluginAlreadyAddedException
from tests.meltano.ui.routers.test_runs import make_record

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.ui.context import AppContext

HUB = "/api/v1/hub"
PLUGINS = "/api/v1/plugins"


def indexed(name: str, *, default: str = "acme", others: tuple[str, ...] = ()) -> dict:
    """Build a Hub index entry.

    Args:
        name: The plugin's name.
        default: The default variant's name.
        others: Additional variant names.

    Returns:
        A single-entry mapping shaped like `get_plugins_of_type` returns.
    """
    variants = {default: VariantRef(default, ref=f"https://hub/{name}/{default}")}
    for variant in others:
        variants[variant] = VariantRef(variant, ref=f"https://hub/{name}/{variant}")
    return {
        name: IndexedPlugin(
            name,
            logo_url=f"https://hub/{name}.png",
            default_variant=default,
            variants=variants,
        ),
    }


class TestBrowseHub:
    """Listing what Hub offers."""

    def test_lists_plugins_of_a_type(self, ui_client: TestClient) -> None:
        """Each entry carries its variants and which one is the default."""
        with mock.patch.object(
            MeltanoHubService,
            "get_plugins_of_type",
            return_value=indexed(
                "tap-github", default="singer-io", others=("meltano",)
            ),
        ):
            body = ui_client.get(f"{HUB}/extractors").json()

        assert [entry["name"] for entry in body] == ["tap-github"]
        entry = body[0]
        assert entry["default_variant"] == "singer-io"
        assert {v["name"]: v["is_default"] for v in entry["variants"]} == {
            "singer-io": True,
            "meltano": False,
        }

    def test_marks_plugins_already_in_the_project(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """`is_added` is what stops the UI offering to add a duplicate."""
        with mock.patch.object(
            MeltanoHubService,
            "get_plugins_of_type",
            return_value={**indexed("tap-github"), **indexed(tap.name)},
        ):
            body = ui_client.get(f"{HUB}/extractors").json()

        added = {entry["name"]: entry["is_added"] for entry in body}
        assert added[tap.name] is True
        assert added["tap-github"] is False

    def test_filters_by_name(self, ui_client: TestClient) -> None:
        """Hub lists hundreds of plugins, so the filter is not a nicety."""
        with mock.patch.object(
            MeltanoHubService,
            "get_plugins_of_type",
            return_value={**indexed("tap-github"), **indexed("target-jsonl")},
        ):
            body = ui_client.get(f"{HUB}/extractors", params={"q": "GITHUB"}).json()

        assert [entry["name"] for entry in body] == ["tap-github"]

    def test_unreachable_hub_is_a_502(self, ui_client: TestClient) -> None:
        """Hub being down says nothing about this project's health.

        Reporting it as a server error would send the user looking in the
        wrong place.
        """
        with mock.patch.object(
            MeltanoHubService,
            "get_plugins_of_type",
            side_effect=HubConnectionError("nope"),
        ):
            response = ui_client.get(f"{HUB}/extractors")

        assert response.status_code == 502
        assert "Meltano Hub" in response.json()["detail"]

    def test_an_unknown_plugin_type_is_404(self, ui_client: TestClient) -> None:
        """A type that does not exist is a 404."""
        assert ui_client.get(f"{HUB}/nonsense").status_code == 404

    def test_a_non_discoverable_type_is_422(self, ui_client: TestClient) -> None:
        """Mappings are defined in the project; Hub does not list them."""
        response = ui_client.get(f"{HUB}/mappings")

        assert response.status_code == 422
        assert "not listed on Hub" in response.json()["detail"]

    def test_anonymous_browsing_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Admission control applies here like anywhere else."""
        assert ui_client_anonymous.get(f"{HUB}/extractors").status_code == 401


class TestAddPlugin:
    """Adding a Hub plugin to the project."""

    def test_add_declares_the_plugin(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """The plugin is added through core, not written by this layer."""
        added = mock.Mock(name="tap-github", type="extractors")
        added.name = "tap-github"
        added.variant = "singer-io"
        added.pip_url = "tap-github"

        with (
            mock.patch.object(ProjectAddService, "add", return_value=added) as add,
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(return_value=make_record()),
            ),
        ):
            response = ui_client.post(
                PLUGINS,
                json={"plugin_type": "extractors", "name": "tap-github"},
            )

        assert response.status_code == 201
        assert add.call_args.args[1] == "tap-github"

    def test_add_installs_by_default(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """`meltano add` installs, and so does this.

        The install is supervised rather than awaited, so the response carries
        a run to follow instead of blocking until pip finishes.
        """
        added = mock.Mock()
        added.name = "tap-github"
        added.type = "extractors"
        added.variant = None
        added.pip_url = None

        with (
            mock.patch.object(ProjectAddService, "add", return_value=added),
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(return_value=make_record()),
            ) as start,
        ):
            body = ui_client.post(
                PLUGINS,
                json={"plugin_type": "extractors", "name": "tap-github"},
            ).json()

        assert start.await_count == 1
        assert start.await_args.kwargs["kind"] is not None
        assert body["run_id"]

    def test_add_can_skip_installing(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """Declaring without installing is a supported half-step."""
        added = mock.Mock()
        added.name = "tap-github"
        added.type = "extractors"
        added.variant = None
        added.pip_url = None

        with (
            mock.patch.object(ProjectAddService, "add", return_value=added),
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(return_value=make_record()),
            ) as start,
        ):
            body = ui_client.post(
                PLUGINS,
                json={
                    "plugin_type": "extractors",
                    "name": "tap-github",
                    "install": False,
                },
            ).json()

        assert start.await_count == 0
        assert body["run_id"] is None

    def test_add_passes_the_chosen_variant(
        self,
        ui_client: TestClient,
        ui_context: AppContext,
    ) -> None:
        """A named variant reaches core; omitting it leaves Hub's default."""
        added = mock.Mock()
        added.name = "tap-github"
        added.type = "extractors"
        added.variant = "meltano"
        added.pip_url = None

        with (
            mock.patch.object(ProjectAddService, "add", return_value=added) as add,
            mock.patch.object(
                ui_context.run_manager,
                "start",
                new=mock.AsyncMock(return_value=make_record()),
            ),
        ):
            ui_client.post(
                PLUGINS,
                json={
                    "plugin_type": "extractors",
                    "name": "tap-github",
                    "variant": "meltano",
                },
            )

        assert add.call_args.kwargs["variant"] == "meltano"

    def test_adding_a_duplicate_is_409(self, ui_client: TestClient) -> None:
        """Re-adding is a conflict, not a silent overwrite."""
        with mock.patch.object(
            ProjectAddService,
            "add",
            side_effect=PluginAlreadyAddedException(mock.Mock(), mock.Mock()),
        ):
            response = ui_client.post(
                PLUGINS,
                json={"plugin_type": "extractors", "name": "tap-github"},
            )

        assert response.status_code == 409

    def test_add_reports_an_unreachable_hub(self, ui_client: TestClient) -> None:
        """The definition comes from Hub, so adding can fail the same way."""
        with mock.patch.object(
            ProjectAddService,
            "add",
            side_effect=HubConnectionError("nope"),
        ):
            response = ui_client.post(
                PLUGINS,
                json={"plugin_type": "extractors", "name": "tap-github"},
            )

        assert response.status_code == 502

    def test_anonymous_add_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """An unauthenticated caller cannot modify the project."""
        response = ui_client_anonymous.post(
            PLUGINS,
            json={"plugin_type": "extractors", "name": "tap-github"},
        )
        assert response.status_code == 401
