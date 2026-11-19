"""Frozen v1 contract: state machine + event vocabulary.

Nothing in the engine may move an agent except the transitions declared here.
If an event/state pair is not in `TRANSITIONS`, the engine *rejects* it instead
of guessing a target state (the world never invents state).
"""

from __future__ import annotations

from dataclasses import dataclass, field

# --- Frozen v1 state machine: idle -> working -> review -> break -> meeting -> idle
STATES: tuple[str, ...] = ("idle", "working", "review", "break", "meeting")

# --- Frozen v1 event vocabulary
EVENT_TYPES: tuple[str, ...] = (
    "task_start",
    "task_done",
    "message",
    "meeting_open",
    "meeting_close",
    "break",
    "handoff",
    "idle",
)

# Events that do not change the FSM state (only annotate the agent / emit speech).
NON_TRANSITIONAL: frozenset[str] = frozenset({"message"})

# (from_state, event) -> to_state.  A wildcard "*" means "any state".
TRANSITIONS: dict[tuple[str, str], str] = {
    # a task may start from rest/break/review, or replace the current task
    ("idle", "task_start"): "working",
    ("working", "task_start"): "working",
    ("review", "task_start"): "working",
    ("break", "task_start"): "working",
    # finishing work sends the agent to review (never straight to idle)
    ("working", "task_done"): "review",
    # review hands the artefact over, then the agent rests
    ("review", "handoff"): "idle",
    ("working", "handoff"): "working",
    ("idle", "handoff"): "working",
    # coffee
    ("working", "break"): "break",
    ("review", "break"): "break",
    ("idle", "break"): "break",
    # meetings: anyone in the office can be pulled in, only meeting can end one
    ("idle", "meeting_open"): "meeting",
    ("working", "meeting_open"): "meeting",
    ("review", "meeting_open"): "meeting",
    ("break", "meeting_open"): "meeting",
    ("meeting", "meeting_open"): "meeting",  # idempotent join
    ("meeting", "meeting_close"): "idle",
    # explicit park
    ("*", "idle"): "idle",
}

# Documents the shape the renderer can rely on.
ROOM_PREFIX = "desk-"


def resolve(current: str, event_type: str) -> str | None:
    """Return the next state, or None when the transition is not allowed."""
    if event_type not in EVENT_TYPES:
        return None
    if event_type in NON_TRANSITIONAL:
        return current if current in STATES else None
    target = TRANSITIONS.get((current, event_type))
    if target is None:
        target = TRANSITIONS.get(("*", event_type))
    if target is None or target not in STATES:
        return None
    return target


@dataclass(frozen=True)
class Rejection:
    reason: str
    agent_id: str | None = None
    event_type: str | None = None
    from_state: str | None = None

    def as_dict(self) -> dict:
        return {
            "reason": self.reason,
            "agent_id": self.agent_id,
            "event_type": self.event_type,
            "from_state": self.from_state,
        }


# --- agent roster (v1: one floor, six desks) ---------------------------------
@dataclass(frozen=True)
class AgentSpec:
    id: str
    name: str
    role: str
    desk_room: str


def default_roster() -> dict[str, AgentSpec]:
    specs = [
        ("hakim", "Hakim", "coordinator"),
        ("layla", "Layla", "schedules"),
        ("maryam", "Maryam", "contract"),
        ("omar", "Omar", "core-dev"),
        ("salim", "Salim", "research"),
        ("yassin", "Yassin", "infra"),
    ]
    return {sid: AgentSpec(sid, name, role, f"{ROOM_PREFIX}{sid}") for sid, name, role in specs}
