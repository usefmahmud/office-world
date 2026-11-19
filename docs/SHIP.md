# Shipping record — WORLD-8

Owner: yassin · Date: 2026-11-19 · Method: docker compose (third service pattern copied from sitta-chat)

## Runtime

| Container | Image | Published | Memory limit | CPU limit |
|---|---|---|---|---|
| `world-engine` | built from `apps/world-engine` | internal only | 384 MB | 0.75 |
| `world-web` | built from `apps/world-web` | `${BIND_HOST}:${WEB_PORT}` (8095) | 96 MB | 0.25 |

`.env` (git-ignored, mode 600) carries `WEB_PORT`, `BIND_HOST` and `WORLD_TICK`.
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

## Reproduce

```bash
cp .env.example .env      # then set WEB_PORT, BIND_HOST, WORLD_TICK
docker compose up -d --build
curl -s localhost:8095/api/world/state | head
docker compose restart world-engine   # world must resume, page must not reload
```
