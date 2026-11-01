# Power-Iteration Convergence Analysis

A looped block performs normalized power iteration ``v <- normalize(A v)`` on a
symmetric, diagonal-dominant matrix A. Analyze:

- **Convergence rate**: dominated by the ratio of the second to first eigenvalue.
- **Monotonicity**: cosine similarity to the top eigenvector increases with passes.
- **Parameter efficiency**: the matrix A is the only learnable state; the loop
  count is pure compute.

Explain how "more loops -> better" follows from the eigenvalue gap, and where it
stops helping.
