"""Run events → Server-Sent Events.

The agent (agent/runner.py) is synchronous: it runs on a worker thread and its Emitter hands each
event to the event loop through an asyncio.Queue. The SSE generator drains the queue, sends a
comment every PING_S so idle proxies keep the connection open, and sets the run's cancel flag when
the client goes away so an abandoned run stops spending Gemini tokens at its next turn.

Failures after the first byte cannot change the HTTP status, so they arrive as `error` events on
a 200 (same pattern as k8s-copilot backend/src/main.py).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import AsyncIterator, Callable

from agent import events as ev
from agent import runner

log = logging.getLogger(__name__)

PING_S = 10.0
MAX_GOLDEN_GAP_S = 8.0  # a recorded pause longer than this is dead air on stage
_DONE = object()

SSE_HEADERS = {
    # no-transform: an intermediary that gzips a stream buffers it. X-Accel-Buffering: the opt-out
    # Cloud Run's docs name for streaming responses.
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}


def _crash_event() -> ev.RunError:
    return ev.RunError(
        code="internal", message="The run failed unexpectedly. Showing the recorded run is safe.",
        retryable=True, fallback="golden",
    )


def run_blocking(ctx: runner.RunContext, *, client=None) -> None:
    """run_advisory_events that never raises: cancellation ends quietly, a crash becomes an event."""
    try:
        runner.run_advisory_events(ctx, client=client)
    except runner.RunCancelled:
        log.info("run cancelled by client")
    except Exception:  # the runner handles Gemini/tool failures itself; this is a bug, not an outage
        log.exception("run crashed")
        ctx.emit(_crash_event())


async def agent_frames(
    make_ctx: Callable[[ev.Emitter, threading.Event], runner.RunContext],
    *, on_finish: Callable[[], None], client=None,
) -> AsyncIterator[str]:
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    cancel = threading.Event()
    emitter = ev.Emitter(lambda e: loop.call_soon_threadsafe(queue.put_nowait, e))
    ctx = make_ctx(emitter, cancel)

    def work() -> None:
        try:
            run_blocking(ctx, client=client)
        finally:
            on_finish()
            loop.call_soon_threadsafe(queue.put_nowait, _DONE)

    threading.Thread(target=work, name=f"run-{emitter.run_id}", daemon=True).start()
    try:
        # A comment first forces headers and one body chunk through any buffering intermediary.
        yield ": keepalive\n\n"
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=PING_S)
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if item is _DONE:
                return
            yield ev.to_sse(item)
    finally:
        cancel.set()  # client disconnected (or the run finished, where it is a no-op)


def collect(make_ctx: Callable[[ev.Emitter, threading.Event], runner.RunContext], *, client=None) -> list[ev.RunEvent]:
    """The same run as one list, for ?stream=0 (networks whose proxies buffer event streams)."""
    events: list[ev.RunEvent] = []
    ctx = make_ctx(ev.Emitter(events.append), threading.Event())
    run_blocking(ctx, client=client)
    return events


def restamp(events: list[ev.RunEvent], note: str | None) -> list[ev.RunEvent]:
    """A recorded run replayed under a fresh run_id, labelled as recorded."""
    run_id = uuid.uuid4().hex[:12]
    out = []
    for e in events:
        update: dict = {"run_id": run_id}
        if e.type == "run_started":
            update |= {"recorded": True, "note": note}
        out.append(e.model_copy(update=update))
    return out


async def golden_frames(events: list[ev.RunEvent], *, speed: float = 1.0, note: str | None = None) -> AsyncIterator[str]:
    """Replay at the recorded pacing (t_ms), so the trace builds up the way it did live."""
    yield ": keepalive\n\n"
    prev = 0
    for e in restamp(events, note):
        if speed > 0 and e.t_ms > prev:
            await asyncio.sleep(min((e.t_ms - prev) / 1000 / speed, MAX_GOLDEN_GAP_S))
        prev = e.t_ms
        yield ev.to_sse(e)
