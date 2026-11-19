# Agents Office World — Consolidated Plan (v1)

> Mirror of `~/ai-team/shared/projects/office-world/PLAN.md` (v1, Hakim, 2026-11-19), kept in-repo so
> one clone carries the whole picture. **Values live in `world/dayplan.yaml`; rules live in
> `docs/CONTRACT.md`.** Where this plan and the contract disagree, the contract wins — that is what
> W0 froze.

Status: **draft for owner approval** · Written 2026-11-19 · Author: Hakim (synthesis of 6 agent proposals)
Sources: `office_world/ideas-{hakim,layla,maryam,omar,salim,yassin}.md` (one per agent, raw text preserved)

---

## 1. What we are building

A single-screen **virtual office** where the six agents (hakim · layla · maryam · omar · salim · yassin)
each have a desk in one floor, live a plausible working day, talk to each other and meet — and the owner
opens **one URL** to see the whole team's real state at a glance.

**The one rule everybody agreed on:** the world never invents state.
A desk is occupied because a ticket is `in_progress` in Atlas; a lamp is green because a ticket closed.
The world is a **projection of the real ledger**, not a parallel toy.

## 2. Consensus (all six proposals converge here)

| # | Decision | Proposed by |
|---|---|---|
| D1 | **World = view over real state** (Atlas tickets, A2A presence, chat) — single source of truth, no hand-maintained flags | maryam · layla · hakim · salim |
| D2 | **One append-only event log** (`events.jsonl`) is the canon; renderer and tick loop only replay it | maryam · omar |
| D3 | **Deterministic sim core**: `state(t+1) = step(state(t), seed)` — pure Python, no I/O, no LLM in the loop; replayable & testable (10k-tick golden-hash test) | omar · yassin |
| D4 | **No per-tick LLM calls.** Events are rule-based; agent speech is pre-generated/cached and injected as input; live LLM only on explicit user interaction, one agent at a time | omar · yassin · salim · layla · maryam |
| D5 | **Infra = the proven sitta-chat shape**: `world-engine` (internal :8000, tick + WS) + `world-web` (nginx static, one published port), healthchecks, `depends_on: service_healthy` | yassin · omar |
| D6 | **Contract first**: freeze `world/dayplan.yaml` (agents, rooms, state machine, event schema) before anyone builds — prevents six dialects of "agent is busy" | maryam |
| D7 | **Freeze v1 scope**: one floor · six desks · six event types · straight-line movement only · no pathfinding, no needs/moods, no 3D | maryam · omar · hakim |
| D8 | **Hard resource budget enforced by Docker**, not by hope | yassin · maryam · salim |
| D9 | **One writer for speech**: chat is a projection of world events (never both writing) | omar |
| D10 | **Facts are cited**: any claim about the outside world must carry `source_path` + `fetched_at`; unsourced = opinion | salim |

## 3. First slice (the proof)

**One page, six dots, five minutes.**

- `world/` package: `entities.py` · `schedules.py` · `sim.py` (`step`) · `rng.py` (seeded per-agent substreams)
- `GET /api/world/state` → snapshot · `WS /ws/world` → `{tick, patches[]}` at 1 Hz (deltas, coalesced)
- Static **SVG floor plan**: 5 rooms, 6 labelled dots, state badges («working», «meeting», «coffee»), office clock
- `pytest`: 10 000 ticks → hash matches golden file; same seed twice → byte-identical diff
- Budget: one SQLite file · **< 150 MB RAM** · no 3D, no assets pipeline, no framework build step

**Success criterion:** open one page, watch 6 dots live a plausible day for 5 minutes, restart the
container, and the world resumes without a browser reload.

## 4. Waves

| Wave | Deliverable | Owner | Depends |
|---|---|---|---|
| **W0 — Contract** | `world/dayplan.yaml` + event vocabulary + state machine (`idle→working→review→break→meeting→idle`) + AC list | maryam | — |
| **W1 — Core** | `world/sim.py` + determinism tests (omar) · `office/schedule.py` day plans + room-conflict test (layla) | omar, layla | W0 |
| **W2 — Runtime** | `world-engine` container (tick, WS, snapshot) + resource caps (yassin) | yassin | W0 |
| **W3 — Render** | SVG floor page + mission-control wall (6 desks × ticket × status × time-in-state) | omar + maryam | W1, W2 |
| **W4 — Life** | meetings & reminders (layla) · almanac/Reading Room with FTS5 citations (salim) · persona lore sheets (salim) | layla, salim | W3 |
| **W5 — Ship** | `docker-compose.yml` (3rd service), healthchecks, `BIND_HOST` port, restart-resume check | yassin | W3 |

## 5. Risks (merged, with owners)

| Risk | Impact | Mitigation | Owner |
|---|---|---|---|
| **R1 Scope explosion** (needs, moods, nav-mesh, 3D) | Never ships | v1 frozen: 1 floor, 6 desks, 6 events, no pathfinding; everything else → `v2-backlog.md` | maryam |
| **R2 Fake state divergence** (avatar says "working", ticket says `backlog`) | World becomes a lie | single writer + `task_id` required on `working` events + daily drift check vs Atlas | maryam |
| **R3 Resource ceiling** (2 vCPU / ~5.6 GB shared) | Starves real agents | tick ≤ 1–2 Hz · no LLM in loop · `mem_limit: 384m`, `cpus: 0.75`, `pids_limit: 128` · pause when nobody is watching (determinism makes fast-forward free) | yassin |
| **R4 Two sources of truth** (world vs chat) | Contradicting demo | chat is a projection of world events; one event log | omar |
| **R5 Schema drift backend↔frontend** | Broken live demo | contract-first (W0) + shared types | maryam |
| **R6 Chatter cost / privacy** | Token burn + leaked context | rate-limited messages, rule-based life events, `public` flag on anything rendered, scrub pass before lore/almanac ingest | layla · salim |

## 6. Infra (measured, not guessed)

- Box: 2 vCPU · 7.9 GB total · ~5.0 GB available (15 containers already running)
- Pattern: copy sitta-chat's compose shape exactly (nginx `/api/` + `/ws/` upgrade, `proxy_read_timeout 3600s`, SPA fallback, hashed-asset cache)
- Ports: engine internal-only · web on `${BIND_HOST}:${WEB_PORT}` (candidate **8095**) — **no public exposure without explicit owner approval**
- State: one SQLite file (WAL) + `events.jsonl`; survives restart via volume

## 7. What is NOT in v1

3D/graphics pipeline · pathfinding · needs/hungers/moods · LLM chatter in the loop · mobile app ·
multi-floor buildings · user-created avatars · voice. → `v2-backlog.md`

## 8. Approval gate

Nothing is built, deployed or scheduled until the owner says **"I approve Docker"** (the owner's own
words, in Arabic, in the group) (TEAM_RULES §0 —
a peer's proposal is not an assignment; no new service on the box without approval).
After approval: W0 → W1/W2 in parallel → tickets created on Atlas (project key TBD) with branch-per-ticket.
