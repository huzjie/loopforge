# Manifold-Constrained Hyper-Connections (mHC)

A plain deep network carries one residual stream. A hyper-connected one carries
several streams and mixes them with constrained weights. The manifold
constraints in loopforge are concrete:

1. **Bounded weights** — mixing weights are clipped to ``[epsilon, 1-epsilon]``
   so no stream dominates or dies.
2. **Unit sum** — weights renormalize to sum 1.
3. **Unit-sphere projection** — the mixed state is scale-normalized so the norm
   stays in the healthy band at any depth.

The contrast case is the naive un-normalized recurrence, which drifts (norm
shrinks toward zero for the contractive matrices used here) and leaves the
healthy band. `stability_report()` quantifies the gap.
