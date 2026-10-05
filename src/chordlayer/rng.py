"""Deterministic, seeded randomness for reproducible melodies.

FL re-runs ``apply()`` whenever a control changes, so every random choice must
derive from the user-facing *Seed* knob: same seed in, same melody out.
"""

from __future__ import annotations

import random
from dataclasses import dataclass


def normalize_seed(value: int) -> int:
    """Fold any integer into a stable 0..2**31-1 seed."""
    return abs(int(value)) % (2**31)


@dataclass
class Rng:
    """Thin wrapper around :class:`random.Random` with music-friendly helpers."""

    seed: int

    def __post_init__(self) -> None:
        self._random = random.Random(normalize_seed(self.seed))

    # -- primitives ---------------------------------------------------------

    def chance(self, probability: float) -> bool:
        """True with the given probability (0..1)."""
        return self._random.random() < probability

    def uniform(self, lo: float, hi: float) -> float:
        return self._random.uniform(lo, hi)

    def choice(self, options):
        options = list(options)
        if not options:
            raise ValueError("choice() needs at least one option")
        return self._random.choice(options)

    def weighted(self, options, weights) :
        """Pick from ``options`` with matching ``weights`` (zero-weight excluded)."""
        pairs = [(option, weight) for option, weight in zip(options, weights) if weight > 0]
        if not pairs:
            raise ValueError("weighted() needs at least one positive weight")
        total = sum(weight for _, weight in pairs)
        ticket = self._random.random() * total
        for option, weight in pairs:
            ticket -= weight
            if ticket <= 0:
                return option
        return pairs[-1][0]

    def shuffle(self, items):
        items = list(items)
        self._random.shuffle(items)
        return items
