"""world-engine: FastAPI + tick loop + snapshot/delta WebSocket.

Contract (v1, frozen):
  GET  /api/world/state   -> full snapshot
  GET  /api/world/contract-> states, events, transitions (read-only)
  POST /api/world/events  -> single-writer ingest (Atlas -> world)
  WS   /ws/world          -> snapshot on connect, then {"type":"delta", ...} per tick
  GET  /healthz           -> liveness + tick freshness (used by Docker healthcheck)

No LLM is called anywhere in this process.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any

from fastapi import FastAPI, Header, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from . import sim
from .hub import Client, Hub
from .schema import EVENT_TYPES, STATES, TRANSITIONS
from .world import World

TICK_SECONDS = float(os.getenv("TICK", "2"))
WORLD_SEED = int(os.getenv("WORLD_SEED", "7"))
STATE_DIR = os.getenv("WORLD_STATE_DIR") or None
ALLOW_MANUAL_TICK = os.getenv("WORLD_ALLOW_MANUAL_TICK", "0") == "1"
TOKEN = os.getenv("WORLD_TOKEN") or None

world = World(seed=WORLD_SEED, state_dir=STATE_DIR)
replayed = world.replay()
plan = sim.initial_durations(world)
hub = Hub()
started_at = time.time()
last_tick_at = time.time()
last_events: list[dict] = []
_ticker_task: asyncio.Task | None = None


def _agent_patches(prev: list[dict], curr: list[dict]) -> list[dict]:
    """Only send agents whose projection changed."""
    old = {a["id"]: a for a in prev}
    patches = []
    for agent in curr:
        before = old.get(agent["id"])
        if before != agent:
            patches.append(agent)
    return patches


def _delta(before: dict, after: dict, events: list[dict]) -> dict:
    patches = _agent_patches(before["agents"], after["agents"])
    meeting_changed = before.get("meeting") != after.get("meeting")
    return {
        "type": "delta",
        "tick": after["tick"],
        "ts": after["ts"],
        "patches": patches,
        "meeting": after.get("meeting"),
        "meeting_changed": meeting_changed,
        "counts": after["counts"],
        "events": events,
    }


def advance() -> dict:
    """One tick: step the sim, broadcast the delta, return it."""
    global last_tick_at, last_events
    before = world.snapshot()
    last_events = sim.step(world, plan)
    after = world.snapshot()
    last_tick_at = time.time()
    frame = _delta(before, after, last_events)
    hub.broadcast(frame)
    return frame


async def _tick_loop() -> None:
    while True:
        await asyncio.sleep(TICK_SECONDS)
        try:
            advance()
        except Exception as exc:  # never let one bad tick kill the loop
            print(f"[world-engine] tick failed: {exc!r}", flush=True)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    global _ticker_task
    _ticker_task = asyncio.create_task(_tick_loop())
    print(
        f"[world-engine] up tick={TICK_SECONDS}s seed={WORLD_SEED} "
        f"replayed={replayed} agents={len(world.agents)}",
        flush=True,
    )
    try:
        yield
    finally:
        if _ticker_task:
            _ticker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await _ticker_task


app = FastAPI(title="world-engine", version="0.1.0", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> JSONResponse:
    age = time.time() - last_tick_at
    healthy = age < max(10.0, TICK_SECONDS * 5)
    body = {
        "status": "ok" if healthy else "stale",
        "tick": world.tick,
        "tick_age_s": round(age, 2),
        "tick_seconds": TICK_SECONDS,
        "agents": len(world.agents),
        "clients": hub.size,
        "uptime_s": round(time.time() - started_at, 1),
    }
    return JSONResponse(body, status_code=200 if healthy else 503)


@app.get("/api/world/state")
async def get_state() -> dict[str, Any]:
    return world.snapshot()


@app.get("/api/world/contract")
async def get_contract() -> dict[str, Any]:
    return {
        "states": list(STATES),
        "events": list(EVENT_TYPES),
        "transitions": [
            {"from": frm, "event": ev, "to": to} for (frm, ev), to in TRANSITIONS.items()
        ],
        "tick_seconds": TICK_SECONDS,
        "seed": WORLD_SEED,
    }


def _token_ok(provided: str | None) -> bool:
    return TOKEN is None or provided == TOKEN


@app.post("/api/world/events")
async def post_event(
    payload: dict,
    x_world_token: str | None = Header(default=None),
) -> JSONResponse:
    if not _token_ok(x_world_token):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, 401)
    agent_id = str(payload.get("agent", ""))
    event_type = str(payload.get("type", ""))
    if not agent_id or not event_type:
        return JSONResponse({"ok": False, "reason": "agent and type are required"}, 422)

    before = world.snapshot()
    event = world.emit(
        agent_id,
        event_type,
        ticket=payload.get("ticket"),
        note=payload.get("note"),
        room=payload.get("room"),
        source=str(payload.get("source", "api")),
    )
    if event is None:
        return JSONResponse({"ok": False, "rejection": world.rejections[-1].as_dict()}, 409)

    after = world.snapshot()
    frame = _delta(before, after, [event])
    frame["type"] = "delta"
    hub.broadcast(frame)
    return JSONResponse({"ok": True, "event": event, "state": after}, 200)


@app.post("/api/world/tick")
async def manual_tick(x_world_token: str | None = Header(default=None)) -> JSONResponse:
    if not ALLOW_MANUAL_TICK:
        return JSONResponse({"ok": False, "reason": "manual tick disabled"}, 403)
    if not _token_ok(x_world_token):
        return JSONResponse({"ok": False, "reason": "unauthorized"}, 401)
    return JSONResponse({"ok": True, "frame": advance()}, 200)


@app.websocket("/ws/world")
async def ws_world(websocket: WebSocket) -> None:
    await websocket.accept()
    client = Client()
    hub.add(client)
    try:
        await websocket.send_json({"type": "snapshot", "world": world.snapshot()})
        while True:
            payload = await client.queue.get()
            await websocket.send_text(payload)
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        hub.remove(client)
