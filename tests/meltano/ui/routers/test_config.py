"""Tests for reading and writing plugin configuration.

The security-relevant behaviour here is that a secret entered through the UI
must land in `.env` and must never come back out over HTTP.
"""

from __future__ import annotations

import typing as t

import pytest

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.plugin.project_plugin import ProjectPlugin
    from meltano.core.project import Project

SECRET = "correct-horse-battery-staple"  # noqa: S105


def config_url(plugin: ProjectPlugin, setting: str | None = None) -> str:
    """Build the config URL for a plugin, optionally for one setting.

    Args:
        plugin: The plugin in question.
        setting: A specific setting name.

    Returns:
        The URL path.
    """
    base = f"/api/v1/plugins/{plugin.type}/{plugin.name}/config"
    return f"{base}/{setting}" if setting else base


@pytest.fixture
def sensitive_setting(ui_client: TestClient, tap: ProjectPlugin) -> str:
    """Return the name of a sensitive setting on the test extractor.

    Args:
        ui_client: The API client.
        tap: The test extractor.

    Returns:
        The setting's name.
    """
    payload = ui_client.get(config_url(tap)).json()
    secrets = [s["name"] for s in payload["settings"] if s["sensitive"]]
    if not secrets:
        pytest.skip("fixture plugin declares no sensitive settings")
    return secrets[0]


@pytest.mark.usefixtures("tap")
class TestReadConfig:
    """Listing a plugin's settings."""

    def test_returns_settings_with_metadata(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Each setting carries what a form field needs."""
        payload = ui_client.get(config_url(tap)).json()
        assert payload["name"] == tap.name
        assert payload["settings"]

        setting = payload["settings"][0]
        for key in ("name", "kind", "sensitive", "source", "is_set"):
            assert key in setting

    def test_unknown_plugin_is_404(self, ui_client: TestClient) -> None:
        """A name that is not in the project is refused."""
        response = ui_client.get("/api/v1/plugins/extractors/tap-nope/config")
        assert response.status_code == 404

    def test_unknown_plugin_type_is_404(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A bogus type segment does not raise a 500."""
        response = ui_client.get(f"/api/v1/plugins/wombats/{tap.name}/config")
        assert response.status_code == 404

    def test_requires_authentication(
        self,
        ui_client_anonymous: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Configuration is not public: it names every secret a plugin takes."""
        assert ui_client_anonymous.get(config_url(tap)).status_code == 401


@pytest.mark.usefixtures("tap")
class TestSecretHandling:
    """Secrets go to `.env` and never come back."""

    def test_secret_is_written_to_dotenv(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        sensitive_setting: str,
        project: Project,
    ) -> None:
        """Meltano's AUTO store routes sensitive values away from meltano.yml."""
        response = ui_client.put(
            config_url(tap, sensitive_setting),
            json={"value": SECRET},
        )
        assert response.status_code == 200

        body = response.json()
        assert body["store"] == "dotenv"
        assert body["is_set"] is True

        assert SECRET in (project.root / ".env").read_text()

    def test_secret_never_reaches_meltano_yml(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        sensitive_setting: str,
        project: Project,
    ) -> None:
        """The committed file must not gain the secret."""
        ui_client.put(config_url(tap, sensitive_setting), json={"value": SECRET})
        assert SECRET not in project.meltanofile.read_text()

    def test_write_response_does_not_echo_the_secret(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        sensitive_setting: str,
    ) -> None:
        """The response confirming a write must not contain the value."""
        response = ui_client.put(
            config_url(tap, sensitive_setting),
            json={"value": SECRET},
        )
        assert SECRET not in response.text

    def test_reading_back_redacts_the_secret(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        sensitive_setting: str,
    ) -> None:
        """A configured secret reads back as the redaction marker."""
        ui_client.put(config_url(tap, sensitive_setting), json={"value": SECRET})

        response = ui_client.get(config_url(tap))
        assert SECRET not in response.text

        entry = next(
            item
            for item in response.json()["settings"]
            if item["name"] == sensitive_setting
        )
        assert entry["is_set"] is True
        assert entry["value"] != SECRET

    def test_no_endpoint_reveals_the_secret(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        sensitive_setting: str,
    ) -> None:
        """There is deliberately no way to read a secret back out."""
        ui_client.put(config_url(tap, sensitive_setting), json={"value": SECRET})

        # The whole documented surface, checked for a leak.
        for path in (
            config_url(tap),
            "/api/v1/plugins",
            "/api/v1/project",
            "/api/v1/meta",
        ):
            assert SECRET not in ui_client.get(path).text, f"{path} leaked the secret"


@pytest.mark.usefixtures("tap")
class TestNonSensitiveWrites:
    """Ordinary settings round-trip normally."""

    def test_set_and_read_back(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """A plain value is stored and returned."""
        payload = ui_client.get(config_url(tap)).json()
        plain = next(item for item in payload["settings"] if not item["sensitive"])

        response = ui_client.put(
            config_url(tap, plain["name"]), json={"value": "hello"}
        )
        assert response.status_code == 200
        assert response.json()["is_set"] is True

        after = ui_client.get(config_url(tap)).json()
        entry = next(i for i in after["settings"] if i["name"] == plain["name"])
        assert entry["value"] == "hello"

    def test_unset_restores_the_default(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
    ) -> None:
        """Deleting a value falls back to the setting's default."""
        payload = ui_client.get(config_url(tap)).json()
        plain = next(item for item in payload["settings"] if not item["sensitive"])

        ui_client.put(config_url(tap, plain["name"]), json={"value": "hello"})
        response = ui_client.delete(config_url(tap, plain["name"]))

        assert response.status_code == 200
        assert response.json()["is_set"] is False

    def test_readonly_mode_refuses_writes(
        self,
        ui_client: TestClient,
        tap: ProjectPlugin,
        monkeypatch: pytest.MonkeyPatch,
        ui_context: t.Any,
    ) -> None:
        """A read-only server does not accept configuration changes."""
        monkeypatch.setattr(
            type(ui_context.settings), "readonly", property(lambda _: True)
        )
        response = ui_client.put(config_url(tap, "anything"), json={"value": "x"})
        assert response.status_code == 403
