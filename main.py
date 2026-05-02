"""
Sentinel — FastAPI backend.
Serves the single-page frontend, exposes action endpoints,
and maintains a WebSocket fanout for state + network events.
"""
import asyncio
import json
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from graph import (
    SentinelGraph,
    apply_fail_switch_a,
    apply_fail_switch_a_commit,
    apply_fix,
    apply_obstruct_cam2,
    compute_full_state,
    load_topology,
    reset,
)
from timeline import TimelinePlayer

BASE = Path(__file__).parent
TOPOLOGY_PATH = BASE / "assets" / "topology.json"
TIMELINE_PATH = BASE / "assets" / "network_timeline.json"

# ---------------------------------------------------------------------------
# Shared state
# ---------------------------------------------------------------------------

sg: SentinelGraph
timeline: TimelinePlayer
state_lock = asyncio.Lock()
connections: set[WebSocket] = set()
_last_broadcast_state: dict | None = None
_state_dirty = False


async def _broadcast(msg: dict):
    data = json.dumps(msg)
    dead: set[WebSocket] = set()
    for ws in connections.copy():
        try:
            await ws.send_text(data)
        except Exception:
            dead.add(ws)
    connections.difference_update(dead)


async def _broadcast_state():
    global _last_broadcast_state, _state_dirty
    async with state_lock:
        state = compute_full_state(sg)
    await _broadcast({"type": "state", **state})
    _last_broadcast_state = state
    _state_dirty = False


# ---------------------------------------------------------------------------
# Background tasks
# ---------------------------------------------------------------------------

async def _state_loop():
    """Broadcast state at 5 Hz, but only when dirty."""
    global _state_dirty
    while True:
        await asyncio.sleep(0.2)
        if _state_dirty:
            await _broadcast_state()


_last_event_broadcast: float = 0.0
_EVENT_INTERVAL = 0.35  # broadcast at most ~3 events/sec — readable but clearly live


async def _event_loop():
    """Replay network timeline events; suppress offline devices; throttle display rate."""
    global _last_event_broadcast
    while True:
        due = timeline.tick()
        async with state_lock:
            suppressed = set(sg.suppressed_devices)
        now = time.monotonic()
        if due and now - _last_event_broadcast >= _EVENT_INTERVAL:
            visible = [e for e in due if e["src"] not in suppressed]
            if visible:
                evt = random.choice(visible)
                await _broadcast({
                    "type": "event",
                    "ts": int(time.time() * 1000),
                    "event_type": evt["type"],
                    "src": evt["src"],
                    "dst": evt["dst"],
                    "summary": evt["summary"],
                })
                _last_event_broadcast = now
        await asyncio.sleep(0.02)


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sg, timeline, _state_dirty
    sg = load_topology(TOPOLOGY_PATH)
    timeline = TimelinePlayer(TIMELINE_PATH)
    _state_dirty = True
    asyncio.create_task(_state_loop())
    asyncio.create_task(_event_loop())
    yield


app = FastAPI(title="Sentinel", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.get("/")
async def index():
    return FileResponse(BASE / "static" / "index.html")


# ---------------------------------------------------------------------------
# REST — read state
# ---------------------------------------------------------------------------

@app.get("/state")
async def get_state():
    async with state_lock:
        return JSONResponse(compute_full_state(sg))


# ---------------------------------------------------------------------------
# REST — actions
# ---------------------------------------------------------------------------

@app.post("/action/obstruct-cam2")
async def action_obstruct_cam2():
    global _state_dirty
    async with state_lock:
        apply_obstruct_cam2(sg)
        _state_dirty = True
    return {"ok": True}


@app.post("/action/fail-switch-a")
async def action_fail_switch_a():
    """
    Returns 202 immediately. Events are suppressed right away.
    State update (score crash, incident) fires 1.5s later.
    The 1.5s gap is the visual proof of the 'network-layer detection before
    device-layer monitoring' pitch.
    """
    global _state_dirty
    async with state_lock:
        apply_fail_switch_a(sg)  # suppress events immediately
    asyncio.create_task(_delayed_fail_commit())
    return JSONResponse(
        {"ok": True, "note": "state update in 1500ms"},
        status_code=202,
    )


async def _delayed_fail_commit():
    global _state_dirty
    await asyncio.sleep(1.5)
    async with state_lock:
        apply_fail_switch_a_commit(sg)
        _state_dirty = True


@app.post("/action/apply-fix")
async def action_apply_fix():
    global _state_dirty
    async with state_lock:
        if sg.incident is None or sg.incident.fix_applied:
            return JSONResponse({"ok": False, "error": "no active unfixed incident"}, status_code=409)
        apply_fix(sg)
        _state_dirty = True
    return {"ok": True}


@app.post("/action/reset")
async def action_reset():
    global _state_dirty
    async with state_lock:
        reset(sg)
        _state_dirty = True
    return {"ok": True}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connections.add(ws)
    # Push full state on connect so new client is immediately up-to-date
    async with state_lock:
        state = compute_full_state(sg)
    await ws.send_text(json.dumps({"type": "state", **state}))
    try:
        while True:
            await ws.receive_text()  # keep alive; we only push from server
    except WebSocketDisconnect:
        connections.discard(ws)
    except Exception:
        connections.discard(ws)
