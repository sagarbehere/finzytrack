"""The demo generator's seeded random stream (EP-10; ADR-0022 "seeded by name").

SplitMix64 (Steele, Lea and Flood 2014), written out here instead of `random`, so the bytes a seed gives do not
depend on the Python version's `random` internals and no ambient randomness can slip in.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from decimal import Decimal

_MASK = (1 << 64) - 1
_SEED_PREFIX = "finzytrack-demo:"


class SeededRng:
    """A deterministic stream of 64-bit values; one per fragment, seeded from the fragment's name."""

    def __init__(self, seed: int) -> None:
        self._state = seed & _MASK

    @classmethod
    def for_name(cls, name: str) -> SeededRng:
        digest = hashlib.sha256((_SEED_PREFIX + name).encode()).digest()
        return cls(int.from_bytes(digest[:8], "big"))

    def next_u64(self) -> int:
        self._state = (self._state + 0x9E3779B97F4A7C15) & _MASK
        z = self._state
        z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & _MASK
        z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & _MASK
        return z ^ (z >> 31)

    def below(self, n: int) -> int:
        """A uniform integer in [0, n), without modulo bias (rejection sampling)."""
        if n <= 0:
            msg = f"bound must be positive, got {n}"
            raise ValueError(msg)
        limit = ((1 << 64) // n) * n
        while True:
            x = self.next_u64()
            if x < limit:
                return x % n

    def between(self, lo: int, hi: int) -> int:
        """A uniform integer in [lo, hi]."""
        return lo + self.below(hi - lo + 1)

    def choice[T](self, items: Sequence[T]) -> T:
        return items[self.below(len(items))]

    def weighted[T](self, items: Sequence[tuple[T, int]]) -> T:
        pick = self.below(sum(w for _, w in items))
        for item, weight in items:
            if pick < weight:
                return item
            pick -= weight
        msg = "unreachable: the pick is below the total weight"
        raise AssertionError(msg)

    def cents(self, lo: Decimal, hi: Decimal) -> Decimal:
        """A uniform amount in [lo, hi], in whole cents."""
        return Decimal(self.between(int(lo * 100), int(hi * 100))).scaleb(-2)
