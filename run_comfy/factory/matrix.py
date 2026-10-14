"""Variation matrix: which combinations of hooks x scripts x avatars x voices x styles to render.

Modes
- full:     every combination (Cartesian product)
- random:   n distinct random combinations
- pairwise: the smallest set (greedy) where every pair of options from two different axes appears
            together at least once - the classic way to test many factors without the blow-up.
            4 hooks x 2 scripts x 3 avatars x 2 voices = 48 full, ~12 pairwise.
Variant codes encode the combination (H2-S1-A3-V1) so ad-platform results map back to creative choices.
"""
from __future__ import annotations

import itertools
import random

LETTERS = {"hook": "H", "body": "S", "cta": "C", "avatar": "A", "voice": "V", "style": "B", "headline": "L"}


def code(combo: dict[str, int]) -> str:
    return "-".join(f"{LETTERS.get(k, k[:1].upper())}{v + 1}" for k, v in combo.items())


def full(sizes: dict[str, int]) -> list[dict[str, int]]:
    keys = list(sizes)
    return [dict(zip(keys, c)) for c in itertools.product(*(range(sizes[k]) for k in keys))]


def rand(sizes: dict[str, int], n: int, seed: int = 0) -> list[dict[str, int]]:
    total = 1
    for v in sizes.values():
        total *= v
    if n >= total:
        return full(sizes)
    rng = random.Random(seed)
    seen, out = set(), []
    while len(out) < n:
        c = tuple(rng.randrange(sizes[k]) for k in sizes)
        if c not in seen:
            seen.add(c)
            out.append(dict(zip(sizes, c)))
    return out


def pairwise(sizes: dict[str, int], seed: int = 0, candidates: int = 60) -> list[dict[str, int]]:
    keys = [k for k in sizes if sizes[k] > 0]
    if len(keys) < 2:
        return full(sizes)
    rng = random.Random(seed)
    uncovered = {(a, i, b, j) for a, b in itertools.combinations(keys, 2)
                 for i in range(sizes[a]) for j in range(sizes[b])}
    out: list[dict[str, int]] = []

    def gain(c):
        return sum((a, c[a], b, c[b]) in uncovered for a, b in itertools.combinations(keys, 2))

    while uncovered:
        # seed each candidate with one still-uncovered pair so progress is guaranteed
        a, i, b, j = next(iter(sorted(uncovered)))
        best, best_gain = None, -1
        for _ in range(candidates):
            c = {k: rng.randrange(sizes[k]) for k in keys}
            c[a], c[b] = i, j
            g = gain(c)
            if g > best_gain:
                best, best_gain = c, g
        out.append(best)
        for x, y in itertools.combinations(keys, 2):
            uncovered.discard((x, best[x], y, best[y]))
    return out


def build(sizes: dict[str, int], mode: str = "pairwise", n: int | None = None, seed: int = 0) -> list[dict[str, int]]:
    sizes = {k: v for k, v in sizes.items() if v > 0}
    if mode == "full":
        combos = full(sizes)
    elif mode == "random":
        combos = rand(sizes, n or 10, seed)
    elif mode == "pairwise":
        combos = pairwise(sizes, seed)
    else:
        raise ValueError(f"unknown matrix mode {mode!r}")
    if n and mode != "random":
        combos = combos[:n]
    # stable order: sort by code so H1 variants come first
    return sorted(combos, key=lambda c: tuple(c.values()))


def count(sizes: dict[str, int], mode: str, n: int | None = None) -> int:
    return len(build(sizes, mode, n))
