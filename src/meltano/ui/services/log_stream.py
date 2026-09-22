"""Server-sent-event formatting for run output.

SSE is used rather than WebSockets because the stream is strictly
server-to-client (cancellation is an ordinary ``DELETE``), and because SSE is
plain HTTP: it passes through the same dependency chain as every other route,
so the token and ``Host`` checks in :mod:`meltano.ui.security` apply
automatically. WebSocket handshakes bypass CORS entirely and would need their
own hand-written origin validation.
"""

from __future__ import annotations

import asyncio
import json
import typing as t
from contextlib import suppress

if t.TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from meltano.ui.services.run_manager import RunManager

#: Comment frames keep intermediaries and the browser from timing the
#: connection out during a quiet stretch of a long run.
KEEPALIVE_SECONDS = 15.0


def format_event(
    event: str,
    data: t.Any,  # noqa: ANN401
    *,
    event_id: int | None = None,
) -> str:
    """Render a single SSE frame.

    Args:
        event: The event name.
        data: JSON-serializable payload.
        event_id: Optional id, echoed back by the browser as `Last-Event-ID`.

    Returns:
        The encoded frame, terminated by a blank line.
    """
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.extend(
        (
            f"event: {event}",
            f"data: {json.dumps(data, default=str)}",
        )
    )
    return "\n".join(lines) + "\n\n"


def parse_last_event_id(raw: str | None) -> int:
    """Interpret the `Last-Event-ID` header.

    Args:
        raw: The header value, if the browser sent one.

    Returns:
        The last line id the client received, or 0.
    """
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _decode_line(line: str) -> dict[str, t.Any]:
    """Turn one output line into a payload.

    Meltano is invoked with ``--log-format=json``, and Singer SDK plugins are
    configured by core to emit structured JSON too, so most lines carry real
    fields (level, event, stream name, exception) rather than prose. Lines
    that are not JSON are passed through as plain messages.

    Args:
        line: A raw output line.

    Returns:
        A mapping suitable for the SSE payload.
    """
    stripped = line.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        try:
            parsed = json.loads(stripped)
        except ValueError:
            pass
        else:
            if isinstance(parsed, dict):
                return {"structured": True, **parsed}
    return {"structured": False, "message": line}


async def stream_run(
    manager: RunManager,
    run_id: str,
    *,
    last_event_id: int = 0,
) -> AsyncIterator[str]:
    """Yield SSE frames for a run until it finishes.

    Args:
        manager: The supervising run manager.
        run_id: The run to follow.
        last_event_id: Resume point from the client's `Last-Event-ID`.

    Yields:
        Encoded SSE frames.
    """
    record = manager.get(run_id)
    if record is None:
        yield format_event("error", {"detail": f"Unknown run {run_id}"})
        return

    yield format_event("status", record.to_dict())

    lines = manager.subscribe(run_id, after_id=last_event_id)
    pending: asyncio.Task[tuple[int, str]] | None = None
    iterator = lines.__aiter__()

    try:
        while True:
            if pending is None:
                pending = asyncio.ensure_future(_next(iterator))

            done, _ = await asyncio.wait(
                {pending},
                timeout=KEEPALIVE_SECONDS,
            )
            if not done:
                yield ": keepalive\n\n"
                continue

            try:
                line_id, text = pending.result()
            except StopAsyncIteration:
                break
            finally:
                pending = None

            yield format_event("log", _decode_line(text), event_id=line_id)
    finally:
        if pending is not None:
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        with suppress(Exception):
            await iterator.aclose()  # type: ignore[attr-defined]

    final = manager.get(run_id)
    if final is not None:
        yield format_event("end", final.to_dict())


async def _next(iterator: t.Any) -> tuple[int, str]:  # noqa: ANN401
    """Advance an async iterator, translating exhaustion into a raise."""
    return await iterator.__anext__()
