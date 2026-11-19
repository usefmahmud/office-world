# Shipping record — WORLD-8

Owner: yassin · Date: 2026-11-19 · Method: docker compose (third service pattern copied from sitta-chat)

## Runtime

| Container | Image | Published | Memory limit | CPU limit |
|---|---|---|---|---|
| `world-engine` | built from `apps/world-engine` | internal only | 384 MB | 0.75 |
| `world-web` | built from `apps/world-web` | `${BIND_HOST}:${WEB_PORT}` (8095) | 96 MB | 0.25 |

`.env` (git-ignored, mode 600) carries `WEB_PORT`, `BIND_HOST` and `TICK`
(`WORLD_TICK` is the ticket's name for it; compose maps `${TICK}` into the engine's `TICK`).
No interface other than the web port is published; the engine stays on the internal network.

## Verification (measured, not assumed)

| Check | Result |
|---|---|
| `GET /` | **200 · 36,196 bytes** |
| `GET /api/world/state` | snapshot with **6 agents** (`Hakim, Layla, Maryam, Omar, Salim, Yassin`), tick advancing |
| `WS /ws/world` | live deltas confirmed — tick `159` received after connect |
| Restart resume | `docker compose restart world-engine` → engine healthy again, page resumed without reload |
| Memory — `world-engine` | **33.4 MiB / 384 MiB (8.7%)** |
| Memory — `world-web` | **7.2 MiB / 96 MiB (7.5%)** |
| CPU (moment) | engine 0.14% · web 2.60% |

All numbers above were taken from the running containers with `docker stats` and `curl`,
not from the design documents.

## Verification detail (W5 pass — the evidence behind each row)

Stack raised as `docker compose config -q` → `docker compose build` → `docker compose create`
→ `docker compose start` (both services reported `(healthy)` on the readiness gate), from
`~/ai-team/repos/office-world` with the branch tip checked out.

| Check | Raw evidence |
|---|---|
| Page | `GET /` → `200 · text/html · 36,196 bytes`, title `Agents Office World — floor & mission control` |
| Snapshot | `GET /api/world/state` → `keys=[tick, seed, ts, agents, meeting, counts]`, `agents=6` (`hakim, layla, maryam, omar, salim, yassin`), `tick 69 → 79` over 20 s (TICK=2) |
| WS over the **published** port | first frame `type=snapshot tick=85 agents=6`, then `type=delta` every 2 s with 0–2 patches (`tick 86…89`) |
| Dots moving | DOM sample of `g.agent[data-agent]` at 3 s intervals: 5 of the 6 dots changed `transform` inside a 135 s window (hakim ×4, salim ×4, layla ×2, omar ×2, yassin ×2), maryam had already moved `876,184 → 520,96` in the preceding 60 s window — all six observed moving |
| Restart resume | live WS held, `docker compose restart world-engine` → old socket `ConnectionClosedError`, client recovered in 3 attempts / 2.0 s with a fresh `snapshot tick=111`; state `tick 109 → 111`, 6 agents intact; boot line `[world-engine] up tick=2.0s seed=7 replayed=125 agents=6` |
| Page without reload | browser tab on `http://100.86.40.89:8095/`: `window.__noReloadMarker` survived the restart, `performance.getEntriesByType('navigation').length == 1`, header chip returned to `live`, tick `119 → 127 → 131` |
| Caps applied | `docker inspect`: engine `memory=402653184 · nanocpus=750000000 · pids=128 · PortBindings={}`, web `memory=100663296 · nanocpus=250000000 · pids=64 · PortBindings={"80/tcp":[{"HostIp":"100.86.40.89","HostPort":"8095"}]}` |
| Live RSS | `docker stats --no-stream`: engine `34.28MiB / 384MiB (8.93%)`, web `3.09MiB / 96MiB (3.22%)`, 5 and 3 PIDs |
| Binding | `ss -ltn` → `LISTEN 100.86.40.89:8095` only; `0.0.0.0` never binds the port |
| Suite on the shipped tree | `pytest -q` → `133 passed, 1 warning, 6 subtests passed` |

### Known gap (not blocking)

`memswap_limit` is not set, so Docker defaults it to 2× `mem_limit` (engine 768 MB,
web 192 MB): under host swap pressure the engine may page out instead of being killed at
384 MB. One-line fix in `docker-compose.yml` when a follow-up ticket allows touching the
compose file: `memswap_limit: 384m` (engine) / `96m` (web).

## Reproduce

```bash
cp .env.example .env      # then set WEB_PORT, BIND_HOST, WORLD_TICK
docker compose up -d --build
curl -s localhost:8095/api/world/state | head
docker compose restart world-engine   # world must resume, page must not reload
```
