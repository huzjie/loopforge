# -*- coding: utf-8 -*-
"""Ground-truth corpus: diagonal-dominant positive matrices for power iteration.

Each task is a symmetric, diagonal-dominant matrix with a UNIQUE dominant
eigenvalue (the diagonal strictly decreases, off-diagonal entries are tiny and
non-negative). Under the Perron-Frobenius theorem the top eigenvector is
all-positive, so the all-ones start vector has a positive overlap with it and
power iteration converges monotonically. That monotonic convergence is the
whole point: it lets us MEASURE "more loops -> better" instead of asserting it.

The "params" story mirrors SenseNova Loop: the task's matrix is the only
learnable state (like one weight-tied block); the loop count is the compute
budget you spend to approach the ground truth.
"""
from __future__ import annotations

from ..utils.rng import stable_float
from ..utils.linalg import normalize

# task id -> (dimension, seed) ; 5 tasks of increasing difficulty
TASKS = [
    ("copy_d6", 6),
    ("parity_d8", 8),
    ("bracket_d10", 10),
    ("sum_d12", 12),
    ("sort_d14", 14),
]


def build_matrix(dim, seed):
    """Symmetric, diagonal-dominant, CONTRACTIVE matrix (top eigenvalue < 1).

    The strictly-decreasing diagonal keeps a UNIQUE dominant eigenvalue close
    to 0.95, so power iteration (normalized) converges fast to the top
    eigenvector, while the naive (un-normalized) recurrence ``h = A h``
    contracts toward zero -- which is exactly the drift that mHC must prevent.
    """
    m = [[0.0] * dim for _ in range(dim)]
    for i in range(dim):
        m[i][i] = 0.95 / (1.0 + 0.3 * i)   # 0.95 -> ~0.19, strictly decreasing
    for i in range(dim):
        for j in range(i + 1, dim):
            v = stable_float("%s:%d:%d" % (seed, i, j), 0.0, 0.02)
            m[i][j] = v
            m[j][i] = v
    return m


def make_batch():
    """Return the full task list (each with id, dim, seed, matrix, init)."""
    tasks = []
    for idx, (tid, dim) in enumerate(TASKS):
        seed = "task:%s" % tid
        init = normalize([1.0] * dim)    # all-ones start (positive overlap with v*)
        tasks.append({
            "id": tid,
            "dim": dim,
            "seed": seed,
            "matrix": build_matrix(dim, seed),
            "init": init,
        })
    return tasks


def task_ids():
    return [t["id"] for t in make_batch()]
