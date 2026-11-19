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
| `TICK` | `2` | seconds between ticks |
| `WORLD_SEED` | `7` | seed for the deterministic day plan |
| `WORLD_STATE_DIR` | unset | directory for the append-only `events.jsonl` (replayed on boot) |
| `WORLD_TOKEN` | unset | when set, `X-World-Token` is required on POST routes |
| `WORLD_ALLOW_MANUAL_TICK` | `0` | enables `POST /api/world/tick` (tests only) |

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
