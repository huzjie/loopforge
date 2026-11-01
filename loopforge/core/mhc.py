# -*- coding: utf-8 -*-
"""Manifold-Constrained Hyper-Connections (mHC).

A plain deep network carries one residual stream; a hyper-connected one carries
SEVERAL streams (identity / residual / gate) and mixes them with weights that
live on a constrained manifold. In SenseNova Loop the point of mHC is to keep
signal propagation stable through extra recurrent passes.

Here the manifold constraint is concrete and testable:

- mixing weights are clipped to ``[epsilon, 1-epsilon]`` and renormalized to
  unit sum (so no stream can dominate or vanish), then
- the mixed state is projected back onto the unit sphere (scale normalization),
  which is what keeps the norm in the healthy band no matter how deep the loop.

The contrast case is the naive loop (``normalize=False`` without mHC), which
drifts and collapses/blows up at depth.
"""
from __future__ import annotations

import math

from ..utils.linalg import clamp, matvec, normalize, weighted_sum


def sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


class HyperConnections:
    def __init__(self, alpha=None, epsilon=1e-3, n_streams=3):
        self.epsilon = epsilon
        self.n_streams = n_streams
        self.alpha = alpha or [0.5, 0.3, 0.2]

    def _constrain(self, weights):
        """Project mixing weights onto the constraint manifold."""
        clipped = [clamp(w, self.epsilon, 1.0 - self.epsilon) for w in weights]
        s = sum(clipped) or 1.0
        return [c / s for c in clipped]

    def apply(self, h, matrix):
        """One hyper-connected pass: mix identity / residual / gate streams.

        Returns the mixed, scale-normalized state (norm ~ 1, i.e. stable).
        """
        residual = matvec(matrix, h)
        gate = [sigmoid(v) * v for v in h]
        streams = [h, residual, gate]
        alpha = self._constrain(self.alpha[:self.n_streams])
        mixed = weighted_sum(streams, alpha)
        # manifold projection: constrain the state onto the unit sphere
        return normalize(mixed)

    def forward(self, x, matrix, loops):
        h = list(x)
        trace = [sum(v * v for v in h) ** 0.5 or 1e-12]
        for _ in range(loops):
            h = self.apply(h, matrix)
            trace.append((sum(v * v for v in h) ** 0.5) or 1e-12)
        return h, trace
