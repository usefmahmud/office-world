"""WORLD-10 — the transcript panel renders `message` events.

The page keeps one source of truth for the line format: the inline
`<script id="world-transcript-core">` block. These tests drive that exact block in
Node (no browser, no framework, no copy of the logic in Python) and then assert the
DOM hooks the panel needs are wired in `index.html`.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from contract_projection import INDEX_HTML, embedded

HTML = INDEX_HTML.read_text(encoding="utf-8")
CONTRACT = embedded()
CORE_RE = re.compile(
    r'<script id="world-transcript-core" type="text/javascript">(.*?)</script>',
    re.DOTALL,
)

NODE = shutil.which("node")

HARNESS = r"""
const fs = require('fs');
const vm = require('vm');

const core = fs.readFileSync(process.argv[2], 'utf8');
const sandbox = {};
sandbox.globalThis = sandbox;
vm.createContext(sandbox);
vm.runInContext(core, sandbox);

const T = sandbox.WorldTranscript;
const meta = JSON.parse(fs.readFileSync(process.argv[3], 'utf8'));
const ctx = {
  t0: meta.t0,
  secondsPerTick: meta.secondsPerTick,
  agentName: (id) => ({omar: 'Omar', maryam: 'Maryam', salim: 'Salim'}[id] || id),
  roomName: (raw) => ({
    'meeting-room': 'Meeting Room', 'desk-omar': 'Bullpen',
    'desk-maryam': 'Bullpen', 'coffee-corner': 'Kitchen'
  }[raw] || raw),
  isMeetingRoom: (raw) => raw === 'meeting-room'
};

function ingestAll(events) {
  const state = T.create();
  const added = T.ingest(state, events, ctx);
  return {state: state, added: added, rows: T.rows(state)};
}

const script = JSON.parse(fs.readFileSync(process.argv[4], 'utf8'));
const out = {};

if (script.mode === 'basic') {
  const run = ingestAll(script.events);
  out.count = run.rows.length;
  out.lines = run.rows.map((r) => r.line);
  out.meeting = run.rows.map((r) => r.meeting);
  out.agents = run.rows.map((r) => r.agentId);
  out.rooms = run.rows.map((r) => r.roomId);
  out.ticks = run.rows.map((r) => r.tick);
  out.clockAt2 = T.hhmmss(T.simDate(ctx.t0, ctx.secondsPerTick, 2));
  out.clockAt0 = T.hhmmss(T.simDate(ctx.t0, ctx.secondsPerTick, 0));
  out.clockAt1h = T.hhmmss(T.simDate(ctx.t0, ctx.secondsPerTick, 60));
  // a duplicate delta must not double the line
  out.dupAdded = T.ingest(run.state, script.events, ctx).length;
  out.countAfterDup = T.rows(run.state).length;
}

if (script.mode === 'cap') {
  const events = [];
  for (let i = 0; i < script.n; i += 1) {
    events.push({tick: i + 1, agent: 'omar', type: 'message', room: 'desk-omar', note: 'msg ' + i});
  }
  const run = ingestAll(events);
  out.count = run.rows.length;
  out.max = T.MAX_LINES;
  out.newest = run.rows[0].line;
  out.oldest = run.rows[run.rows.length - 1].line;
}

if (script.mode === 'odd') {
  const run = ingestAll(script.events);
  out.count = run.rows.length;
  out.lines = run.rows.map((r) => r.line);
}

console.log(JSON.stringify(out));
"""


def core_source() -> str:
    match = CORE_RE.search(HTML)
    assert match is not None, 'index.html lost its world-transcript-core script block'
    return match.group(1)


def run_core(tmp_path: Path, script: dict) -> dict:
    if NODE is None:
        pytest.skip("node is not installed; the transcript core needs a JS runtime")
    core_path = tmp_path / "transcript-core.js"
    core_path.write_text(core_source(), encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(HARNESS, encoding="utf-8")
    ctx_path = tmp_path / "ctx.json"
    ctx_path.write_text(
        json.dumps({"t0": CONTRACT["sim"]["t0"], "secondsPerTick": CONTRACT["sim"]["tick_seconds"]}),
        encoding="utf-8",
    )
    script_path = tmp_path / "script.json"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    done = subprocess.run(
        [NODE, str(harness_path), str(core_path), str(ctx_path), str(script_path)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_panel_hooks_exist_in_the_page() -> None:
    for hook in ('id="transcript"', 'id="chat-count"', 'id="chat-empty"', 'aria-live="polite"'):
        assert hook in HTML, f"transcript panel lost {hook}"
    # the panel is rendered from the shared core, not from a second copy of the rules
    assert "WorldTranscript.create()" in HTML
    assert "WorldTranscript.rows(" in HTML
    assert "WorldTranscript.ingest(" in HTML
    # a new delta must reach the panel
    assert "applyEvents(msg.events)" in HTML


def test_a_fresh_snapshot_refills_the_panel() -> None:
    """Reloading the page must not show an empty transcript while the engine has history."""
    assert "applyEvents(snap.recent_messages)" in HTML
    assert "recent_messages" in HTML


def test_meeting_room_rows_are_highlighted_by_css() -> None:
    assert "#transcript li.is-meeting" in HTML
    assert "#transcript li.is-live" in HTML
    # the live mark is read from the current snapshot, not remembered per line
    assert "function inMeetingRoomNow(" in HTML
    assert "row.meeting && inMeetingRoomNow(row.agentId)" in HTML


def test_a_message_becomes_one_formatted_line(tmp_path: Path) -> None:
    result = run_core(
        tmp_path,
        {
            "mode": "basic",
            "events": [
                {"tick": 2, "agent": "omar", "type": "message", "room": "meeting-room",
                 "note": "standup in 5"},
                {"tick": 1, "agent": "maryam", "type": "task_start", "room": "desk-maryam",
                 "ticket": "WORLD-10", "note": "starting"},
                {"tick": 3, "agent": "maryam", "type": "message", "room": "desk-maryam",
                 "note": "panel is green"},
                {"tick": 4, "agent": "salim", "type": "meeting_open", "room": "meeting-room",
                 "note": "standup"},
            ],
        },
    )

    # only `message` events are conversations
    assert result["count"] == 2
    # newest first
    assert result["lines"] == [
        "[09:03:00] Bullpen · Maryam: panel is green",
        "[09:02:00] Meeting Room · Omar: standup in 5",
    ]
    # [sim time] ROOM · Agent: note — the exact contract shape
    for line in result["lines"]:
        assert re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\] [^·]+ · [^:]+: .+", line), line
    # only the meeting-room line is a meeting line
    assert result["meeting"] == [False, True]


def test_the_buffer_is_chronological_and_duplicates_are_dropped(tmp_path: Path) -> None:
    result = run_core(
        tmp_path,
        {
            "mode": "basic",
            "events": [
                {"tick": 5, "agent": "omar", "type": "message", "room": "desk-omar", "note": "later"},
                {"tick": 2, "agent": "omar", "type": "message", "room": "desk-omar", "note": "earlier"},
            ],
        },
    )
    assert result["count"] == 2
    assert result["dupAdded"] == 0, "the same delta replayed must not duplicate a line"
    assert result["countAfterDup"] == 2
    assert result["ticks"] == [5, 2]           # rows() is newest first
    assert [line.split(": ", 1)[-1] for line in result["lines"]] == ["later", "earlier"]


def test_the_clock_advances_sixty_seconds_per_tick(tmp_path: Path) -> None:
    result = run_core(
        tmp_path,
        {"mode": "basic", "events": [{"tick": 0, "agent": "omar", "type": "message",
                                      "room": "desk-omar", "note": "hi"}]},
    )
    assert CONTRACT["sim"]["tick_seconds"] == 60
    assert result["clockAt0"] == "09:00:00"
    assert result["clockAt2"] == "09:02:00"      # 2 ticks x 60 s
    assert result["clockAt1h"] == "10:00:00"     # 60 ticks x 60 s


def test_the_buffer_is_capped_at_the_newest_lines(tmp_path: Path) -> None:
    result = run_core(tmp_path, {"mode": "cap", "n": 70})
    assert result["max"] == 60
    assert result["count"] == 60
    assert "msg 69" in result["newest"], result["newest"]
    assert "msg 10" in result["oldest"], result["oldest"]


def test_odd_events_do_not_break_the_panel(tmp_path: Path) -> None:
    result = run_core(
        tmp_path,
        {
            "mode": "odd",
            "events": [
                {"tick": 1, "agent": "omar", "type": "message", "room": "meeting-room"},
                {"tick": "2", "agent": "ghost", "type": "message", "room": "nowhere", "note": "x"},
                {"type": "message"},
                None,
                "not an event",
            ],
        },
    )
    assert result["count"] == 3, "a message with no room/agent still renders honestly"
    assert "[09:01:00] Meeting Room · Omar: " in result["lines"]
    # an unknown room or agent is shown as the wire spelled it, never invented
    assert "[--:--:--] nowhere · ghost: x" in result["lines"]
    for line in result["lines"]:
        assert line.startswith("[")


def test_the_page_does_not_run_an_llm_or_fetch_text() -> None:
    """Contract §7: the transcript shows what the world emitted, it never invents text."""
    assert "text_source" not in HTML.lower() or "llm" not in HTML.lower()
    assert "fetch('/api/world/messages" not in HTML
