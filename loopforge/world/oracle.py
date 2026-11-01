# -*- coding: utf-8 -*-
"""Oracle: ground-truth top eigenvector + train/bench signals.

The oracle computes the true top eigenvector of each task matrix by running
power iteration to convergence (many more passes than any benchmarked loop),
so we can score a short loop against the converged answer.
"""
from __future__ import annotations

from ..utils.linalg import matvec, normalize, cos
from .corpus import make_batch

TRUE_SIGNAL = 0.90      # strong evidence the task is genuinely "solved"
FALSE_SIGNAL = 0.10     # weak evidence (distractor)

_CACHE = {}


def ground_truth(task, iters=300):
    """The converged top eigenvector of a task's matrix (cached per task id)."""
    tid = task["id"]
    if tid in _CACHE:
        return _CACHE[tid]
    v = list(task["init"])
    for _ in range(iters):
        v = normalize(matvec(task["matrix"], v))
    _CACHE[tid] = v
    return v


def convergence_of(task, loops):
    """cos similarity between a `loops`-pass estimate and the ground truth."""
    v = list(task["init"])
    for _ in range(loops):
        v = normalize(matvec(task["matrix"], v))
    return cos(v, ground_truth(task))


def signal_for(task, loops):
    """Deterministic convergence signal for a (task, loops) pair."""
    return convergence_of(task, loops)


def make_batch():
    """Re-export the corpus batch (alias so callers use one entry point)."""
    from .corpus import make_batch as _mb
    return _mb()


def true_count(batch):
    return len(batch)
