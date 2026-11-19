"""Seeded, per-agent random substreams.

Deterministic by construction: stream(agent_id) always yields the same numbers
for the same world seed, and one agent's draws never shift another's.
"""

from __future__ import annotations

import hashlib

_MASK = (1 << 64) - 1


class SubStream:
    """SplitMix64 — tiny, fast, and reproducible across Python versions."""

    def __init__(self, seed: int, name: str) -> None:
        digest = hashlib.sha256(f"{seed}:{name}".encode("utf-8")).digest()
        self._state = int.from_bytes(digest[:8], "big")

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return (z ^ (z >> 31)) & _MASK

    def below(self, n: int) -> int:
        if n <= 0:
            raise ValueError("n must be positive")
        return self.next_u64() % n

    def between(self, lo: int, hi: int) -> int:
        """Inclusive range."""
        if hi < lo:
            raise ValueError("hi must be >= lo")
        return lo + self.below(hi - lo + 1)

    def pick(self, items: list):
        return items[self.below(len(items))]
