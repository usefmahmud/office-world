# Agents Office World — World Contract (v1.0.0)

**Status:** `FROZEN` — this document plus `world/dayplan.yaml` is the single reference every wave
builds against.
**Owner:** maryam (delivery) · **Ticket:** WORLD-1 (W0) · **Consumers:** W1 core, W2 runtime, W3 render,
W4 life, W5 ship.
**Change rule:** any change to a name, a field, a type, an enum value or an edge in this contract is a
**contract change**: bump `version` in `world/dayplan.yaml`, open one ticket, and update `docs/AC.md`
in the same commit. Renames are breaking; additions are a minor bump; removals are a major bump.

Machine-readable source of truth: **`world/dayplan.yaml`**. This document is the prose around it.
If the two ever disagree, the YAML wins for values, this document wins for rules — and that disagreement
is itself a bug (code `W-CFG-01`).

---

## 1. Glossary and closed enums

Everything the world can say is one of the values below. There is no free text on the wire; prose lives
inside `payload.text` and is never parsed by the renderer or the sim.

### 1.1 Agent ids (`agent`, `target`)

| id | Role | Home desk | Persona tone |
|---|---|---|---|
| `hakim` | orchestration / dispatch | D1 | terse, directive |
| `layla` | lifecycle, meetings, reminders | D2 | warm, scheduling-minded |
| `maryam` | delivery / PM, acceptance | D3 | crisp, asks for evidence |
| `omar` | application development | D4 | dry, technical |
| `salim` | research, almanac, citations | D5 | curious, always cites |
| `yassin` | infrastructure, runtime | D6 | factual, capacity-first |

Exactly six. Order matters: the declaration order in `dayplan.yaml → agents` is the **tie-break order**
for same-tick events (§3.4).

### 1.2 Room ids (`room`)

| id | Kind | Capacity | What it is for |
|---|---|---|---|
| `bullpen` | open floor | 6 | the six desks; default room |
| `meeting_room` | meeting | 6 | standup, daily review |
| `review_corner` | review | 2 | laptop stand where a reviewer sits |
| `kitchen` | break | 4 | breaks, coffee |
| `reading_room` | focus | 2 | almanac / reading (salim) |

Five rooms. A `room` is the room the agent occupies **after** the event has been applied (§4).

### 1.3 State names (`state`)

`idle` · `working` · `review` · `break` · `meeting` — five states, no others. Frozen by the owner's
brief; `idle` is the initial state.

### 1.4 Event types (`type`)

`task_start` · `task_done` · `message` · `meeting_open` · `meeting_close` · `break` · `handoff` · `idle`

Eight types, closed. **Documented decision (D-01):** `PLAN.md` §7 still says "six event types" from the
first scope note; the enumerated vocabulary was extended to eight when `handoff` and `idle` were given
their own names instead of being encoded inside `message`. The authoritative count is **8**, and
`world/validate_dayplan.py` asserts it. Any future type is a major bump and a v2 item.

---

## 2. Determinism rules (non-negotiable)

The sim is `state(t+1) = step(state(t), seed)`: a pure function. These six rules are what makes replay
byte-identical, and each one has an acceptance criterion in `docs/AC.md`.

| # | Rule | Why it is a contract rule |
|---|---|---|
| **DT-1** | `step()` performs **no I/O** — no network, no disk, no clock, no `random` module globals, no environment reads. Inputs: state + config. Outputs: new state + ordered event list. | Testable in isolation, 10 000 ticks in milliseconds |
| **DT-2** | `ts` is **derived**, never read: `ts = t0 + tick * tick_seconds`, `t0` fixed in `dayplan.yaml`. No `datetime.now()` anywhere under `world/`. | Wall-clock time breaks replay on a different day |
| **DT-3** | Coordinates are **integers** (pixels). No float accumulation in positions. | Float drift makes the golden hash unstable across platforms |
| **DT-4** | RNG is **per-agent, per-tick, per-purpose**: `Random(sha256(f"{seed}|{agent}|{tick}|{purpose}"))`. One generator is never shared between agents or purposes. | Shared RNG makes output depend on call order |
| **DT-5** | Iteration is over **ordered sequences** (the `agents`/`rooms` lists in YAML), never over `set` or bare `dict` keys, in any code path that emits events. | Hash randomization reorders sets between processes |
| **DT-6** | Event order inside one tick: **agent declaration order**, then **rule priority** (lower number first), then `seq` is assigned. `seq` is gapless and monotonic across the whole log. | A stable order is the only thing that makes "same seed → same file" true |

The golden test: 10 000 ticks from seed `42` → `sha256(events.jsonl)` must equal
`world/tests/golden/seed42-10k.sha256`. Same seed twice → `diff` is empty.

---

## 3. Event envelope

One JSON object per line (`events.jsonl`, append-only). Every event carries the envelope; the payload
adds type-specific fields. Required means: rejecting the event is mandatory (code `W-EVT-02`).

| Field | Type | Req | Constraints |
|---|---|---|---|
| `seq` | `int` | ✓ | Gapless, monotonic, starts at `1`; assigned by the single writer |
| `tick` | `int` | ✓ | `>= 0`; sim tick at 1 Hz |
| `ts` | `str` | ✓ | ISO-8601 UTC, `YYYY-MM-DDTHH:MM:SSZ`, derived per DT-2 |
| `agent` | `str` | ✓ | Enum §1.1 |
| `type` | `str` | ✓ | Enum §1.4 |
| `room` | `str` | ✓ | Enum §1.2 — the room the agent is in **after** the event |
| `state` | `str` | ✓ | Enum §1.3 — the state the agent is in **after** the event |
| `task_id` | `str \| null` | ✓ (key present) | `null` allowed only for the types in §5 that say so. Format `[A-Z][A-Z0-9]*-\d+` |
| `target` | `str \| null` | ✓ (key present) | Agent id, or `null` |
| `payload` | `object` | ✓ | Shape fixed per type, §5. Unknown keys → `W-EVT-03` |

Two hard rules:

* **`state` and `room` describe the post-state.** A consumer that applies the event gets exactly these
  values; it never recomputes them. This is what makes "replay" and "step" agree (AC-W1-04).
* **One event = one transition, except self-loops.** `message` and `handoff` do not change state; every
  other type maps to exactly the post-state in the table in §4.

### 3.1 Example (one line of `events.jsonl`)

```json
{"seq":41,"tick":3120,"ts":"2026-01-05T09:52:00Z","agent":"omar","type":"task_done","room":"review_corner","state":"review","task_id":"WORLD-2","target":null,"payload":{"title":"deterministic sim core","duration_ticks":3120,"artifact":"world/sim.py","result":"ok"}}
```

---

## 4. State machine (frozen)

States, initial state and the canonical cycle, exactly as fixed by the brief:

```
canonical cycle:  idle -> working -> review -> break -> meeting -> idle
initial state:    idle
```

A **transition table**, not prose, is the law. `(type, from_state)` must appear here or the event is
rejected with `W-STATE-01`. There are no other legal moves.

| `type` | legal `from_state` | resulting `state` | notes |
|---|---|---|---|
| `task_start` | `idle`, `review` | `working` | `review → working` is **rework** on the same or a new ticket |
| `task_done` | `working` | `review` | the only way into `review` |
| `message` | `idle`, `working`, `review`, `break`, `meeting` | unchanged (self-loop) | rate-limited (§6.3) |
| `meeting_open` | `idle`, `working`, `review`, `break` | `meeting` | a participant can join from a break |
| `meeting_close` | `meeting` | `idle` | back to the floor; the next `task_start` resumes work |
| `break` | `working`, `review` | `break` | scheduled windows only |
| `handoff` | `working`, `review` | unchanged (self-loop) | requires `target != null` |
| `idle` | `idle`, `working`, `review`, `break`, `meeting` | `idle` | end of day, or a timeout recovery |

The canonical cycle is fully reachable: `idle →(task_start) working →(task_done) review →(break) break
→(meeting_open) meeting →(meeting_close) idle`. The extra edges above are the complete interrupt set —
`working → break` (a coffee during focus) and `review → working` (rework) are the two that are not on the
happy path.

### 4.1 Per-state timeouts (`guards.state_max_ticks`)

Exceeding a timeout emits `W-STATE-02` and forces a recovery event; the world never freezes silently.

| state | max ticks | max wall time | recovery |
|---|---|---|---|
| `idle` | 900 | 15 min | in work hours: pull the next ticket → `task_start`; else stay idle, warn |
| `working` | 1500 | 25 min | `message` or `handoff` required, else `W-STATE-02` |
| `review` | 600 | 10 min | `task_done` is already done; recovery is `break` or `task_start` |
| `break` | 1200 | 20 min | forced back to `working`/`idle` |
| `meeting` | 3600 | 60 min | `meeting_close` forced |

### 4.2 Real-state coupling (anti-drift)

`task_start` and `task_done` **must** carry `task_id` (`W-EVT-05` otherwise). A `task_start` whose
`task_id` is not a real ticket assigned to that agent in Atlas is `W-DRIFT-01`: the world is a projection
of the ledger, so a desk may not claim work that does not exist. The renderer shows the ticket key from
the ledger, never from a hand-maintained flag.

---

## 5. Event catalogue

`payload` per type. Extra keys → `W-EVT-03`; a missing required payload key → `W-EVT-02`.

### 5.1 `task_start` — begin work on a real ticket

| payload key | type | req | constraint |
|---|---|---|---|
| `title` | `str` | ✓ | 1–120 chars, from the ledger |
| `source` | `str` | ✓ | `atlas` \| `queue` — where the ticket was read |
| `priority` | `str` | ✗ | `low`\|`normal`\|`high` — default `normal` |

Envelope extras: `task_id` required, `target` = `null`, `state` = `working`.

### 5.2 `task_done` — hand the ticket to review

| payload key | type | req | constraint |
|---|---|---|---|
| `duration_ticks` | `int` | ✓ | `>= 1`, ticks spent in `working` |
| `artifact` | `str` | ✗ | repo-relative path of the deliverable |
| `result` | `str` | ✓ | `ok` \| `blocked` (`blocked` also emits `handoff` to the owner) |

Envelope extras: `task_id` required, `state` = `review`, `room` usually `review_corner` or `bullpen`.

### 5.3 `message` — agent speaks to an agent, about a ticket

| payload key | type | req | constraint |
|---|---|---|---|
| `text` | `str` | ✓ | 1–280 chars; pre-generated or templated, **never** an LLM call in the loop (§7) |
| `text_source` | `str` | ✓ | `template` \| `cached` \| `human` (`llm` is illegal in the tick loop) |
| `about` | `str` | ✗ | `task` \| `meeting` \| `break` — default `task` |

Envelope extras: `target` required (an agent id ≠ `agent`), state unchanged, both agents must be in the
same `room` (else `W-EVT-04`). Rate limit §6.3.

### 5.4 `meeting_open` — a room becomes a meeting

| payload key | type | req | constraint |
|---|---|---|---|
| `topic` | `str` | ✓ | 1–80 chars (`standup`, `daily review`, …) |
| `participants` | `str[]` | ✓ | 2–6 agent ids, must include `agent` |
| `planned_ticks` | `int` | ✓ | 60–3600 |

Envelope extras: `room` must be `meeting_room` (v1), `task_id` = `null`, `state` = `meeting`.
Occupancy over capacity → `W-CAP-01`.

### 5.5 `meeting_close` — the meeting ends

| payload key | type | req | constraint |
|---|---|---|---|
| `actual_ticks` | `int` | ✓ | `>= 1` |
| `decisions` | `str[]` | ✗ | 0–5 short strings; no free-form context |

Envelope extras: `state` = `idle`, `room` = `bullpen` after the agents walk back (the sim emits the walk
as part of applying this event, which is exactly one movement — see DT-3).

### 5.6 `break` — a real break, in the kitchen

| payload key | type | req | constraint |
|---|---|---|---|
| `kind` | `str` | ✓ | `coffee` \| `lunch` \| `stretch` |
| `planned_ticks` | `int` | ✓ | 60–2700 |

Envelope extras: `room` = `kitchen`, `state` = `break`, `task_id` = `null`.

### 5.7 `handoff` — work moves to the next owner

| payload key | type | req | constraint |
|---|---|---|---|
| `reason` | `str` | ✓ | `review` \| `blocked` \| `expertise` \| `shift_end` |
| `note` | `str` | ✗ | 1–200 chars |

Envelope extras: `target` required, `task_id` required, state unchanged.

### 5.8 `idle` — end of day / timeout recovery

| payload key | type | req | constraint |
|---|---|---|---|
| `reason` | `str` | ✓ | `end_of_day` \| `timeout` \| `no_work` |
| `ticks_idle` | `int` | ✗ | ticks spent in the previous state |

Envelope extras: `task_id` = `null`, `state` = `idle`.

---

## 6. Codes (closed sets)

### 6.1 Validation and error codes

Raised by the guard in `step()` (event rejected, never written) or by the API. Codes are stable strings;
the WS/HTTP mapping is fixed so the frontend never parses prose.

| code | http | raised when | recovery |
|---|---|---|---|
| `W-EVT-01` | 422 | unknown `type` | event dropped |
| `W-EVT-02` | 422 | missing required field (envelope or payload) | event dropped |
| `W-EVT-03` | 422 | wrong field type, or unknown/extra key | event dropped |
| `W-EVT-04` | 422 | unknown `agent`/`room`/`target`, or `message` across rooms | event dropped |
| `W-EVT-05` | 422 | `task_id` required by the type and `null` | event dropped |
| `W-STATE-01` | 409 | `(type, from_state)` not in §4 | event dropped |
| `W-STATE-02` | 409 | state timeout exceeded (§4.1) | a recovery event is emitted |
| `W-CAP-01` | 409 | room occupancy > capacity | event dropped |
| `W-RATE-01` | 429 | `message` rate exceeded (§6.3) | event dropped, counted |
| `W-SEQ-01` | 500 | `seq` gap / duplicate on replay | replay aborts, snapshot frozen |
| `W-DRIFT-01` | 409 | `task_id` not a real ticket for that agent in the ledger | event dropped + warning |
| `W-DRIFT-02` | — | agent `idle` beyond timeout during work hours | warning in the drift report |
| `W-CFG-01` | 500 (boot) | `dayplan.yaml` violates its own schema | process refuses to start |
| `W-DET-01` | test | golden hash mismatch | CI failure, no merge |

`409`/`422` are **not** user errors in normal operation — they mean the sim tried to lie. They are
counted and surfaced in the drift report, never silently swallowed.

### 6.2 Exit/health codes

`world-engine` health: `200 {"status":"ok","tick":<int>,"last_event_seq":<int>}`; `503` if the tick loop
has not advanced for 30 s (stall is a failure, not a warning).

### 6.3 Rate limits (enforced by the sim, not by the renderer)

`message` ≤ 1 per agent per 300 ticks · ≤ 3 `meeting_open` per day · ≤ 4 `break` per agent per day ·
`handoff` ≤ 2 per agent per hour. Exceeding → `W-RATE-01`, event dropped.

---

## 7. No intelligence in the movement loop

The owner's rule, stated once and binding for every wave:

* Event generation is **rule-based** and comes from `dayplan.yaml` + the ledger.
* `message.payload.text` comes from a **template** or a **cache**; `text_source: llm` is illegal inside
  the tick loop and rejected by the guard. Live LLM output only exists when a human explicitly interacts
  with one agent, one agent at a time, and it enters the world as `text_source: human`.
* No network call, no model, no token from anything under `world/`. AC-W0-06 greps for it.

---

## 8. Geometry and movement (straight lines only)

Canvas `1200 × 700` px, origin top-left, integer coordinates. Movement is a straight line between the
agent's current point and the target point, at `speed_px_per_tick` (default `8`). Room-to-room travel uses
the room `anchor` (doorway) as a waypoint, so the path is at most three segments: `here → anchor(A) →
anchor(B) → target`. No pathfinding, no collision, no nav-mesh in v1.

Positions live in the state, not in events: an event carries `room` and `state`; the tick loop interpolates
`x,y` deterministically from the agent's last arrival point and the tick count, so replay reproduces the
same pixels without storing them.

---

## 9. Wire format

### 9.1 `GET /api/world/state` — snapshot

```json
{"version":"1.0.0","tick":3120,"sim_time":"2026-01-05T09:52:00Z","seed":42,
 "agents":[{"id":"omar","state":"working","room":"bullpen","task_id":"WORLD-2","elapsed_ticks":840,"x":320,"y":300}],
 "rooms":[{"id":"meeting_room","occupants":[]}],
 "last_event_seq":41}
```

Field types are fixed: `tick`/`elapsed_ticks`/`x`/`y`/`last_event_seq` are `int`, `seed` is `int`,
`task_id` is `str|null`, everything else `str`. The snapshot is exactly the state a fresh client needs;
no second call.

### 9.2 `WS /ws/world` — patches at 1 Hz

On connect: `{"type":"snapshot", ...same as §9.1}`.
Then, at most once per tick: `{"type":"patch","tick":3121,"last_event_seq":42,"agents":[{"id":"omar","state":"review","room":"review_corner","task_id":"WORLD-2","elapsed_ticks":0,"x":840,"y":380}]}`.
Only changed agents appear; a patch is **coalesced** — if an agent changed twice in one tick, one patch
entry with the final values. Keepalive `{"type":"ping","tick":<n>}` every 30 s. Rev `1` of the schema —
the renderer must reject unknown `type` values and keep drawing.

---

## 10. Acceptance criteria per wave

Full, checkable list with commands: **`docs/AC.md`**. The gate per wave:

| Wave | Gate (one line) | AC ids |
|---|---|---|
| **W0 — Contract** | `world/dayplan.yaml` parses, six agents, five rooms, eight event types, five states, transition table total over the vocabulary; this file and `docs/AC.md` exist and contain no Arabic in repo docs | AC-W0-01 … AC-W0-09 |
| **W1 — Core** | `pytest` green: golden hash on 10 000 ticks, replay equality, no wall-clock/no-LLM imports under `world/` | AC-W1-01 … AC-W1-06 |
| **W2 — Runtime** | engine boots from the YAML, `/api/world/state` matches the schema, WS delivers ≥ 1 patch/s, resume after restart without a browser reload, RAM under budget | AC-W2-01 … AC-W2-06 |
| **W3 — Render** | one page, six dots, five rooms, state badges, clock; every dot's badge equals the snapshot; 30 fps and < 150 MB RSS on 2 vCPU | AC-W3-01 … AC-W3-06 |
| **W4 — Life** | meetings/reminders fire from the schedule; almanac answers carry `source_path` + `fetched_at`; persona lore never leaks private context | AC-W4-01 … AC-W4-05 |
| **W5 — Ship** | compose brings the stack up under caps, healthchecks pass, one published port only, restart-resume verified — **only after the owner approves Docker** | AC-W5-01 … AC-W5-05 |

W0 closes when the table above exists, the lint passes and every downstream owner has signed that they
can build against it. It does not close when the files merely exist.

---

## 11. Decisions recorded here

| id | decision | rationale |
|---|---|---|
| **D-01** | Event vocabulary is **8** types, not the "six" left in `PLAN.md` §7 | `handoff` and `idle` were split out of `message`/implicit state; count fixed at 8 and asserted |
| **D-02** | `state` and `room` on the event are **post-state** | removes the recompute path that makes replay and step disagree |
| **D-03** | `review` is a state, `review_corner` is a room | the ticket is under review while the person may still be at their desk |
| **D-04** | `meeting_close` lands in `idle`, never straight into `working` | matches the frozen cycle; the next `task_start` resumes work and keeps the log readable |
| **D-05** | Coordinates are integers and live in state, not in events | DT-3 + no pixel drift in the golden hash |
| **D-06** | `ts` is derived from `t0 + tick` | DT-2; a replay tomorrow must produce today's file |
| **D-07** | Illegal transitions are counted, not dropped silently | `W-STATE-01` counts appear in the drift report — a lie is visible |
| **D-08** | v1 has no pathfinding, needs, moods, 3D, LLM chatter | frozen scope (PLAN §7); anything else goes to `v2-backlog.md` |

---

*v1.0.0 · frozen 2026-11-19 · WORLD-1 · the world may be wrong, but it may not be invented.*
