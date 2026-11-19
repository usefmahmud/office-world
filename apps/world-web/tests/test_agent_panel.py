"""WORLD-11 — clicking an agent fills the AGENT PANEL with what it thinks and last said.

Two layers, one source of truth each:

* the DOM-free transcript core (`WorldTranscript.lastBy`) is executed in Node, so the
  "last said" rule under test is the rule the page ships;
* the shipped `index.html` is driven in a real DOM (jsdom) — a click on a Mission
  Control row and a click on a floor dot — and the panel is read back by its ids.

The page holds no copy of the thought logic: the line arrives on the wire from the
engine (`thought`), and every field the panel shows is either the wire value or an
explicit empty state.

`jsdom` is a test-only dependency of this directory (``cd apps/world-web/tests/js &&
npm install``); without it the DOM test skips instead of pretending it ran, exactly
like the Node-backed transcript test does when Node is missing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from contract_projection import INDEX_HTML

HERE = Path(__file__).resolve().parent
JS_DIR = HERE / "js"
HTML = INDEX_HTML.read_text(encoding="utf-8")
CORE_RE = re.compile(
    r'<script id="world-transcript-core" type="text/javascript">(.*?)</script>',
    re.DOTALL,
)

NODE = shutil.which("node")


def _jsdom_available() -> bool:
    if NODE is None or not (JS_DIR / "node_modules" / "jsdom").exists():
        return False
    probe = subprocess.run(
        [NODE, "-e", "require('jsdom')"], cwd=JS_DIR, capture_output=True, text=True
    )
    return probe.returncode == 0


HAS_JSDOM = _jsdom_available()

# --- the wire the panel is proved against ---------------------------------
THOUGHT_OMAR = "شغّال على WORLD-11 — لوحة الوكيل"
THOUGHT_MARYAM = "بيراجع WORLD-10 — لوحة المحادثات"
THOUGHT_OMAR_DELTA = "شغّال على WORLD-12 — حوار قصير بين زمايل"
TITLE_OMAR = "Agent panel on click: thinking, last words, ticket details"

SNAPSHOT = {
    "tick": 42,
    "seed": 7,
    "sim_time": "2026-01-05T09:42:00Z",
    "sim_seconds_per_tick": 60,
    "meeting": None,
    "counts": {"idle": 4, "working": 1, "review": 1, "break": 0, "meeting": 0},
    "agents": [
        {
            "id": "omar",
            "name": "Omar",
            "role": "development",
            "state": "working",
            "room": "desk-omar",
            "ticket": "WORLD-11",
            "ticket_title": TITLE_OMAR,
            "ticket_status": "in_progress",
            "since_tick": 40,
            "thought": THOUGHT_OMAR,
            "thought_source": "ticket",
        },
        {
            "id": "maryam",
            "name": "Maryam",
            "role": "delivery",
            "state": "review",
            "room": "desk-maryam",
            "ticket": "WORLD-10",
            "ticket_title": "World chat: render message events as a live transcript",
            "ticket_status": "done",
            "since_tick": 41,
            "thought": THOUGHT_MARYAM,
            "thought_source": "ticket",
        },
    ],
    "recent_messages": [
        {"tick": 40, "agent": "omar", "type": "message", "room": "desk-omar",
         "note": "panel wired — thought comes from Atlas"},
        {"tick": 41, "agent": "maryam", "type": "message", "room": "desk-maryam",
         "note": "looks green"},
    ],
}

DELTA = {
    "type": "delta",
    "tick": 43,
    "ts": "2026-01-05T09:43:00Z",
    "sim_time": "2026-01-05T09:43:00Z",
    "sim_seconds_per_tick": 60,
    "meeting": None,
    "meeting_changed": False,
    "counts": {"idle": 4, "working": 1, "review": 1, "break": 0, "meeting": 0},
    "patches": [
        {
            "id": "omar",
            "name": "Omar",
            "role": "development",
            "state": "working",
            "room": "desk-omar",
            "ticket": "WORLD-12",
            "ticket_title": "Natural short dialogue between co-located agents",
            "ticket_status": "backlog",
            "since_tick": 42,
            "thought": THOUGHT_OMAR_DELTA,
            "thought_source": "ticket",
        }
    ],
    "events": [
        {"tick": 43, "agent": "omar", "type": "message", "room": "desk-omar",
         "note": "delta wired"}
    ],
}

FIXTURE = {"click": "omar", "other": "maryam", "snapshot": SNAPSHOT, "delta": DELTA}


# --- node: lastBy ---------------------------------------------------------
LASTBY_HARNESS = r"""
const fs = require('fs');
const vm = require('vm');
const sandbox = {};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(process.argv[2], 'utf8'), sandbox);
const T = sandbox.WorldTranscript;
const events = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const ctx = {t0: '2026-01-05T09:00:00Z', secondsPerTick: 60,
             agentName: (id) => id, roomName: (r) => r, isMeetingRoom: () => false};
const state = T.create();
T.ingest(state, events, ctx);
const last = T.lastBy(state, 'omar') || {};
console.log(JSON.stringify({
  line: last.line || null,
  tick: last.tick === undefined ? null : last.tick,
  clock: last.clock || null,
  ghost: T.lastBy(state, 'ghost'),
  empty: T.lastBy(T.create(), 'omar'),
  firstOfDisplay: T.rows(state)[0].agentId
}));
"""


def test_last_by_returns_the_newest_message_of_one_agent(tmp_path: Path) -> None:
    if NODE is None:
        pytest.skip("node is not installed; the transcript core needs a JS runtime")
    match = CORE_RE.search(HTML)
    assert match is not None, "index.html lost its world-transcript-core script block"
    core = tmp_path / "core.js"
    core.write_text(match.group(1), encoding="utf-8")
    harness = tmp_path / "harness.js"
    harness.write_text(LASTBY_HARNESS, encoding="utf-8")
    events = tmp_path / "events.json"
    events.write_text(
        json.dumps(
            [
                {"tick": 5, "agent": "omar", "type": "message", "room": "desk-omar", "note": "first"},
                {"tick": 9, "agent": "maryam", "type": "message", "room": "desk-maryam", "note": "hers"},
                {"tick": 12, "agent": "omar", "type": "message", "room": "desk-omar", "note": "newest"},
                {"tick": 3, "agent": "omar", "type": "task_start", "room": "desk-omar", "ticket": "WORLD-11"},
            ]
        ),
        encoding="utf-8",
    )
    done = subprocess.run(
        [NODE, str(harness), str(core), str(events)], capture_output=True, text=True, timeout=60
    )
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert out["line"] == "[09:12:00] desk-omar · omar: newest"
    assert out["tick"] == 12 and out["clock"] == "09:12:00"
    assert out["ghost"] is None and out["empty"] is None, "no message means no line"
    assert out["firstOfDisplay"] == "omar", "the transcript still shows newest first"


# --- the page itself ------------------------------------------------------
def test_the_panel_has_the_hooks_the_owner_asked_for() -> None:
    for hook in (
        'id="detail"',
        'id="detail-name"',
        'id="detail-role"',
        'id="detail-state"',
        'id="detail-room"',
        'id="detail-elapsed"',
        'id="detail-ticket"',
        'id="detail-title"',
        'id="detail-thought"',
        'id="detail-last-said"',
        "Thinking:",
        "Last said:",
    ):
        assert hook in HTML, f"agent panel lost {hook}"
    # both entry points: a floor dot and a Mission Control row
    assert "addEventListener('click', () => select(agent.id))" in HTML
    # the panel is re-painted from the model on every frame, not only on click
    assert "renderDetail(simNow)" in HTML


def test_the_page_never_invents_a_thought__it_is_wire_only() -> None:
    # the thought text is Arabic and comes from the engine: the page carries no Arabic
    # of its own, so it cannot be composing a line locally (AC-W0-05 + no invention).
    assert re.findall(r"[\u0600-\u06FF]", HTML) == []
    assert "thought" in HTML and "a.thought" in HTML
    # and the last-said line is the transcript's, not a second buffer
    assert "WorldTranscript.lastBy(transcript, id)" in HTML


@pytest.mark.skipif(not HAS_JSDOM, reason="jsdom is not installed (see apps/world-web/tests/js)")
def test_clicking_a_row_fills_the_panel_with_thought_and_last_message(tmp_path: Path) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_text(json.dumps(FIXTURE, ensure_ascii=False), encoding="utf-8")
    done = subprocess.run(
        [NODE, "panel_click.js", str(INDEX_HTML), str(fixture)],
        cwd=JS_DIR,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    out = json.loads(done.stdout)
    assert "fatal" not in out, out["fatal"]

    assert out["rows"] == 6 and out["dots"] == 6, "the page did not boot from the contract"
    assert out["errors"] == [], out["errors"]
    assert out["warnings"] == [], out["warnings"]

    # nothing selected: every field says so instead of sitting blank
    assert out["initial"]["panelAgent"] == ""
    assert out["initial"]["name"] == "no agent selected"
    assert out["initial"]["thought"] == "no agent selected"
    assert out["initial"]["lastSaid"] == "nothing said yet"

    # --- the click under test: one Mission Control row ---------------------
    clicked = out["clicked"]
    assert clicked["panelAgent"] == "omar"
    assert clicked["name"] == "Omar"
    assert clicked["role"] == "development"
    assert clicked["state"] == "working"
    assert clicked["room"] == "Bullpen"
    assert clicked["elapsed"] == "2m 00s (2 ticks)", clicked["elapsed"]
    assert clicked["ticket"] == "WORLD-11 · in_progress"
    assert clicked["title"] == TITLE_OMAR
    assert clicked["thought"] == THOUGHT_OMAR
    assert clicked["thoughtSource"] == "ticket"
    # last said: the newest `message` from this agent, with its sim time
    assert clicked["lastSaid"] == "09:40:00 · panel wired — thought comes from Atlas"
    assert clicked["lastSaidTick"] == "40"
    assert clicked["lastSaidAgent"] == "omar"

    # a different row moves the panel, including the last-said line
    other = out["other"]
    assert other["panelAgent"] == "maryam"
    assert other["thought"] == THOUGHT_MARYAM
    assert other["lastSaid"] == "09:41:00 · looks green"
    assert other["ticket"] == "WORLD-10 · done"

    # a floor dot is the second entry point
    dot = out["dot"]
    assert dot["panelAgent"] == "omar"
    assert dot["thought"] == THOUGHT_OMAR

    # a live delta updates the open panel: new thought, new line, no reload
    delta = out["delta"]
    assert delta["panelAgent"] == "omar"
    assert delta["thought"] == THOUGHT_OMAR_DELTA
    assert delta["ticket"] == "WORLD-12 · backlog"
    assert delta["lastSaid"] == "09:43:00 · delta wired"
    assert delta["lastSaidTick"] == "43"

    # clicking the selected row again closes the panel — explicitly
    deselected = out["deselected"]
    assert deselected["panelAgent"] == ""
    assert deselected["thought"] == "no agent selected"
    assert deselected["lastSaid"] == "nothing said yet"
    assert out["selectedRows"] == 0 and out["selectedDots"] == 0
