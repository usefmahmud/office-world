# Office World

A single-screen virtual office where the six agents of the team each have a desk,
live a plausible working day, talk and meet — driven entirely by real state
(Atlas tickets, presence, chat events). The world never invents state.

## Rules (v1)

- One floor · six desks · six event types · straight-line movement only.
- Deterministic core: `state(t+1) = step(state(t), seed)` — replayable, testable.
- No LLM in the tick loop: events are rule-based; speech is pre-generated.
- One append-only event log is the canon; the renderer only replays it.
- Hard resource budget enforced by Docker.

## Layout

```
world/        # deterministic simulation core (pure Python)
office/       # schedules, meetings, reminders
apps/         # world-engine (FastAPI + WS) and world-web (static SVG floor)
docs/         # contract (dayplan, events) and decisions
```

See `docs/PLAN.md` for the full plan and `docs/CONTRACT.md` for the frozen event vocabulary.

## Run the deterministic core

```bash
pytest -q            # 27 tests, incl. the 10 000-tick golden hash
python3 -c "from world import *; print(state_sha256(run(initial_state(12345), 10_000)))"
```

The core contract (state machine, event vocabulary, determinism rules, snapshot
shape) is frozen in `docs/CONTRACT-SIM.md`.
