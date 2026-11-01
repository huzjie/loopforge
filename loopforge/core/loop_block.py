# -*- coding: utf-8 -*-
"""The weight-tied recurrent block (the "Loop" of Loop-forge).

The whole idea of a Looped Transformer is that you do NOT stack N different
layers -- you re-run ONE block N times. Parameters stay fixed while the
*effective depth* grows with the number of passes. This module implements that
block on plain lists.

Two modes:

- ``normalize=True``  -> each pass is ``h = normalize(A h)``, i.e. power
  iteration. This is the mode that *converges* to the top eigenvector of the
  transition matrix, which is exactly what we benchmark ("more loops -> better").
- ``normalize=False`` -> each pass is ``h = A h`` (naive). With a spectral
  radius > 1 the activations blow up, with radius < 1 they vanish. This is the
  mode used to demonstrate WHY you need hyper-connections: unconstrained deep
  recurrence drifts out of the healthy band.
"""
from __future__ import annotations

from ..utils.linalg import matvec, normalize, norm2


class LoopedBlock:
    def __init__(self, matrix, normalize=True):
        self.matrix = matrix            # dim x dim transition matrix
        self.dim = len(matrix)
        self.normalize = normalize

    def step(self, h):
        h = matvec(self.matrix, h)
        if self.normalize:
            h = normalize(h)
        return h

    def forward(self, x, loops):
        """Run the block `loops` times from initial state `x`.

        Returns (final_state, norm_trace) where norm_trace is the L2 norm of the
        state after each pass (length loops+1).
        """
        h = list(x)
        trace = [norm2(h)]
        for _ in range(loops):
            h = self.step(h)
            trace.append(norm2(h))
        return h, trace

    @property
    def params(self):
        """Parameter count of ONE tied block (independent of loop count)."""
        return self.dim * self.dim + self.dim   # transition matrix + bias
