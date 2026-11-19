# Agents Office World — Acceptance Criteria (v1.2.0)

Companion to `docs/CONTRACT.md` and `world/dayplan.yaml`. One rule for this file: **every criterion is
checkable by a named command**, and "met" means that command was run and printed what it should.
Effort, screenshots and intent are not evidence.

Conventions: `AC-W<wave>-<nn>` · owner is the agent accountable for producing the evidence ·
status is `met` / `pending` / `blocked` (blocked = waits on the owner's decision, named in the row).

---

## W0 — Contract (maryam) — **closed pending 4 downstream acknowledgements**

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W0-01** | `dayplan.yaml` parses and matches its own schema: exactly 6 agents, 5 rooms, 8 event types, 5 states in the frozen order, `initial: idle`, canonical cycle `idle→working→review→break→meeting→idle`, closed transition table | `python3 world/validate_dayplan.py` → exit 0, `OK — 242 contract checks passed` | **met** |
| **AC-W0-02** | `CONTRACT.md` carries the envelope field table, 8 payload sections, 14 error codes, and every event key is documented with a type | `grep -c '^### 5\.' docs/CONTRACT.md` → `8` · `grep -c '^\| \`W-' docs/CONTRACT.md` → `14` | **met** |
| **AC-W0-03** | the transition table and the event catalogue agree: each of the 8 types appears exactly once, its `to_state` equals the table's `to`, every `from_state` is a real state | `python3 world/validate_dayplan.py` (checks tagged `AC-W0-03`) | **met** |
| **AC-W0-04** | `task_id` rules are total and non-contradictory: required on `task_start`/`task_done`/`handoff`, forbidden on `meeting_open`/`meeting_close`/`break`/`idle`, optional on `message`; pattern accepts `WORLD-1`, rejects `world-1` | `python3 world/validate_dayplan.py` (tag `AC-W0-04`) | **met** |
| **AC-W0-05** | repo language rule: no Arabic characters in repo docs or code (Arabic stays in chat and UI copy files) | `grep -rlP '[\x{0600}-\x{06FF}]' docs world README.md` → no output | **met** |
| **AC-W0-06** | the no-intelligence rule is enforced at the contract level: no network/LLM/clock import under `world/`, and `message.text_source` has no `llm` value | `grep -rnE 'openai\|anthropic\|requests\|httpx\|socket\|datetime\.now\|time\.time\|urllib' world/` → no output | **met** |
| **AC-W0-07** | layout and day plan are physically possible: 6 distinct desks inside the bullpen rect, every schedule entry inside 09:00–18:00 and chronologically ordered, participants fit room capacity, `planned_ticks` inside the payload min/max, day opens with `task_start` and closes on `idle` | `python3 world/validate_dayplan.py` (tag `AC-W0-07`) | **met** |
| **AC-W0-08** | the four downstream owners confirm in a ticket comment that they can build against `v1.0.0` without changing a name (omar W1 · layla W1 · yassin W2 · salim W4) | 4 comments on WORLD-1, one per owner | **pending** |
| **AC-W0-09** | negative proof — the lint is not vacuous: breaking the contract makes it fail | `world/validate_dayplan.py <mutated copy>` for 5 mutations → exit 1 each | **met** (5/5) |
| **AC-W0-10** | the sim clock is one consistent scale: `tick_seconds` is a positive integer and `day_length_ticks × tick_seconds` equals the simulated day between `day_start` and `day_end` | `python3 world/validate_dayplan.py` (tag `AC-W0-10`); `pytest world/validate_dayplan.py` mutations: `tick_seconds: 0` and `day_length_ticks: 32400` with 60 s/tick → exit 1 | **met** |

**W0 does not close on file existence.** It closes when AC-W0-08 lands. The files being green is
necessary, not sufficient — a contract nobody has read is a suggestion.

---

## W1 — Core: deterministic sim + schedule (omar · layla)

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W1-01** | 10 000 ticks from seed 42 reproduce the golden hash | `pytest world/tests/test_determinism.py::test_golden_10k_hash -q` | pending |
| **AC-W1-02** | same seed twice → byte-identical event log | `pytest ...::test_same_seed_twice_identical -q` | pending |
| **AC-W1-03** | `step()` is pure: no I/O, no wall clock, no global RNG, no `set` iteration in an emitting path | `pytest ...::test_step_is_pure -q` and `grep -rnE 'datetime\.now\|time\.time\|requests\|open\(' world/sim.py` → empty | pending |
| **AC-W1-04** | replay equals step: applying `events.jsonl` reproduces the snapshot byte-for-byte, and `seq` is gapless (`W-SEQ-01` on a deleted line) | `pytest ...::test_replay_matches_step ...::test_seq_gap_detected -q` | pending |
| **AC-W1-05** | guards reject lies: an illegal `(type, from_state)` raises `W-STATE-01`; `task_start` without `task_id` raises `W-EVT-05`; a `task_id` absent from the ledger raises `W-DRIFT-01` | `pytest world/tests/test_guards.py -q` | pending |
| **AC-W1-06** | schedule is conflict-free: no agent in two rooms in one tick, no room over capacity, all six agents change state at least once per 600 ticks across a simulated 09:00–18:00 day | `pytest office/tests/test_schedule.py::test_room_conflicts -q` | pending |

---

## W2 — Runtime: engine, snapshot, WS (yassin)

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W2-01** | engine boots from `world/dayplan.yaml`; a schema violation is fatal (`W-CFG-01`) rather than silently defaulted | `curl -fsS localhost:8000/health` → `{"status":"ok",...}`; boot with a mutated YAML → exit ≠ 0 | pending |
| **AC-W2-02** | `/api/world/state` matches the §9.1 schema exactly (keys, types, `task_id` nullable) | `pytest apps/world-engine/tests/test_api.py::test_snapshot_schema -q` | pending |
| **AC-W2-03** | WS delivers a coalesced patch every tick for 60 s: ≥ 55 `patch` frames, at most one entry per agent per frame | `pytest apps/world-engine/tests/test_ws.py::test_patch_rate -q` | pending |
| **AC-W2-04** | restart resumes: after a container restart the tick advances past the pre-restart value and a connected browser needs no reload | `test_restart_resumes` + a scripted WS reconnect | pending |
| **AC-W2-05** | resource caps hold: `mem_limit 384m`, `cpus 0.75`, `pids_limit 128`; measured RSS < 150 MB over 10 min | `docker stats --no-stream` | **blocked** — needs owner approval for Docker |
| **AC-W2-06** | an unknown patch `type` does not break a v1 client (forward compatibility) | `test_unknown_patch_ignored` | pending |

---

## W3 — Render: one page, six dots (omar + maryam)

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W3-01** | one page renders 5 rooms, 6 labelled dots, an office clock and a state badge per agent | DOM: `document.querySelectorAll('.agent-dot').length === 6` | pending |
| **AC-W3-02** | every badge equals the snapshot state for that agent (no local invention) | script compares 6 badges to `/api/world/state` | pending |
| **AC-W3-03** | 5 min with zero console errors and zero unhandled rejections | headless run captures `console` | pending |
| **AC-W3-04** | 30 fps sustained and < 150 MB RSS for the page on the 2 vCPU box | `performance` trace + `docker stats` | pending |
| **AC-W3-05** | mission-control wall: 6 rows × ticket key × status × time-in-state, driven by the same snapshot | DOM text assertion per row | pending |
| **AC-W3-06** | clicking an agent opens a panel with its `task_id`, elapsed ticks and next milestone; empty state is explicit, never a blank box | click each dot → panel visible with 3 fields | pending |
| **AC-W3-07** | the transcript panel renders every `message` event as `[HH:MM:SS] ROOM · Agent: text`, newest first, capped at 60, meeting-room lines highlighted, and the "live" mark follows who is in the meeting room *now* (WORLD-10) | `pytest apps/world-web/tests/test_transcript_panel.py -q` → 9 passed; browser: post `message` events → `#transcript li` texts equal the formatted lines | **met** |
| **AC-W3-08** | the displayed clock advances exactly `sim_seconds_per_tick` per tick and reads the scale off the wire, so `WORLD_SIM_SECONDS_PER_TICK` changes the page (default 60 → one simulated minute per tick) | `pytest apps/world-web/tests/test_sim_clock.py apps/world-engine/tests/test_sim_speed.py -q`; browser: `#clock` at tick N and N+1 differ by 60 s | **met** |
| **AC-W3-09** | the agent panel: clicking a floor dot or a Mission Control row fills `AGENT PANEL` with name, role, state, room, time in state, ticket key + status + Atlas title, `Thinking:` the derived `thought` (its `thought_source` on the element) and `Last said:` the newest `message` from that agent with its sim time; every field is a wire value or an explicit empty state, and a new delta updates an open panel | `pytest apps/world-web/tests/test_agent_panel.py apps/world-engine/tests/test_thought.py -q` (jsdom clicks the real page); browser: click a row -> panel text equals `/api/world/state` | **met** (WORLD-11) |

---

## W4 — Life: meetings, reminders, almanac (layla · salim)

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W4-01** | scheduled meetings fire within ±5 ticks of 10:00 and 17:00 with all six participants | `pytest office/tests/test_meetings.py::test_standup_and_review -q` | pending |
| **AC-W4-02** | a reminder is emitted as a `message` with `about: meeting` 15 min before each meeting, rate-limited | `test_reminder_before_meeting` | pending |
| **AC-W4-03** | every almanac answer carries `source_path` and `fetched_at`; unsourced output is rejected, not rendered | `pytest office/tests/test_almanac.py::test_citations_required -q` | pending |
| **AC-W4-04** | FTS5 search returns only cited rows; an empty result says so instead of inventing one | `test_fts_returns_cited_only` | pending |
| **AC-W4-05** | persona lore and rendered text contain no tokens, keys, private paths or chat context | `grep -rniE 'sk-\|token\|password\|/home/' <rendered output>` → no output | pending |

---

## W5 — Ship (yassin) — **blocked until the owner approves Docker**

| id | criterion | check command | status |
|---|---|---|---|
| **AC-W5-01** | the stack starts under caps with healthchecks and `depends_on: service_healthy`, in ≤ 60 s | `docker compose up -d` then `docker compose ps` | **blocked** |
| **AC-W5-02** | exactly one published port, bound to `${BIND_HOST}:${WEB_PORT}`, engine internal-only, no public exposure | `docker compose ps --format '{{.Ports}}'` | **blocked** |
| **AC-W5-03** | end-to-end restart-resume: kill the engine mid-day, bring it back, the page keeps its tick and state without a browser reload | scripted run | **blocked** |
| **AC-W5-04** | whole stack < 200 MB RSS (engine < 150 MB) after 30 min of watching | `docker stats --no-stream` | **blocked** |
| **AC-W5-05** | no secret in the repo, the built image or the HTML | `grep -rlE 'sk-\|BEGIN .*PRIVATE\|password=' .` → no output | **blocked** |

---

## Cross-wave — "it feels alive" (maryam)

"Looks realistic" is not a criterion. These are, and each one is a number a script prints.

| id | criterion | check command | status |
|---|---|---|---|
| **AC-A-01** | every agent has ≥ 1 state change per 10 min of sim time (no frozen desk) | `python3 world/verify_aliveness.py` → column `changes/10min` ≥ 1 for all 6 | pending (W3) |
| **AC-A-02** | ≥ 2 rooms occupied simultaneously in any 60-min sample | same report → `min_rooms_occupied` ≥ 2 | pending (W3) |
| **AC-A-03** | ≥ 3 inter-agent events per 30 min (`message` \| `meeting_open` \| `handoff`) | same report → `inter_agent_per_30min` ≥ 3 | pending (W3) |
| **AC-A-04** | no agent in one state > 25 min without a matching ledger row (drift, not life); `meeting` exempt up to 60 min | same report → `max_state_age` ≤ 25 (non-meeting) | pending (W3) |
| **AC-A-05** | rendered state equals the ledger: 6/6 desks show the ticket Atlas reports for that agent, daily | drift report `W-DRIFT-01` count = 0 | pending (W3) |

---

## Verification log

| stamp | command | result |
|---|---|---|
| `2026-11-19T11:45Z` @ `70aa9d7` on `feat/WORLD-1-contract` | `python3 world/validate_dayplan.py` | `agents=6 rooms=5 event_types=8 states=5 schedule_entries=8` · `OK — 240 contract checks passed (W-CFG-01 clean)` · exit 0 |
| same | 5 mutated contract copies (dropped `meeting_close`, desk outside bullpen, 7th agent, `llm` in `text_source`, broken canonical cycle) | exit 1 on all 5, each with a named `AC-W0-0x` violation — the lint is not vacuous |
| same | `grep -rlP '[\x{0600}-\x{06FF}]' docs world README.md` | no output (AC-W0-05) |
| same | `grep -rnE 'openai\|anthropic\|requests\|httpx\|socket\|datetime\.now\|time\.time\|urllib' world/` | no output (AC-W0-06) |
| same | `grep -c '^### 5\.' docs/CONTRACT.md` / `grep -c '^\| \`W-' docs/CONTRACT.md` | `8` / `14` (AC-W0-02) |
| same | `ls docker-compose.y*ml Dockerfile` | no output — no deploy artifact in W0 |
| `2026-11-19T12:46Z` on `feat/WORLD-9-speed` | `python3 world/validate_dayplan.py` | `agents=6 rooms=5 event_types=8 states=5 schedule_entries=8` · `OK — 242 contract checks passed (W-CFG-01 clean)` · exit 0 (AC-W0-01, AC-W0-10) |
| same | `python -m pytest -q` (root `pytest.ini`) | `162 passed, 1 warning, 6 subtests passed` in 3.0 s — 133 pre-existing + `test_sim_speed.py` 12 + `test_transcript_panel.py` 9 + `test_sim_clock.py` 6 + 2 in `test_api.py` |
| same | `python apps/world-web/contract_projection.py --check` | `OK — index.html contract block matches world/dayplan.yaml` (contract v1.1.0, `tick_seconds` 60, `day_length_ticks` 540) |
| same | engine + page on `127.0.0.1:8099`, `message`/`meeting_*` events posted to `POST /api/world/events` | `#transcript` printed `[09:11:00] Meeting Room · Omar: transcript panel is live` and `[09:12:00] Meeting Room · Maryam: and it highlights the attendees` with `is-meeting is-live`; a bullpen line stayed plain; after `meeting_close` Omar's line kept `is-meeting` and lost `is-live` (AC-W3-07) |
| same | same run, `#clock` sampled at tick 20 and tick 23 | `09:20:00` → `09:23:00` (60 s per tick), `#rate` = `60 s/tick`, 0 console errors, `contract warnings: 0` (AC-W3-08) |
| same | 1440×900 and 780×437 layout probes | transcript list inside its panel, day plan below it, panel inside the aside, page does not scroll at 1440×900 (`scrollH` 900) |

Environment note: the lint needs PyYAML. `/usr/bin/python3` (3.12) has it; the default `python3` (3.14)
on this host does not. **W1 must add `pyyaml` to dev requirements** so `python3 world/validate_dayplan.py`
works without an absolute interpreter path — that gap belongs to AC-W1-03's tooling step, not to W0.

---

| `2026-11-19T13:20Z` on `feat/WORLD-11-panel` | `python -m pytest -q` (root `pytest.ini`) | `191 passed, 1 warning, 6 subtests passed` in 4.4 s — 162 pre-existing + `test_thought.py` 23 + `test_agent_panel.py` 4 (the jsdom test ran, not skipped) |
| same | `python world/validate_dayplan.py` | `agents=6 rooms=5 event_types=8 states=5 schedule_entries=8` · `OK — 242 contract checks passed` · contract v1.2.0, exit 0 |
| same | `python apps/world-web/contract_projection.py --check` · `python apps/world-engine/tools/build_data.py --check` | `OK` on both — the page's inline block and `app/data/schedule.json` match `world/dayplan.yaml` |
| same | engine + page on `127.0.0.1:8099`, real browser, click on Omar's Mission Control row | panel = `Omar · development · working · Bullpen · WORLD-7 · done` + Atlas title; `Thinking:` equals the snapshot `thought` (`thought_source` = `ticket`, Arabic copy from the engine UI copy file); `Last said:` `09:19:00 · …` (tick 19); link `live`, 0 console errors |
| same | jsdom drives the shipped page: row click → other row → floor dot → live delta → deselect | panel follows every click, a delta rewrites `Thinking:` and `Last said:`, deselect returns to `no agent selected` / `nothing said yet` (AC-W3-09) |
| same | `grep -rlP '[\x{0600}-\x{06FF}]' apps/world-web/static/index.html` | no output — the thought text arrives from the engine, the page composes none (AC-W0-05 for this file) |

*v1.2.0 · WORLD-1 · the criterion is the command, not the story.*
