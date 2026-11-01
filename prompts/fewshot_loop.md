# Few-shot: Loop vs Depth

Q: A 9B model with 32 layers runs 56 passes. Is this "deeper" or just "longer"?
A: Effective depth 56, parameter depth 32. The extra 24 passes are pure compute
   on reused weights, so it is deeper in *computation* without being wider in
   *parameters*. That is the compute-for-params trade.

Q: Why does reusing one block N times risk collapse?
A: Unconstrained recurrence with spectral radius != 1 drifts: radius > 1 blows the
   norm up, radius < 1 shrinks it to zero, and repeated nonlinear mixing can
   equalize entries. Constrained hyper-connections keep the norm in band.

Q: How do you measure that more loops actually help?
A: Use a task with a provable ground truth (e.g. power iteration to a top
   eigenvector) and measure cosine similarity to the answer as loops increase.
