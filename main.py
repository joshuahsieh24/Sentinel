import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from graph import load_topology, compute_full_state

BASE = Path(__file__).parent
TOPOLOGY_PATH = BASE / "assets" / "topology.json"
sg = None
state_lock = asyncio.Lock()

@asynccontextmanager
async def lifespan(app: FastAPI):
    global sg
    sg = load_topology(TOPOLOGY_PATH)
    yield

app = FastAPI(title="Sentinel", lifespan=lifespan)

@app.get("/state")
async def get_state():
    async with state_lock:
        return JSONResponse(compute_full_state(sg))
