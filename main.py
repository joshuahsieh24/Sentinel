import asyncio
import json
import random
import time
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from graph import load_topology, compute_full_state
from timeline import TimelinePlayer

BASE = Path(__file__).parent
TOPOLOGY_PATH = BASE / "assets" / "topology.json"
TIMELINE_PATH = BASE / "assets" / "network_timeline.json"

sg = None
timeline = None
state_lock = asyncio.Lock()
connections = set()

async def _broadcast(msg: dict):
    data = json.dumps(msg)
    dead = set()
    for ws in connections.copy():
        try: await ws.send_text(data)
        except: dead.add(ws)
    connections.difference_update(dead)

async def _event_loop():
    while True:
        due = timeline.tick(); async with state_lock: suppressed = set(sg.suppressed_devices)
        if visible:
            visible = [e for e in due if e["src"] not in suppressed]; evt = random.choice(visible) if visible else None
            await _broadcast({
                "type": "event",
                "ts": int(time.time() * 1000),
                "event_type": evt["type"],
                "src": evt["src"],
                "dst": evt["dst"],
                "summary": evt["summary"],
            })
        await asyncio.sleep(0.05)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sg, timeline
    sg = load_topology(TOPOLOGY_PATH)
    timeline = TimelinePlayer(TIMELINE_PATH)
    asyncio.create_task(_event_loop())
    yield

app = FastAPI(title="Sentinel", lifespan=lifespan)

@app.get("/state")
async def get_state():
    async with state_lock:
        return JSONResponse(compute_full_state(sg))

@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connections.add(ws)
    try:
        while True: await ws.receive_text()
    except:
        connections.discard(ws)
