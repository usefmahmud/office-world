# world-engine

FastAPI runtime for the office world: a 2 s tick loop, a read-only snapshot API
and a WebSocket stream of deltas. **No LLM runs in this process.**

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | liveness + tick freshness (Docker healthcheck) |
| GET | `/api/world/state` | full snapshot (`tick`, `agents[]`, `meeting`, `counts`) |
| GET | `/api/world/contract` | frozen states, events, transition table |
| POST | `/api/world/events` | single-writer ingest: `{agent, type, ticket?, note?, room?}` |
| POST | `/api/world/tick` | one manual step (only when `WORLD_ALLOW_MANUAL_TICK=1`) |
| WS | `/ws/world` | `{"type":"snapshot",...}` on connect, then `{"type":"delta",...}` per tick |

Invalid transitions are **refused** (HTTP 409 / dropped) and never guessed: the
world only moves along `idle → working → review → break → meeting → idle`.

## Config

| Env | Default | Meaning |
|---|---|---|
| `TICK` | `2` | real seconds between ticks (how fast the loop runs) |
| `WORLD_SIM_SECONDS_PER_TICK` | `60` | simulated seconds one tick stands for — the clock scale the page reads (WORLD-9) |
| `WORLD_SIM_T0` | `2026-01-05T09:00:00Z` | contract `sim.t0`; every timestamp is `t0 + tick × sim_seconds_per_tick` |
| `WORLD_SEED` | `7` | seed for the deterministic day plan |
| `WORLD_STATE_DIR` | unset | directory for the append-only `events.jsonl` (replayed on boot) |
| `WORLD_TOKEN` | unset | when set, `X-World-Token` is required on POST routes |
| `WORLD_ALLOW_MANUAL_TICK` | `0` | enables `POST /api/world/tick` (tests only) |

With the defaults (`TICK=2`, `WORLD_SIM_SECONDS_PER_TICK=60`) one simulated minute passes per tick,
so the 09:00–18:00 day (540 ticks) plays out in about 18 minutes of wall time.

## Snapshot extensions for the renderer

`GET /api/world/state` also carries `sim_time`, `sim_seconds_per_tick` and `recent_messages`
(the last ≤ 30 `message` events, oldest first) so the page can draw the clock and the transcript
without a second source; every `delta` carries `events`, the events applied in that frame.

## Tests

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt pytest httpx
.venv/bin/python -m pytest
```

Covers the frozen state machine (allowed + forbidden transitions), 10 000-tick
determinism (same seed → identical projection), replay-from-log, and the HTTP/WS
contract.

`app/sim.py` is a **provisional** rule-based life loop; W1's deterministic core
(`world/sim.py`) replaces it without touching `main.py`.
