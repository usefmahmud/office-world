"""Deterministic random substreams for the office world.

Design rules (see ``docs/CONTRACT-SIM.md``):

* Pure integer arithmetic (splitmix64) — no ``random`` module, no float, no
  wall clock.  The same seed produces the same numbers on every machine and
  every Python build.
* Streams are addressed by *labels*, never by a global counter, so adding a
  seventh agent (or an extra draw for one agent) cannot reshuffle anybody
  else's day.  ``Stream(world_seed, "schedule", "omar")`` is independent of
  ``Stream(world_seed, "schedule", "layla")``.

``derive()`` gives the single-authority version of the same idea: a stream
created from a label list only depends on that label list.
"""

from __future__ import annotations

import hashlib

MASK64 = 0xFFFFFFFFFFFFFFFF
_GOLDEN = 0x9E3779B97F4A7C15
_MIX1 = 0xBF58476D1CE4E5B9
_MIX2 = 0x94D049BB133111EB


def splitmix64(state: int) -> int:
    """One splitmix64 output for ``state`` (a pure 64-bit mixing function)."""
    z = (state + _GOLDEN) & MASK64
    z = ((z ^ (z >> 30)) * _MIX1) & MASK64
    z = ((z ^ (z >> 27)) * _MIX2) & MASK64
    return (z ^ (z >> 31)) & MASK64


def stream_seed(world_seed: int, *labels: str) -> int:
    """Map ``(world_seed, labels...)`` to a 64-bit substream seed.

    Unambiguous: every label is length-prefixed, so ``("ab", "c")`` and
    ``("a", "bc")`` hash differently.
    """
    h = hashlib.blake2b(digest_size=8)
    h.update((world_seed & MASK64).to_bytes(8, "big"))
    for label in labels:
        raw = label.encode("utf-8")
        h.update(len(raw).to_bytes(4, "big"))
        h.update(raw)
    return int.from_bytes(h.digest(), "big")


class Stream:
    """A small, reproducible random source addressed by labels."""

    __slots__ = ("_root", "_state")

    def __init__(self, world_seed: int, *labels: str) -> None:
        self._root = (world_seed & MASK64, labels)
        self._state = stream_seed(world_seed, *labels)

    # -- raw ---------------------------------------------------------------
    def next_u64(self) -> int:
        self._state = (self._state + _GOLDEN) & MASK64
        return splitmix64(self._state)

    def below(self, n: int) -> int:
        """Uniform integer in ``[0, n)``."""
        if n <= 0:
            raise ValueError("n must be positive")
        return self.next_u64() % n

    def between(self, low: int, high: int) -> int:
        """Uniform integer in ``[low, high]`` (inclusive)."""
        if high < low:
            raise ValueError("high must be >= low")
        return low + self.below(high - low + 1)

    def chance(self, num: int, den: int) -> bool:
        """True with probability ``num/den`` (integer comparison, no float)."""
        if den <= 0:
            raise ValueError("den must be positive")
        return self.below(den) < num

    def pick(self, seq: tuple[str, ...]) -> str:
        if not seq:
            raise ValueError("empty sequence")
        return seq[self.below(len(seq))]

    # -- addressing --------------------------------------------------------
    def derive(self, *labels: str) -> "Stream":
        """A substream that depends only on the root labels plus ``labels``."""
        root_seed, root_labels = self._root
        return Stream(root_seed, *root_labels, *labels)
