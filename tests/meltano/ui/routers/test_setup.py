"""Tests for the setup server a projectless launch shows."""

from __future__ import annotations

import typing as t
from unittest import mock

import pytest

from meltano.ui.app import create_setup_app
from meltano.ui.context import SetupContext
from meltano.ui.settings import UIServerSettings
from tests.meltano.ui.conftest import TEST_HOST, TEST_TOKEN

if t.TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from fastapi.testclient import TestClient

SETUP = "/api/v1/setup"


@pytest.fixture
def setup_context() -> SetupContext:
    """Build a context for a server that has no project.

    Returns:
        The context under test.
    """
    return SetupContext(
        settings=UIServerSettings(
            host="127.0.0.1",
            port=5001,
            open_browser=False,
            token=TEST_TOKEN,
            extra_allowed_hosts=frozenset({TEST_HOST}),
        ),
    )


@pytest.fixture
def setup_client(setup_context: SetupContext) -> Iterator[TestClient]:
    """Yield an authenticated client against the setup app.

    Args:
        setup_context: The context to serve.

    Yields:
        The client.
    """
    from fastapi.testclient import TestClient

    with TestClient(
        create_setup_app(setup_context),
        headers={"Authorization": f"Bearer {TEST_TOKEN}"},
    ) as client:
        yield client


@pytest.fixture
def in_empty_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Run with the process working directory somewhere harmless.

    The working directory is changed for real rather than by patching
    `Path.cwd`: `ProjectInitService` rewrites its target to a path *relative*
    to the cwd, so a patched `cwd` sends `mkdir` to the real one and scatters
    scaffolded projects through the checkout.

    Args:
        tmp_path: A scratch directory.
        monkeypatch: Restores the previous directory afterwards.

    Returns:
        The directory now in effect.
    """
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.mark.usefixtures("in_empty_dir")
class TestSetupState:
    """What the setup screen is told."""

    def test_reports_an_empty_working_directory(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """With nothing nearby, there is nothing to offer."""
        body = setup_client.get(SETUP).json()

        assert body["cwd"] == str(in_empty_dir)
        assert body["candidates"] == []

    def test_finds_a_project_in_the_working_directory(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """A project the server was started inside is offered directly."""
        (in_empty_dir / "meltano.yml").write_text("version: 1\n")

        body = setup_client.get(SETUP).json()

        assert [c["path"] for c in body["candidates"]] == [str(in_empty_dir)]

    def test_finds_projects_one_level_down(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """The common `~/projects/<name>` layout is found without typing."""
        child = in_empty_dir / "analytics"
        child.mkdir()
        (child / "meltano.yml").write_text("version: 1\n")
        (in_empty_dir / "not-a-project").mkdir()

        body = setup_client.get(SETUP).json()

        assert [c["name"] for c in body["candidates"]] == ["analytics"]

    def test_ignores_dot_directories(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """`.meltano` and friends are not projects to offer."""
        hidden = in_empty_dir / ".cache"
        hidden.mkdir()
        (hidden / "meltano.yml").write_text("version: 1\n")

        assert setup_client.get(SETUP).json()["candidates"] == []


@pytest.mark.usefixtures("in_empty_dir")
class TestCreateProject:
    """Creating a project from the setup screen."""

    def test_create_makes_a_project_and_chooses_it(
        self,
        setup_client: TestClient,
        setup_context: SetupContext,
        in_empty_dir: Path,
    ) -> None:
        """The project is created and recorded for the caller to serve."""
        target = in_empty_dir / "fresh"

        response = setup_client.post(
            f"{SETUP}/create",
            json={"path": str(target)},
        )

        assert response.status_code == 201
        assert (target / "meltano.yml").is_file()
        assert setup_context.project_path == target

    def test_create_does_not_activate_the_project(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """Activation sets process-global state this process will never use.

        The server is about to stop; a fresh one activates the project
        instead, which is what keeps `Project._default` written exactly once.
        """
        with mock.patch(
            "meltano.core.project_init_service.Project.activate",
        ) as activate:
            setup_client.post(
                f"{SETUP}/create",
                json={"path": str(in_empty_dir / "fresh")},
            )

        activate.assert_not_called()

    def test_create_refuses_an_existing_project(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """Overwriting someone's project is a conflict, not a default."""
        target = in_empty_dir / "taken"
        target.mkdir()
        (target / "meltano.yml").write_text("version: 1\n")

        response = setup_client.post(f"{SETUP}/create", json={"path": str(target)})

        assert response.status_code == 409

    def test_create_can_be_forced(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """`force` is the same escape hatch `meltano init --force` offers."""
        target = in_empty_dir / "taken"
        target.mkdir()
        (target / "meltano.yml").write_text("version: 1\n")

        response = setup_client.post(
            f"{SETUP}/create",
            json={"path": str(target), "force": True},
        )

        assert response.status_code == 201

    def test_create_expands_a_home_relative_path(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A typed `~/...` means what it looks like."""
        monkeypatch.setenv("HOME", str(in_empty_dir))

        response = setup_client.post(
            f"{SETUP}/create",
            json={"path": "~/from-tilde"},
        )

        assert response.status_code == 201
        assert (in_empty_dir / "from-tilde" / "meltano.yml").is_file()


@pytest.mark.usefixtures("in_empty_dir")
class TestOpenProject:
    """Pointing the server at a project that already exists."""

    def test_open_chooses_the_project(
        self,
        setup_client: TestClient,
        setup_context: SetupContext,
        in_empty_dir: Path,
    ) -> None:
        """An existing project is recorded without being modified."""
        target = in_empty_dir / "existing"
        target.mkdir()
        (target / "meltano.yml").write_text("version: 1\n")

        response = setup_client.post(f"{SETUP}/open", json={"path": str(target)})

        assert response.status_code == 200
        assert response.json()["created"] is False
        assert setup_context.project_path == target

    def test_open_rejects_a_missing_directory(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """A path that is not there is a 404."""
        response = setup_client.post(
            f"{SETUP}/open",
            json={"path": str(in_empty_dir / "nowhere")},
        )
        assert response.status_code == 404

    def test_open_rejects_a_directory_that_is_not_a_project(
        self,
        setup_client: TestClient,
        in_empty_dir: Path,
    ) -> None:
        """A real directory without `meltano.yml` is not openable.

        Serving it would fail later and less clearly, inside `Project`.
        """
        target = in_empty_dir / "just-a-folder"
        target.mkdir()

        response = setup_client.post(f"{SETUP}/open", json={"path": str(target)})

        assert response.status_code == 422
        assert "meltano.yml" in response.json()["detail"]


class TestSetupIsolation:
    """What the setup app deliberately does not serve."""

    @pytest.mark.parametrize(
        "path",
        ("/api/v1/plugins", "/api/v1/runs", "/api/v1/jobs", "/api/v1/state"),
    )
    def test_the_full_api_is_absent(
        self,
        setup_client: TestClient,
        path: str,
    ) -> None:
        """Every other router dereferences `ctx.project`, which is absent.

        Mounting them here would turn each one into an `AttributeError` rather
        than an honest "there is no project yet".
        """
        assert setup_client.get(path).status_code == 404

    def test_the_spa_shell_is_still_served(self, setup_client: TestClient) -> None:
        """The setup screen is the same application, so it needs the shell."""
        assert setup_client.get("/").status_code == 200


class TestSetupAuth:
    """Admission control applies before a project exists."""

    def test_anonymous_read_is_refused(
        self,
        setup_context: SetupContext,
    ) -> None:
        """An open port that can write to disk is gated like any other."""
        from fastapi.testclient import TestClient

        with TestClient(create_setup_app(setup_context)) as client:
            assert client.get(SETUP).status_code == 401

    def test_readonly_refuses_to_create(
        self,
        in_empty_dir: Path,
    ) -> None:
        """`--readonly` forbids creating a project too.

        Creating one writes to the filesystem, which is exactly what the flag
        exists to prevent.
        """
        from fastapi.testclient import TestClient

        ctx = SetupContext(
            settings=UIServerSettings(
                host="127.0.0.1",
                port=5001,
                open_browser=False,
                readonly=True,
                token=TEST_TOKEN,
                extra_allowed_hosts=frozenset({TEST_HOST}),
            ),
        )

        with TestClient(
            create_setup_app(ctx),
            headers={"Authorization": f"Bearer {TEST_TOKEN}"},
        ) as client:
            response = client.post(
                f"{SETUP}/create",
                json={"path": str(in_empty_dir / "nope")},
            )

        assert response.status_code == 403
        assert ctx.project_path is None
