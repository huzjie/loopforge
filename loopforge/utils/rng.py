# -*- coding: utf-8 -*-
"""Deterministic pseudo-random helpers driven by md5(key) seeds.

Everything in loopforge is reproducible: given the same seed, the same key
always yields the same value, regardless of process or platform.
"""
from __future__ import annotations

import hashlib
import random


def _seed(key: str) -> int:
    """Derive a 64-bit seed from an arbitrary string key (first 16 hex of md5)."""
    h = hashlib.md5(key.encode("utf-8")).hexdigest()[:16]
    return int(h, 16)


def stable_float(key: str, lo: float = 0.0, hi: float = 1.0) -> float:
    return random.Random(_seed(key)).uniform(lo, hi)


def stable_int(key: str, lo: int, hi: int) -> int:
    return random.Random(_seed(key)).randint(lo, hi)


def stable_vector(key: str, n: int, lo: float = -1.0, hi: float = 1.0) -> list:
    r = random.Random(_seed(key))
    return [r.uniform(lo, hi) for _ in range(n)]


def stable_choice(key: str, items: list):
    if not items:
        raise ValueError("stable_choice on empty sequence")
    return items[random.Random(_seed(key)).randrange(len(items))]


def stable_matrix(key: str, rows: int, cols: int, lo: float = -1.0, hi: float = 1.0) -> list:
    r = random.Random(_seed(key))
    return [[r.uniform(lo, hi) for _ in range(cols)] for _ in range(rows)]


def stable_shuffle(key: str, items: list) -> list:
    out = list(items)
    random.Random(_seed(key)).shuffle(out)
    return out
