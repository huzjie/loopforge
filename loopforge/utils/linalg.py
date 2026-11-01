# -*- coding: utf-8 -*-
"""Tiny linear-algebra helpers for real vector math (no numpy).

loopforge actually runs the recurrent blocks, so it needs honest vector ops:
dot products, matrix-vector products, matrix products (for symmetric-matrix
construction), norms, normalization, cosine similarity, and clamping. All are
implemented on plain Python lists.
"""
from __future__ import annotations

import math


def dot(a, b) -> float:
    return sum(x * y for x, y in zip(a, b))


def matvec(M, x) -> list:
    return [dot(row, x) for row in M]


def transpose(M) -> list:
    n = len(M)
    m = len(M[0]) if n else 0
    return [[M[i][j] for i in range(n)] for j in range(m)]


def matmul(A, B) -> list:
    Bt = transpose(B)
    return [[dot(row, col) for col in Bt] for row in A]


def norm2(x) -> float:
    return math.sqrt(dot(x, x)) or 1e-12


def normalize(x) -> list:
    n = norm2(x)
    return [v / n for v in x]


def cos(a, b) -> float:
    return clamp(dot(a, b) / (norm2(a) * norm2(b)), -1.0, 1.0)


def add(a, b) -> list:
    return [x + y for x, y in zip(a, b)]


def scale(a, s) -> list:
    return [x * s for x in a]


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def weighted_sum(streams, weights) -> list:
    """Element-wise weighted sum of several same-length vectors."""
    out = [0.0] * len(streams[0])
    for stream, w in zip(streams, weights):
        for i, v in enumerate(stream):
            out[i] += w * v
    return out
