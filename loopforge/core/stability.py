# -*- coding: utf-8 -*-
"""Signal-propagation stability metrics (activation norm, drift, collapse)."""
from __future__ import annotations

import math


def activation_norm(h):
    return math.sqrt(sum(v * v for v in h)) or 1e-12


def collapsed(h, tol=1e-6):
    """True if the state has collapsed to a near-constant vector (zero variance)."""
    n = len(h)
    mean = sum(h) / n
    var = sum((v - mean) ** 2 for v in h) / n
    return var < tol


def healthy(h, lo=0.5, hi=2.0):
    """A state is healthy if its norm stays in the band AND it has not collapsed."""
    n = activation_norm(h)
    return lo <= n <= hi and not collapsed(h)


def growth(trace):
    """Per-step norm ratios: >1 expands, <1 contracts, ==1 stable."""
    return [trace[i + 1] / trace[i] if trace[i] else float("inf")
            for i in range(len(trace) - 1)]


def spectral_radius(matrix, iters=200):
    """Estimate the dominant eigenvalue via power iteration (for drift analysis)."""
    dim = len(matrix)
    v = [1.0] * dim
    for _ in range(iters):
        w = [sum(row[i] * v[i] for i in range(dim)) for row in matrix]
        n = math.sqrt(sum(x * x for x in w)) or 1e-12
        v = [x / n for x in w]
    av = [sum(row[i] * v[i] for i in range(dim)) for row in matrix]
    return sum(av[i] * v[i] for i in range(dim))
