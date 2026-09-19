"""Tests for the Singer state endpoints."""

from __future__ import annotations

import json
import typing as t

import pytest

from meltano.core.job_state import SINGER_STATE_KEY
from meltano.core.state_service import StateService

if t.TYPE_CHECKING:
    from fastapi.testclient import TestClient

    from meltano.core.project import Project
    from meltano.ui.context import AppContext

STATE = "/api/v1/state"

#: A minimal payload that passes `StateService.validate_state`.
PAYLOAD = {SINGER_STATE_KEY: {"bookmarks": {"animals": {"replication_key": "id"}}}}


@pytest.fixture
def state_service(
    project: Project,
    ui_context: AppContext,
) -> t.Iterator[StateService]:
    """Yield a state service over a project with no state, cleaning up after.

    The `project` fixture is class-scoped and `pytest-randomly` shuffles order,
    so state written by one test would otherwise be visible to the next.

    Args:
        project: The test project.
        ui_context: The application context, for a session on the same
            database the handlers use.

    Yields:
        The service under test.
    """
    session = ui_context.session_factory()
    service = StateService(project, session)
    service.clear_all_states()
    try:
        yield service
    finally:
        service.clear_all_states()
        service.close()
        session.close()


class TestListState:
    """Enumerating the state IDs the backend holds."""

    def test_list_is_empty_without_state(
        self,
        ui_client: TestClient,
        state_service: StateService,  # noqa: ARG002
    ) -> None:
        """A project that has never run reports no state."""
        response = ui_client.get(STATE)
        assert response.status_code == 200
        assert response.json() == []

    def test_list_reports_bookmarked_streams(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """The summary names the streams without returning the payload."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))

        listed = ui_client.get(STATE).json()

        entry = next(
            item for item in listed if item["state_id"] == "dev:tap-mock-to-target-mock"
        )
        assert entry["has_state"] is True
        assert entry["streams"] == ["animals"]

    def test_pattern_narrows_the_list(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """A glob filters state IDs, so a big backend need not be read whole."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))
        state_service.set_state("prod:tap-mock-to-target-mock", json.dumps(PAYLOAD))

        listed = ui_client.get(STATE, params={"pattern": "prod:*"}).json()

        assert [item["state_id"] for item in listed] == [
            "prod:tap-mock-to-target-mock",
        ]


class TestGetState:
    """Reading the payload a run would resume from."""

    def test_returns_the_stored_payload(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """The merged state comes back as stored."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))

        body = ui_client.get(f"{STATE}/dev:tap-mock-to-target-mock").json()

        assert body["state"] == PAYLOAD
        assert body["streams"] == ["animals"]

    def test_unknown_state_id_is_empty_not_404(
        self,
        ui_client: TestClient,
        state_service: StateService,  # noqa: ARG002
    ) -> None:
        """A pipeline that has never run has no bookmarks yet.

        That is a normal condition rather than a missing resource, and a 404
        would make the UI show an error for a perfectly healthy new pipeline.
        """
        response = ui_client.get(f"{STATE}/dev:never-run-to-anything")

        assert response.status_code == 200
        assert response.json()["state"] == {}
        assert response.json()["streams"] == []

    def test_a_state_id_with_colons_is_addressable(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """State IDs contain ':' separators, including an optional suffix.

        They appear verbatim in the path, so routing has to tolerate them.
        """
        state_id = "dev:tap-mock-to-target-mock:nightly"
        state_service.set_state(state_id, json.dumps(PAYLOAD))

        response = ui_client.get(f"{STATE}/{state_id}")

        assert response.status_code == 200
        assert response.json()["state_id"] == state_id


class TestSetState:
    """Overwriting bookmarks."""

    def test_set_stores_the_payload(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """A put is written to the backend, not merely echoed back."""
        response = ui_client.put(
            f"{STATE}/dev:tap-mock-to-target-mock",
            json={"state": PAYLOAD},
        )

        assert response.status_code == 200
        assert state_service.get_state("dev:tap-mock-to-target-mock") == PAYLOAD

    def test_set_overwrites_existing_bookmarks(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """The new payload replaces the old one rather than merging into it."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))
        replacement = {SINGER_STATE_KEY: {"bookmarks": {"plants": {"id": 7}}}}

        body = ui_client.put(
            f"{STATE}/dev:tap-mock-to-target-mock",
            json={"state": replacement},
        ).json()

        assert body["streams"] == ["plants"]

    def test_set_rejects_a_non_singer_payload(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """Validation is not optional here, unlike `--no-validate` on the CLI.

        A malformed payload breaks the next run in a way that only shows up
        when that run happens.
        """
        response = ui_client.put(
            f"{STATE}/dev:tap-mock-to-target-mock",
            json={"state": {"not_singer": {}}},
        )

        assert response.status_code == 422
        assert SINGER_STATE_KEY in response.json()["detail"]
        assert state_service.get_state("dev:tap-mock-to-target-mock") == {}

    def test_set_requires_a_state_object(self, ui_client: TestClient) -> None:
        """A missing body is a validation error, not a wiped bookmark."""
        response = ui_client.put(f"{STATE}/dev:tap-mock-to-target-mock", json={})
        assert response.status_code == 422


class TestClearState:
    """Removing bookmarks so the next run starts from scratch."""

    def test_clear_removes_the_state(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """A delete empties the payload."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))

        assert (
            ui_client.delete(f"{STATE}/dev:tap-mock-to-target-mock").status_code == 204
        )
        assert state_service.get_state("dev:tap-mock-to-target-mock") == {}

    def test_clear_leaves_other_state_alone(
        self,
        ui_client: TestClient,
        state_service: StateService,
    ) -> None:
        """Clearing one state ID is not a clear-all in disguise."""
        state_service.set_state("dev:tap-mock-to-target-mock", json.dumps(PAYLOAD))
        state_service.set_state("prod:tap-mock-to-target-mock", json.dumps(PAYLOAD))

        ui_client.delete(f"{STATE}/dev:tap-mock-to-target-mock")

        assert state_service.get_state("prod:tap-mock-to-target-mock") == PAYLOAD

    def test_clearing_unknown_state_is_not_an_error(
        self,
        ui_client: TestClient,
        state_service: StateService,  # noqa: ARG002
    ) -> None:
        """Clearing nothing leaves nothing; the outcome is already the goal."""
        response = ui_client.delete(f"{STATE}/dev:never-run-to-anything")
        assert response.status_code == 204

    def test_there_is_no_clear_all_endpoint(self, ui_client: TestClient) -> None:
        """`clear --all` is deliberately CLI-only.

        The CLI prompts before wiping every bookmark in the project; the same
        capability one button press away in a browser is a different thing.
        """
        assert ui_client.delete(STATE).status_code in {404, 405}


class TestStateAuth:
    """Admission control applies to these endpoints like any other."""

    def test_anonymous_list_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Without a token, bookmarks are not readable."""
        assert ui_client_anonymous.get(STATE).status_code == 401

    def test_anonymous_clear_is_refused(
        self,
        ui_client_anonymous: TestClient,
    ) -> None:
        """Nor can an unauthenticated caller destroy them."""
        response = ui_client_anonymous.delete(f"{STATE}/dev:tap-mock-to-target-mock")
        assert response.status_code == 401
