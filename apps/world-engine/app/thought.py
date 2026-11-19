"""The one-line ``thought`` each agent carries on the wire (WORLD-11).

Every line is derived from a **real row**, never generated:

1. **ticket** — the key the agent holds (``task_start``) plus that ticket's title and
   status from the Atlas catalog snapshot (``app/data/tickets.json``, which carries
   ``source`` + ``fetched_at``). The agent's own state chooses the verb.
2. **meeting** — the topic of the meeting actually open in the world (it arrived on
   the ``meeting_open`` event, never invented here).
3. **dayplan** — the block of the day running at the current sim minute
   (``world/dayplan.yaml`` → ``app/data/schedule.json``). A block that names other
   participants is not this agent's thought.
4. **state** — the bare state verb, and when even that is impossible, an explicit
   ``no_data`` line. An empty box is a bug, so there is no blank case.

Rules: no LLM, no network, no clock read (the caller passes ``now_minute``), and no
I/O at call time — the three JSON files are read once, at import. The Arabic copy
lives in ``app/data/copy.ar.json`` (a UI copy file), not in this module.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"

SOURCES: tuple[str, ...] = ("ticket", "meeting", "dayplan", "state", "none")

#: Which dayplan blocks may speak for which state. A block of a different nature is
#: somebody else's business (an idle agent is not "in the standup" at 11:00 just
#: because the standup block is the last one that started).
_STATE_SLOTS: dict[str, frozenset[str]] = {
    "meeting": frozenset({"meeting"}),
    "break": frozenset({"break"}),
    # only the two markers that describe the day itself; `resume`/`review_sweep` are a
    # moment's emit, not a block an idle agent is sitting in
    "idle": frozenset({"day_open", "day_close"}),
}


def _load(name: str) -> dict[str, Any]:
    with (DATA_DIR / name).open("r", encoding="utf-8") as fh:
        return json.load(fh)


COPY = _load("copy.ar.json")
_SCHEDULE = _load("schedule.json")
_TICKETS = _load("tickets.json")

CONTRACT_VERSION: str = _SCHEDULE["contract_version"]
DAY_START: str = _SCHEDULE["day"]["start"]
DAY_END: str = _SCHEDULE["day"]["end"]
SCHEDULE_ENTRIES: tuple[dict[str, Any], ...] = tuple(_SCHEDULE["entries"])
TICKETS_SOURCE: str = _TICKETS["source"]
TICKETS_FETCHED_AT: str = _TICKETS["fetched_at"]
TICKET_CATALOG: dict[str, dict[str, Any]] = {t["key"]: t for t in _TICKETS["tickets"]}


# -- clocks ---------------------------------------------------------------
def hhmm_to_minutes(value: str | None) -> int | None:
    """``"13:45"`` -> 825. Anything else is ``None`` (never a guessed 09:00)."""
    if not isinstance(value, str):
        return None
    parts = value.split(":")
    if len(parts) < 2:
        return None
    try:
        hours, minutes = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hours < 24 and 0 <= minutes < 60):
        return None
    return hours * 60 + minutes


def minute_of_ts(ts: str | None) -> int | None:
    """Minute of day of an ISO-8601 UTC stamp (``2026-01-05T09:52:00Z`` -> 592)."""
    if not isinstance(ts, str) or "T" not in ts:
        return None
    return hhmm_to_minutes(ts.split("T", 1)[1])


# -- the two real tables --------------------------------------------------
def ticket_meta(key: str | None) -> dict[str, Any] | None:
    """The Atlas row behind a ticket key, or ``None`` when the catalog has no such key."""
    if not isinstance(key, str) or not key:
        return None
    row = TICKET_CATALOG.get(key)
    if row is None:
        return None
    return {
        "key": row["key"],
        "title": row["title"],
        "status": row["status"],
        "source": TICKETS_SOURCE,
        "fetched_at": TICKETS_FETCHED_AT,
    }


def slot_at(minute: int | None, agent_id: str | None = None) -> dict[str, Any] | None:
    """The dayplan entry that started last at or before ``minute``.

    ``None`` before the day opens (or when the clock is unknown). An entry that lists
    participants and does not list this agent is somebody else's block, so it returns
    ``None`` instead of putting words in this agent's mouth.
    """
    if minute is None:
        return None
    current: dict[str, Any] | None = None
    for entry in SCHEDULE_ENTRIES:
        start = hhmm_to_minutes(entry.get("at"))
        if start is None or start > minute:
            break
        current = entry
    if current is None:
        return None
    participants = current.get("participants")
    if isinstance(participants, list) and agent_id is not None and agent_id not in participants:
        return None
    return current


def next_slot(minute: int | None) -> dict[str, Any] | None:
    """The next dayplan entry after ``minute`` (what an idle agent is waiting for)."""
    if minute is None:
        return None
    for entry in SCHEDULE_ENTRIES:
        start = hhmm_to_minutes(entry.get("at"))
        if start is not None and start > minute:
            return entry
    return None


def slot_what(entry: dict[str, Any]) -> str:
    """The readable subject of a dayplan entry (topic, then kind, then the type)."""
    kind = entry.get("kind")
    return (
        entry.get("topic")
        or COPY.get("kind", {}).get(kind)
        or kind
        or COPY.get("type", {}).get(entry["type"])
        or entry["type"].replace("_", " ")
    )


def slot_text(entry: dict[str, Any]) -> str:
    """Render one dayplan entry with the copy templates (unknown types stay readable)."""
    template = COPY["slot"].get(entry["type"], COPY["slot_default"])
    return template.format(what=slot_what(entry), id=entry["id"], at=entry["at"])


# -- the thought ----------------------------------------------------------
def compose(
    *,
    state: str | None,
    agent_id: str | None = None,
    ticket: str | None = None,
    title: str | None = None,
    meeting_topic: str | None = None,
    now_minute: int | None = None,
) -> tuple[str, str]:
    """Return ``(thought, source)`` where ``source`` names the row it came from."""
    verbs = COPY["state_verb"]

    # 1. the ticket on the desk — the strongest, most specific row
    if state in ("working", "review") and ticket:
        verb = verbs[state].format(ticket=ticket)
        if title:
            return COPY["with_title"].format(verb=verb, title=title), "ticket"
        return COPY["no_title"].format(verb=verb), "ticket"

    slot = slot_at(now_minute, agent_id)

    # 2. the meeting actually open (its topic arrived on `meeting_open`)
    if state == "meeting":
        topic = meeting_topic or (slot or {}).get("topic")
        if topic:
            return COPY["meeting_topic"].format(topic=topic), "meeting"
        return verbs["meeting"], "meeting"

    # 3. the dayplan block that matches this state, not just the last one that started
    if slot is not None and slot["type"] in _STATE_SLOTS.get(state or "", frozenset()):
        return slot_text(slot), "dayplan"

    # 4. what an idle agent is actually waiting for, straight from the day plan
    if state == "idle":
        upcoming = next_slot(now_minute)
        if upcoming is not None:
            return COPY["next_slot"].format(
                what=slot_what(upcoming), id=upcoming["id"], at=upcoming["at"]
            ), "dayplan"

    # 5. the bare state, and only when even that is unavailable, an explicit no-data line
    if state in ("working", "review"):
        return COPY["state_verb_none"][state], "state"
    if state in verbs:
        return verbs[state], "state"
    return COPY["no_data"], "none"
