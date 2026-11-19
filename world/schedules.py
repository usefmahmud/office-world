"""Rule-based day plans — the world's only source of scheduled events.

No LLM, no clock, no filesystem: a day plan is a *pure* function of
``(seed, agent_id)``, so the same seed always produces the same office day.
Each plan is a list of :class:`Planned` events pinned to a tick-of-day; the
sim applies them and the frozen state machine decides whether they land.

Meetings interrupt work.  Because ``meeting_close`` returns everybody to
``idle`` (frozen machine), an interrupted task is *resumed* with a fresh
``task_start`` carrying the same ``task_id`` once the meeting lets out — that
is how the office gets back to work without inventing extra states.

The day is 8 simulated hours (1 tick = 1 simulated second, day starts 09:00).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from .entities import Event
from .rng import Stream

#: Ticks per simulated workday (8 h × 3600 s).
DAY_LEN = 8 * 3600

#: Wall-clock label of tick-of-day 0, used by the renderer only.
DAY_START_LABEL = "09:00"

#: Canned speech ids.  The words themselves live in a content file (W4/Layla)
#: so the tick loop never generates text — it only references an id.
LINE_IDS: tuple[str, ...] = (
    "line-greeting",
    "line-status",
    "line-blocker",
    "line-review-ok",
    "line-coffee",
    "line-tests-green",
    "line-meeting-note",
    "line-thanks",
    "line-handoff",
    "line-question",
    "line-lunch",
    "line-weekend",
)

#: Ambient speech is rate limited per agent.
MESSAGE_MIN_GAP = 45


@dataclass(frozen=True, slots=True)
class Profile:
    """Personality and calendar knobs — four numbers per agent, as designed."""

    agent_id: str
    name: str
    desk_x: int
    desk_y: int
    speed: int          # centi-units per tick (~70 = 0.7 units/s)
    work_len: int       # ticks spent in `working` before task_done
    review_len: int     # ticks spent in `review` before handoff
    social: int         # permille chance of ambient speech per idle tick


PROFILES: tuple[Profile, ...] = (
    Profile("hakim", "حكيم", 1200, 1200, 70, 4200, 900, 14),
    Profile("layla", "ليلى", 3400, 1200, 55, 3000, 700, 26),
    Profile("maryam", "مريم", 5600, 1200, 80, 2400, 500, 22),
    Profile("omar", "عمر", 1200, 3000, 75, 3900, 650, 10),
    Profile("salim", "سليم", 3400, 3000, 50, 3600, 1200, 18),
    Profile("yassin", "ياسين", 5600, 3000, 85, 3300, 550, 12),
)

PROFILE_INDEX: dict[str, Profile] = {p.agent_id: p for p in PROFILES}
AGENT_IDS: tuple[str, ...] = tuple(p.agent_id for p in PROFILES)

#: All-hands meetings: ``(tick_of_day, duration, owner)``.
MEETINGS: tuple[tuple[int, int, str], ...] = (
    (2 * 3600, 1200, "hakim"),
    (5 * 3600 + 1800, 900, "maryam"),
)


@dataclass(frozen=True, slots=True)
class Planned:
    start: int
    kind: str
    agent_id: str
    task_id: str | None = None
    participants: tuple[str, ...] = ()


def _meeting_hit(start: int, end: int) -> tuple[int, int] | None:
    """First meeting window that overlaps ``[start, end)``, if any."""
    for meeting_start, duration, _owner in MEETINGS:
        meeting_end = meeting_start + duration
        if start < meeting_end and meeting_start < end:
            return (meeting_start, meeting_end)
    return None


@lru_cache(maxsize=256)
def day_plan(agent_id: str, seed: int) -> tuple[Planned, ...]:
    """Build one agent's day.  Cached, but still a pure function of its args.

    Blocks are *clipped* at meeting boundaries instead of being allowed to run
    through them: a meeting cuts a block short, ``meeting_close`` leaves the
    agent idle, and the next cycle's ``task_start`` puts them back to work.
    That keeps every transition legal, so the rejection counter stays at zero
    for a healthy day.
    """
    profile = PROFILE_INDEX[agent_id]
    stream = Stream(seed, "schedule", agent_id)
    planned: list[Planned] = []

    tick = stream.between(0, 300)                     # arrive within 5 minutes
    limit = DAY_LEN * 3 // 4                          # keep 25% of the day free
    cycle = 0
    while tick < limit:
        cycle += 1
        task_id = f"T-{agent_id}-{cycle:02d}"
        work_len = max(600, profile.work_len + stream.between(-240, 240))
        review_len = max(240, profile.review_len + stream.between(-90, 90))

        hit = _meeting_hit(tick, tick + work_len)
        resume_at: int | None = None
        if hit is not None:
            meeting_start, meeting_end = hit
            if meeting_start <= tick:
                # The block cannot start now; wait for the meeting to let out.
                tick = meeting_end + stream.between(20, 120)
                continue
            work_end = meeting_start - 1
            if work_end - tick < 120:
                tick = meeting_end + stream.between(20, 120)
                continue
            resume_at = meeting_end + stream.between(20, 120)
        else:
            work_end = tick + work_len

        planned.append(Planned(tick, "task_start", agent_id, task_id))
        planned.append(Planned(work_end, "task_done", agent_id, task_id))

        review_end = work_end + review_len
        hit = _meeting_hit(work_end, review_end)
        if hit is not None:
            review_end = min(review_end, hit[0] - 1)
            resume_at = resume_at or hit[1] + stream.between(20, 120)

        if review_end - work_end >= 120:
            # Leaving review is either a handoff or a plain coffee run; both are
            # legal edges onto `break`, chosen deterministically per cycle.
            leave_kind = "handoff" if stream.chance(1, 2) else "break"
            planned.append(Planned(review_end, leave_kind, agent_id, task_id))
            tick = review_end
        else:
            tick = work_end

        if resume_at is not None:
            tick = resume_at
        tick += stream.between(300, 900)              # coffee before the next task

    planned.append(Planned(min(tick, DAY_LEN - 1), "idle", agent_id))
    return tuple(planned)


@lru_cache(maxsize=64)
def _schedule_index(seed: int) -> dict[int, tuple[Planned, ...]]:
    """tick_of_day -> planned events, sorted; rebuilt per seed."""
    index: dict[int, list[Planned]] = {}

    for meeting_start, duration, owner in MEETINGS:
        index.setdefault(meeting_start % DAY_LEN, []).append(
            Planned(meeting_start, "meeting_open", owner, None, AGENT_IDS)
        )
        index.setdefault((meeting_start + duration) % DAY_LEN, []).append(
            Planned(meeting_start + duration, "meeting_close", owner, None, AGENT_IDS)
        )

    for agent_id in AGENT_IDS:
        for item in day_plan(agent_id, seed):
            index.setdefault(item.start % DAY_LEN, []).append(item)

    return {
        tick: tuple(sorted(items, key=lambda p: (p.agent_id, p.kind, p.task_id or "")))
        for tick, items in index.items()
    }


def events_at(seed: int, tick_of_day: int) -> tuple[Event, ...]:
    """Scheduled events for a given tick-of-day, in a stable order."""
    items = _schedule_index(seed).get(tick_of_day % DAY_LEN, ())
    return tuple(
        Event(
            tick=tick_of_day,
            kind=item.kind,
            agent_id=item.agent_id,
            task_id=item.task_id,
            participants=item.participants,
            source="schedule",
        )
        for item in items
    )


def ambient_line(seed: int, agent_id: str, tick: int) -> str:
    """Deterministic canned line id for one ambient utterance."""
    stream = Stream(seed, "ambient", agent_id, str(tick))
    return stream.pick(LINE_IDS)
