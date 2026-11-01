# Loop Depth (compute-for-params)

The key idea of a Looped Transformer: do not stack N distinct layers, re-run ONE
block N times. Parameters stay fixed; only the loop count grows.

- **effective depth** = loop count
- **parameter count** = one block (independent of loop count)

The benchmark measures this directly with a power-iteration task: the block
performs ``v <- normalize(A v)``, and cosine similarity to the ground-truth top
eigenvector is the score. More loops -> higher similarity -> better, until it
saturates at 1.0. See `loopforge/bench.py` `run_loop_curve()`.

This is the "32 layers -> 56 passes" of SenseNova Loop: the extra passes are
pure compute on reused weights.
