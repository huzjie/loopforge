# -*- coding: utf-8 -*-
"""loopforge: Looped Transformers with Manifold-Constrained Hyper-Connections.

When SenseNova open-sourced Loop-9B (Looped Transformer + manifold-constrained
hyper-connections), the headline was remarkable: 32 distinct decoder layers
executed 56 decoding passes, so a 9B-parameter model approaches the performance
of a 27B one. The mechanism is *weight tying in depth* -- run the SAME block
many times instead of stacking different ones -- plus *hyper-connections* that
keep signal flowing stably through those extra passes.

loopforge is a self-contained, zero-dependency reference implementation of the
three ideas that make "Loop" notable:

1. **Looped blocks (compute-for-params)** -- a single weight-tied block is
   re-run `loops` times, extending the *effective depth* without adding
   parameters. The more loops, the deeper the effective model.
2. **Manifold-constrained hyper-connections (mHC)** -- instead of one residual
   stream, several streams (identity / residual / gate) are mixed with weights
   constrained on a manifold (unit-sum, bounded), which keeps activations and
   gradients stable even at high loop counts.
3. **Reproducible convergence benchmark** -- a power-iteration task where the
   looped block provably converges to the ground-truth top eigenvector, so the
   "more loops -> better" claim is measured, not asserted.

The "model" is a trainable, skill-parametrized scorer (deterministic, seeded,
monotonic in skill) layered over real vector math, so the whole loop ->
hyper-connect -> stabilize -> decide pipeline runs end-to-end on a laptop in
seconds, on the Python standard library only.
"""

__version__ = "0.1.0"
__all__ = ["__version__"]
