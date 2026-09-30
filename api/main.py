"""FastAPI app for the showcase: REST for the fleet and forecast, SSE for agent runs, and the SPA.

    uv run uvicorn api.main:app --reload --port 8000

One public Cloud Run service with no accounts, so every endpoint is read-only or bounded:
live Gemini runs are capped (api/state.py RunLimiter) and past the cap the recorded run for the
same shipment is served instead, labelled as recorded. There is no free-text input anywhere.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
import os
import threading
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
from agent import events as ev
from agent.runner import RunContext
from api import streaming
from api.fleet import Fleet, scan_fleet
from api.state import AppState, Scene, build_state

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "frontend" / "dist"
VERSION = "1.0.0"
HERO_SHIPMENT = "SHP-2001"
FORECAST_VARS = ("wind_mph", "precip_mm_hr")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    if not hasattr(app.state, "data"):  # tests install their own state
        app.state.data = build_state()
    yield


app = FastAPI(title="WeatherNext 3 Route Disruption Agent", version=VERSION, lifespan=lifespan)
# Cloud Run does not compress. Starlette's GZip skips text/event-stream, so SSE still streams.
app.add_middleware(GZipMiddleware, minimum_size=1000)


def get_state(request: Request) -> AppState:
    return request.app.state.data


def get_genai_client():
    """None = the runner's shared Vertex AI client. Tests override this with a scripted fake."""
    return None


def _scene(state: AppState, mode: Optional[str]) -> Scene:
    scene = state.scene(mode)
    if scene is None:
        raise HTTPException(400, f"mode '{mode}' is not available; available: {sorted(state.scenes)}")
    return scene


# --- REST ---------------------------------------------------------------------------------------
@app.get("/api/health")
def health(state: AppState = Depends(get_state)) -> dict:
    return {
        "status": "ok", "version": VERSION, "git_sha": os.getenv("GIT_SHA", "dev"),
        "modes": sorted(state.scenes), "golden": sorted(state.golden),
    }


@app.get("/api/config")
def app_config(state: AppState = Depends(get_state)) -> dict:
    """What the browser needs to boot. The Maps JS key is public by design (referrer + API
    restricted); the Routes key never leaves the server and is never part of this response."""
    return {
        "maps_api_key": config.MAPS_JS_API_KEY,
        "map_id": config.MAP_ID,
        "model": config.GEMINI_MODEL,
        "event_version": ev.EVENT_VERSION,
        "default_mode": state.default_mode,
        "modes": [
            {"id": m, "label": s.cube.meta.label, "init_time": s.cube.meta.init_time.isoformat().replace("+00:00", "Z")}
            for m, s in sorted(state.scenes.items())
        ],
        "golden": sorted(state.golden),
        "hero_shipment": HERO_SHIPMENT,
        "live_runs_left_today": max(0, state.limiter.daily_cap - state.limiter.today),
    }


@app.get("/api/fleet", response_model=Fleet, response_model_exclude_none=True)
async def fleet(mode: Optional[str] = None, state: AppState = Depends(get_state)) -> Fleet:
    return await asyncio.to_thread(scan_fleet, _scene(state, mode), state.routes)


@app.get("/api/forecast")
def forecast(
    request: Request,
    var: Literal["wind_mph", "precip_mm_hr"] = "wind_mph",
    mode: Optional[str] = None,
    state: AppState = Depends(get_state),
) -> Response:
    """Every forecast hour as a quantized uint8 grid, so the time slider scrubs locally."""
    scene = _scene(state, mode)
    meta = scene.cube.meta
    etag = '"' + hashlib.sha256(f"{meta.source}|{meta.init_time.isoformat()}|{var}|v2".encode()).hexdigest()[:16] + '"'
    headers = {"ETag": etag, "Cache-Control": "public, max-age=600"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers=headers)
    body = {
        **scene.cube.wire(var),
        "init_time": meta.init_time.isoformat().replace("+00:00", "Z"),
        "now_s": int((scene.now - meta.init_time).total_seconds()),
    }
    return JSONResponse(body, headers=headers)


# --- Runs (SSE) ---------------------------------------------------------------------------------
class RunRequest(BaseModel):
    shipment_id: str = Field(..., pattern=r"^[A-Z0-9-]{1,32}$")
    mode: Optional[Literal["live", "replay", "scenario"]] = None


def _golden_response(state: AppState, shipment_id: str, *, speed: float, note: Optional[str], stream: bool):
    events = state.golden.get(shipment_id)
    if events is None:
        return None
    if not stream:
        return JSONResponse([e.model_dump(mode="json", exclude_none=True) for e in streaming.restamp(events, note)])
    return StreamingResponse(
        streaming.golden_frames(events, speed=speed, note=note), media_type="text/event-stream",
        headers={**streaming.SSE_HEADERS, "X-Run-Source": "golden"},
    )


@app.post("/api/runs")
async def create_run(
    req: RunRequest,
    stream: int = Query(1, ge=0, le=1),
    state: AppState = Depends(get_state),
    client=Depends(get_genai_client),
):
    scene = _scene(state, req.mode)
    shipment = scene.shipments.get(req.shipment_id)
    if shipment is None:
        raise HTTPException(404, f"unknown shipment {req.shipment_id}")

    refusal = state.limiter.try_acquire()
    if refusal is not None:
        golden = _golden_response(state, req.shipment_id, speed=1.0, note=f"{refusal} · recorded run", stream=bool(stream))
        if golden is not None:
            return golden
        return JSONResponse({"detail": refusal, "fallback": None}, status_code=429)

    def make_ctx(emit: ev.Emitter, cancel: threading.Event) -> RunContext:
        return RunContext(
            shipment=shipment, cube=scene.cube, routes=state.routes.scoped(), emit=emit, mode=scene.mode,
            cancel=cancel, now=scene.now,
        )

    if not stream:
        try:
            events = await asyncio.to_thread(streaming.collect, make_ctx, client=client)
        finally:
            state.limiter.release()
        return JSONResponse([e.model_dump(mode="json", exclude_none=True) for e in events])
    return StreamingResponse(
        streaming.agent_frames(make_ctx, on_finish=state.limiter.release, client=client),
        media_type="text/event-stream", headers={**streaming.SSE_HEADERS, "X-Run-Source": "live"},
    )


@app.get("/api/golden/{shipment_id}")
async def golden(
    shipment_id: str,
    speed: float = Query(1.0, ge=0, le=16),
    stream: int = Query(1, ge=0, le=1),
    state: AppState = Depends(get_state),
):
    resp = _golden_response(state, shipment_id, speed=speed, note="Recorded run", stream=bool(stream))
    if resp is None:
        raise HTTPException(404, f"no recorded run for {shipment_id}")
    return resp


# --- SPA ----------------------------------------------------------------------------------------
# Vite emits hashed files under /assets (cache forever) and an index.html that must not be cached.
if DIST.is_dir():
    if (DIST / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(404)
        f = (DIST / path).resolve()
        if path and f.is_file() and DIST in f.parents:
            return FileResponse(f)
        return FileResponse(DIST / "index.html", headers={"Cache-Control": "no-cache"})
