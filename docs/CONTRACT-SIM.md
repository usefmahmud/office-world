# Sim contract — `world/` core (W1 / WORLD-2)

The simulation core is a **pure function of state and seed**. This document is
the contract other waves build against; changing anything marked *frozen*
requires regenerating the golden file and a note on the ticket.

```python
from world import initial_state, step, run, state_sha256

s = initial_state(seed=12345)
s = step(s)                       # one tick
s = step(s, external_events)      # engine-fed events for this tick
s = run(s, 10_000)
state_sha256(s)                   # ef7d0d4c… (frozen, see tests/golden/)
```

## 1. Time

| Term | Meaning |
|---|---|
| tick | 1 simulated second |
| `DAY_LEN` | 28 800 ticks (one 8-hour day, 09:00 → 17:00, then wraps) |
| tick-of-day | `tick % DAY_LEN` — the clock the schedules read |

The sim never reads the wall clock. The engine (W2) decides how many ticks to
run per real second (1–2 Hz in production).

## 2. Frozen state machine

States: `idle`, `working`, `review`, `break`, `meeting`.

Frozen cycle: **idle → working → review → break → meeting → idle**.

Legal edges (`TRANSITIONS` in `world/entities.py`):

| from | event | to |
|---|---|---|
| idle | `task_start` | working |
| break | `task_start` | working |
| review | `task_start` | working |
| meeting | `task_start` | working |
| working | `task_done` | review |
| working | `handoff` | review |
| review | `handoff` | break |
| working | `break` | break |
| review | `break` | break |
| idle | `break` | break |
| idle / working / review / break | `meeting_open` | meeting |
| meeting | `meeting_close` | idle |

Special cases:

* `message` never changes state — it is a side effect (speech) only.
* `idle` is the explicit reset event and is legal from any state.
* **Anything else is rejected**: the agent keeps its state, and the snapshot
  counter `stats["rejected"]` goes up. Rejections are never silent and never
  clamp the agent into some other state.

## 3. Frozen event vocabulary

`task_start · task_done · message · meeting_open · meeting_close · break ·
handoff · idle`

Extra rules:

* `task_start` **must** carry `task_id` — a green desk is always attributable
  to a real ticket (risk R2 in `PLAN.md`). A `task_start` without one is
  rejected.
* `meeting_open` / `meeting_close` carry `participants`; the transition applies
  to every participant. Empty participants ⇒ just `agent_id`.
* `message` carries `text_id` (a key into the canned-lines content file) — the
  core never generates text and never calls an LLM.

## 4. Determinism rules (each one has a test)

1. **No float.** All geometry is integer centi-units; the snapshot is JSON with
   ints and strings only. (`test_snapshot_contains_no_floats`)
2. **No clock, no network, no global RNG.** `world/*.py` may not import
   `time`, `datetime`, `random`, `os`, `socket`, `requests`, `openai`, … —
   enforced by an AST scan. (`test_world_package_imports_no_clock_no_network_no_llm`)
3. **Seeded substreams addressed by label, not by draw order.** A stream is
   `Stream(seed, "schedule", agent_id)`; add a seventh agent and nobody else's
   day moves. (`test_substreams_are_addressed_by_label_not_by_draw_order`)
4. **Total order on events.** Events in a tick are applied in
   `(agent_id, kind, task_id, text_id, source)` order, so identical inputs can
   never apply in a different sequence. (`test_same_seed_twice_is_byte_identical`)
5. **Immutable state.** `step` never mutates its argument; every change builds
   a new frozen dataclass. (`test_step_does_not_mutate_its_input`)
6. **Golden hash.** 10 000 ticks from `seed=12345` must hash to
   `ef7d0d4c3ca0916558928599d5baeb5afdf5024609168ad494ef72c365383c35`
   (`sha256` of the canonical JSON snapshot).
   (`test_golden_hash_after_10k_ticks`)
7. **Event chain.** `WorldState.digest` is a running SHA-256 over every applied
   event in order — the cheap "did history diverge?" probe for the engine and
   for restart-resume checks.

Regenerate the golden file only on a deliberate contract change:

```bash
WORLD_REGEN_GOLDEN=1 pytest tests/test_sim.py -k golden
```

## 5. Movement

Straight lines only, one tick per step, integer interpolation between the leg's
start and target:

```
x(k) = x0 + round(dx * k / n),   n = ceil(max(|dx|, |dy|) / speed)
```

Agents occupy distinct standing spots in shared rooms (slot = roster index), so
six dots never stack on one pixel.

## 6. Snapshot shape (what W2 serves and W3 renders)

```jsonc
{
  "tick": 10000,
  "seed": 12345,
  "agents": [{
    "agent_id": "omar", "name": "عمر", "state": "working", "task_id": "T-omar-03",
    "x": 1200, "y": 3000, "room": "desks", "dest_room": "",
    "leg_k": 0, "leg_n": 0, "since_tick": 9870, "messages": 12
  }],
  "feed": [["omar", "task_start", "T-omar-03", ""]],   // last 25 events
  "stats": {"applied": 197, "rejected": 0, "messages": 146, "task_starts": 19, "...": 0},
  "digest": "0bb556b2…",
  "rooms": [{"room_id": "desks", "label": "قاعة العمل", "x": 200, "y": 200, "w": 7600, "h": 4000}]
}
```

`rooms` is static — the engine can ship it once on connect and send only
`{tick, agents-changed, feed-tail}` patches afterwards.

## 7. Day rules (rule-based life, no LLM)

* Every agent has a `Profile`: desk, walking speed, work length, review length,
  social weight (permille chance of saying something per idle tick).
* A cycle is `task_start → task_done → handoff|break → coffee → next task`.
  Which of `handoff` / `break` ends a review is decided by a seeded coin per
  cycle, so both legal edges get exercised.
* Two all-hands meetings per day (09:00 + 2 h and 09:00 + 5.5 h). Blocks are
  clipped at meeting boundaries: the machine has no "resume" state, so the
  meeting ends and the agent goes `idle`, then the next cycle's `task_start`
  puts them back to work. Result: `stats["rejected"] == 0` on a healthy day.
* Ambient chatter is rate limited per agent (`MESSAGE_MIN_GAP = 45` ticks) and
  emits only a `text_id`.

## 8. What the core deliberately does **not** do

No I/O, no persistence, no WebSocket, no HTTP, no LLM, no pathfinding, no
needs/moods, no 3D. Persistence and transport belong to W2 (`world-engine`);
schedules for meetings/reminders for W4 belong in `office/`, not here.
