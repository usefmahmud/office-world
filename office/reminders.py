"""Commitments said in chat become world reminders; overdue ones become nudges (WORLD-6 / W4).

Pure Python: no I/O, no clock, no randomness, no LLM. Parsing is rule-based.
Time is passed in as an explicit ``now`` (minute of day), never read from the clock.

Scope of v1 (see ``parse_commitment``):
* First-person future verb + a clock time -> commitment.
* Clock time: Arabic (``الساعة 3``, ``الساعه ٩ الصبح``) or English (``at 3pm``).
* No period word on an hour 1..6 -> PM, 7..11 -> AM; the result is flagged
  ``ambiguous`` so the caller can ask one question instead of guessing silently.

A ``nudge`` is a view overlay on the late agent's desk. It is NOT a ninth event type:
the contract's closed vocabulary (§1.4) stays at eight.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from typing import Any, Sequence

from .schedule import AGENTS, MINUTES_PER_DAY

#: First-person future markers (Egyptian + MSA + English). Matched as substrings.
FUTURE_MARKERS: tuple[str, ...] = (
    "هبعت", "هبعتلك", "هبعتها", "هخلص", "هخلّص", "هكلم", "هكلّم", "هرجعلك", "هرجع",
    "هراجع", "هتابع", "هبلغ", "هعمل", "هحاول", "هوصل", "هبدأ", "هبدا",
    "I'll", "I will", "I'm going to",
)
#: Period words that disambiguate an hour (Arabic, after the time).
AM_WORDS: tuple[str, ...] = ("الصبح", "الصباح", "الفجر")
PM_WORDS: tuple[str, ...] = ("الضهر", "الظهر", "العصر", "المغرب", "بالليل", "بليل", "الليل", "بالمسا", "المسا")

_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_AR_TIME = re.compile(r"الساع[ةه]\s*(\d{1,2})(?::(\d{2}))?")
_EN_TIME = re.compile(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b", re.IGNORECASE)

#: Nudge fires this many minutes after due (grace, so a minute late is not nagging).
DEFAULT_GRACE_MIN = 5
MAX_TEXT = 200


@dataclass(frozen=True)
class Commitment:
    owner: str
    text: str
    due: int          # minute of day
    ambiguous: bool   # True when AM/PM had to be defaulted


@dataclass(frozen=True)
class Reminder:
    id: str
    owner: str
    text: str
    due: int
    status: str = "pending"   # pending | nudged | done
    scope: str = "world"      # reminders are global, not private to the chat


@dataclass(frozen=True)
class Nudge:
    reminder_id: str
    agent: str
    late_min: int
    text_id: str = "nudge-overdue"


def _normalise(text: str) -> str:
    return text.translate(_ARABIC_DIGITS)


def _resolve_hour(hour: int, minute: int, period: str | None) -> tuple[int, bool] | None:
    """Return ``(minute_of_day, ambiguous)`` or ``None`` if the time is not a clock time."""
    if not (0 <= minute <= 59) or not (0 <= hour <= 23):
        return None
    if period == "am":
        h = 0 if hour == 12 else hour
        return (h * 60 + minute, False) if hour <= 12 else None
    if period == "pm":
        if hour > 12:
            return None
        h = hour if hour == 12 else hour + 12
        return (h * 60 + minute, False)
    if hour == 0 or hour > 12:
        return (hour * 60 + minute, False)          # 00:xx and 13..23 are unambiguous
    if hour == 12:
        return (12 * 60 + minute, True)             # 12 alone: noon by default, flagged
    if 1 <= hour <= 6:
        return ((hour + 12) * 60 + minute, True)    # 1..6 with no period -> PM
    return (hour * 60 + minute, True)               # 7..11 with no period -> AM


def _has_future_marker(text: str) -> bool:
    return any(marker in text for marker in FUTURE_MARKERS)


def parse_commitment(owner: str, message: str) -> Commitment | None:
    """Extract a timed commitment from one chat message, or ``None``.

    Deterministic: the same ``(owner, message)`` always yields the same result.
    """
    if owner not in AGENTS:
        raise ValueError(f"unknown agent {owner!r}")
    text = _normalise(message).strip()
    if not text or not _has_future_marker(text):
        return None

    ar = _AR_TIME.search(text)
    if ar:
        hour, minute = int(ar.group(1)), int(ar.group(2) or 0)
        tail = text[ar.end():ar.end() + 20]
        period = "am" if any(w in tail for w in AM_WORDS) else (
            "pm" if any(w in tail for w in PM_WORDS) else None)
        resolved = _resolve_hour(hour, minute, period)
    else:
        en = _EN_TIME.search(text)
        if not en:
            return None
        hour, minute = int(en.group(1)), int(en.group(2) or 0)
        period = en.group(3).lower() if en.group(3) else None
        resolved = _resolve_hour(hour, minute, period)
        if resolved is not None and period is None:
            resolved = (resolved[0], True)
    if resolved is None:
        return None
    due, ambiguous = resolved
    if not (0 <= due < MINUTES_PER_DAY):
        return None
    return Commitment(owner=owner, text=text[:MAX_TEXT], due=due, ambiguous=ambiguous)


def reminder_id(owner: str, due: int, text: str) -> str:
    digest = hashlib.sha256(f"{owner}|{due}|{text}".encode("utf-8")).hexdigest()[:8]
    return f"rem-{owner}-{due:04d}-{digest}"


def add_commitment(store: Sequence[Reminder], commitment: Commitment) -> tuple[Reminder, ...]:
    """Add a reminder for a commitment. Same owner/due/text is idempotent."""
    rid = reminder_id(commitment.owner, commitment.due, commitment.text)
    if any(r.id == rid for r in store):
        return tuple(store)
    item = Reminder(id=rid, owner=commitment.owner, text=commitment.text, due=commitment.due)
    return tuple(sorted([*store, item], key=lambda r: (r.due, r.id)))


def mark_done(store: Sequence[Reminder], rid: str) -> tuple[Reminder, ...]:
    if not any(r.id == rid for r in store):
        raise KeyError(rid)
    return tuple(replace(r, status="done") if r.id == rid else r for r in store)


def due_nudges(
    store: Sequence[Reminder],
    now: int,
    grace: int = DEFAULT_GRACE_MIN,
) -> tuple[tuple[Reminder, ...], list[Nudge]]:
    """Advance the clock to ``now``. Returns ``(new_store, nudges)``.

    A pending reminder becomes ``nudged`` once ``now >= due + grace``, and is nudged
    exactly once. ``done`` reminders never nudge. Calling again with the same ``now``
    produces no new nudges.
    """
    nudges: list[Nudge] = []
    out: list[Reminder] = []
    for r in sorted(store, key=lambda r: (r.due, r.id)):
        if r.status == "pending" and now >= r.due + grace:
            out.append(replace(r, status="nudged"))
            nudges.append(Nudge(reminder_id=r.id, agent=r.owner, late_min=now - r.due))
        else:
            out.append(r)
    return tuple(out), nudges


def snapshot(store: Sequence[Reminder]) -> list[dict[str, Any]]:
    """Canonical, JSON-ready view of the store."""
    return [
        {"id": r.id, "owner": r.owner, "text": r.text, "due": r.due,
         "status": r.status, "scope": r.scope}
        for r in sorted(store, key=lambda r: (r.due, r.id))
    ]
