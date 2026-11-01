# Signal Propagation & Stability

Deep recurrence has three failure modes, each measured by `core/stability.py`:

- **Drift up** — spectral radius > 1: the norm grows geometrically.
- **Drift down** — spectral radius < 1: the norm shrinks toward zero.
- **Collapse** — the state loses variance (all entries equalize).

The healthy check combines a norm band (``[0.5, 2.0]`` by default) with a
collapse test (state variance below a tolerance). mHC keeps the state on the
unit sphere, so deep recurrence stays healthy; the naive recurrence drifts out
of band as the loop count grows.
