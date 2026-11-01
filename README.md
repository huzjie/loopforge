# loopforge

> A zero-dependency reference implementation of **Looped Transformers with
> Manifold-Constrained Hyper-Connections (mHC)** — the "32 layers, 56 passes"
> trick behind SenseNova Loop-9B. Pure Python standard library, no numpy/torch.

[![CI](https://github.com/huzjie/loopforge/actions/workflows/ci.yml/badge.svg)](https://github.com/huzjie/loopforge/actions/workflows/ci.yml)

---

## Why this exists

SenseNova open-sourced **Loop-9B**, a Looped Transformer that runs **32 distinct
decoder layers for 56 decoding passes**, so a ~9B-parameter model approaches the
quality of a ~27B one. Two mechanisms make that work:

1. **Weight tying in depth** — re-run the *same* block instead of stacking
   different ones. The extra passes are pure compute on reused weights.
2. **Manifold-constrained hyper-connections (mHC)** — several feature streams
   (identity / residual / gate) mixed with weights constrained on a manifold,
   so signal propagation stays stable through the extra passes.

loopforge captures those two ideas — plus a *provable* way to measure them — in
a self-contained, dependency-free codebase you can read and run in seconds.

## The three engineering pillars

### 1. Looped blocks (compute-for-params)

One weight-tied block, re-run `loops` times. **Parameters stay fixed; effective
depth grows with loop count.**

```python
from loopforge.core.loop_block import LoopedBlock

block = LoopedBlock(matrix, normalize=True)   # one block, fixed params
v, trace = block.forward(init, loops=32)      # 32 passes = effective depth 32
```

### 2. Manifold-constrained hyper-connections (mHC)

Mix identity / residual / gate streams with weights bounded and normalized, then
project back onto the unit sphere — deep recurrence never drifts.

```python
from loopforge.core.mhc import HyperConnections

mhc = HyperConnections()
h, trace = mhc.forward(init, matrix, loops=32)  # norm stays ~1.0
```

### 3. A provable benchmark

The looped block performs power iteration `v <- normalize(A v)` on
diagonal-dominant matrices whose top eigenvector is the ground truth. **Cosine
similarity to that eigenvector is the score** — so "more loops -> better" is
*measured*, not asserted.

## Quick start

```bash
python -m loopforge.cli doctor      # smoke check
python -m loopforge.cli bench       # monotonic benchmark (below)
python -m loopforge.cli stability   # mHC vs naive deep recurrence
python -m loopforge.cli serve       # HTTP API on :8431
```

No install needed — everything imports from the Python standard library.

## Measured results

Both dimensions are monotonic and reproducible (deterministic seeds).

**Skill** (0.5 random -> 1.0 expert), loops fixed at 8:

| skill | recall | precision | fpr | accuracy | f1 |
|---|---|---|---|---|---|
| 0.5 | 0.200 | 1.000 | 0.000 | 0.200 | 0.333 |
| 0.6 | 0.400 | 1.000 | 0.000 | 0.400 | 0.571 |
| 0.7 | 0.800 | 1.000 | 0.000 | 0.800 | 0.889 |
| 0.8 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |
| 0.9 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |
| 1.0 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |

**Loop count** (the compute-for-params axis):

| loops | convergence | eff. depth | param-efficiency | mHC health | collapse gap |
|---|---|---|---|---|---|
| 1 | 0.5990 | 1 | 0.0085 | 1.000 | 0 |
| 2 | 0.7629 | 2 | 0.0169 | 1.000 | 4 |
| 4 | 0.9220 | 4 | 0.0339 | 1.000 | 5 |
| 8 | 0.9909 | 8 | 0.0678 | 1.000 | 5 |
| 16 | 0.9999 | 16 | 0.1356 | 1.000 | 5 |
| 32 | 1.0000 | 32 | 0.2712 | 1.000 | 5 |

Convergence 0.599 -> 1.0 (more loops -> closer to ground truth); parameter
efficiency 0.0085 -> 0.2712 (32x more depth per parameter, no added weights);
mHC health stays 1.0 while the naive recurrence's collapse gap grows.

## Reuse this idea

- **Confidence formula** (`core/model.py`): `boost=(skill-0.5)*2`,
  `conf=0.5+boost*(signal-0.5)+(1-|boost|)*(noise-0.5)` — signal and noise live
  on the same scale and converge at both ends, so weak signals are not drowned
  by noise.
- **Weight-tied recurrence** (`core/loop_block.py`): one block, a `loops`
  knob, `params` independent of depth.
- **Manifold constraints** (`core/mhc.py`): clip weights to `[eps, 1-eps]`,
  renormalize to unit sum, project the state onto the unit sphere — the three
  constraints that keep deep recurrence stable.
- **Provable benchmark** (`world/`): pick a task with a closed-form ground
  truth (here: top eigenvector) so "more X -> better" is measurable.
- Ready-to-reuse prompts and recipes are in `prompts/` and `recipes/`.

## Layout

```
loopforge/
  core/      loop_block, mhc, stability, model, engine
  world/     corpus (matrices) + oracle (ground truth)
  training/  monotonic skill trainer
  serving/   stdlib HTTP API
  utils/     rng, yamlish, linalg (zero-dep)
tests/       stdlib unittest suite
examples/    loop_convergence, mhc_stability
docs/        architecture, benchmark, mhc, params-efficiency, ...
prompts/     reusable prompt templates
recipes/     yaml config recipes
deploy/      Dockerfile, k8s, helm
```

## License

MIT — see `LICENSE`.
