"""Endpoints for Singer state - the bookmarks incremental runs resume from.

Deliberately narrower than `meltano state`. Listing, reading, overwriting and
clearing one state ID are what a person needs while watching a pipeline;
`copy`, `move`, `merge`, `export` and `import` are migration tools that belong
in a terminal, and `clear --all` is not something that should be one button
press away.

Note that this never goes through `state_service_from_state_id`, the helper the
CLI uses to pick a state service by parsing the state ID. That helper calls
`Project.activate_environment`, which mutates process-global state that this
server shares across every request. The standalone `StateService` - the CLI's
own fallback - is correct here, and operates on the environment the server was
started in.
"""

from __future__ import annotations

import json
import typing as t
from contextlib import contextmanager

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from meltano.core.job_state import SINGER_STATE_KEY
from meltano.core.state_service import (
    InvalidJobStateError,
    StatePersistenceError,
    StateService,
)
from meltano.ui.deps import CtxDep, SessionDep, require_auth
from meltano.ui.errors import HTTP_422_UNPROCESSABLE
from meltano.ui.schemas.state import SetStateRequest, StateDetail, StateSummary

if t.TYPE_CHECKING:
    from collections.abc import Iterator

    from sqlalchemy.orm import Session

    from meltano.ui.context import AppContext

router = APIRouter(tags=["state"], dependencies=[Depends(require_auth)])


@contextmanager
def _state_service(ctx: AppContext, session: Session) -> Iterator[StateService]:
    """Yield a state service bound to this request's session.

    Args:
        ctx: The application context.
        session: A system-database session, used by the systemdb backend.

    Yields:
        The service, closed on the way out.
    """
    service = StateService(ctx.project, session)
    try:
        yield service
    finally:
        # Closes the state store manager, which for a remote backend holds a
        # client. The session is the caller's and is left alone.
        service.close()


def _streams(state: dict[str, t.Any]) -> list[str]:
    """List the bookmarked streams in a Singer state payload.

    Args:
        state: The payload, which may be empty or an unexpected shape.

    Returns:
        The stream names, sorted; empty when there are none to report.
    """
    bookmarks = state.get(SINGER_STATE_KEY, {}).get("bookmarks", {})
    return sorted(bookmarks) if isinstance(bookmarks, dict) else []


@router.get("/state", response_model=list[StateSummary])
def list_state(
    ctx: CtxDep,
    session: SessionDep,
    pattern: str | None = Query(
        default=None,
        description="Glob-style filter on state IDs, e.g. 'dev:*'.",
    ),
) -> list[StateSummary]:
    """List every state ID the configured backend holds.

    Payloads are read to report which IDs actually hold bookmarks, so this is
    one backend round trip per state ID. `pattern` narrows that.

    Args:
        ctx: The application context.
        session: A system-database session.
        pattern: Optional glob-style filter.

    Returns:
        The known state IDs, ordered by ID.
    """
    with _state_service(ctx, session) as service:
        states = service.list_state(pattern)

    return sorted(
        (
            StateSummary(
                state_id=state_id,
                has_state=bool(payload),
                streams=_streams(payload or {}),
            )
            for state_id, payload in states.items()
        ),
        key=lambda item: item.state_id,
    )


@router.get("/state/{state_id}", response_model=StateDetail)
def get_state(state_id: str, ctx: CtxDep, session: SessionDep) -> StateDetail:
    """Return the state a run of `state_id` would resume from.

    An unknown state ID is not an error: a pipeline that has never run has no
    bookmarks yet, which is reported as an empty payload rather than a 404.

    Args:
        state_id: The state ID to read.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The merged state.
    """
    with _state_service(ctx, session) as service:
        state = service.get_state(state_id)

    return StateDetail(state_id=state_id, state=state, streams=_streams(state))


@router.put("/state/{state_id}", response_model=StateDetail)
def set_state(
    state_id: str,
    payload: SetStateRequest,
    ctx: CtxDep,
    session: SessionDep,
) -> StateDetail:
    """Overwrite a state ID's bookmarks.

    Validated as Singer state. The CLI can skip that with `--no-validate`;
    this cannot, because writing a malformed payload breaks the next run in a
    way that is only visible when it happens.

    No project write lock is taken: state lives in the state backend, not in
    `meltano.yml`.

    Args:
        state_id: The state ID to write.
        payload: The new state.
        ctx: The application context.
        session: A system-database session.

    Returns:
        The stored state.

    Raises:
        HTTPException: 422 when the payload is not Singer state; 502 when the
            backend refuses the write.
    """
    with _state_service(ctx, session) as service:
        try:
            service.set_state(state_id, json.dumps(payload.state))
        except InvalidJobStateError as err:
            raise HTTPException(
                HTTP_422_UNPROCESSABLE,
                detail=(
                    f"Not valid Singer state: a top-level {SINGER_STATE_KEY!r} "
                    "key is required"
                ),
            ) from err
        except StatePersistenceError as err:
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                detail="The configured state backend rejected the write",
            ) from err

        stored = service.get_state(state_id)

    return StateDetail(state_id=state_id, state=stored, streams=_streams(stored))


@router.delete("/state/{state_id}", status_code=status.HTTP_204_NO_CONTENT)
def clear_state(state_id: str, ctx: CtxDep, session: SessionDep) -> Response:
    """Clear a state ID's bookmarks, so the next run starts from scratch.

    There is deliberately no "clear everything" endpoint. `meltano state clear
    --all` exists and prompts first; the same capability behind a button in a
    browser is a different proposition.

    Args:
        state_id: The state ID to clear.
        ctx: The application context.
        session: A system-database session.

    Returns:
        An empty 204 response.
    """
    with _state_service(ctx, session) as service:
        service.clear_state(state_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
