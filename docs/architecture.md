# Architecture

```
            ┌────────────────────────────────────────┐
            │                Engine                   │
            │  loop -> hyper-connect -> measure -> decide │
            └────────────────────────────────────────┘
                │              │              │
        ┌───────▼─────┐  ┌─────▼─────┐  ┌─────▼──────┐
        │ LoopedBlock │  │    mHC    │  │ Stability  │
        │ weight-tied │  │ manifold  │  │ norm/drift │
        │  recurrence │  │  mixing   │  │ /collapse  │
        └───────┬─────┘  └───────────┘  └────────────┘
                │
        ┌───────▼─────┐  ┌─────────────┐  ┌──────────┐
        │   Model     │  │   Oracle    │  │  Corpus  │
        │ skill score │  │ ground truth│  │ matrices │
        └─────────────┘  └─────────────┘  └──────────┘
```

- **LoopedBlock** (`core/loop_block.py`) — one weight-tied map re-run `loops`
  times; `normalize=True` is power iteration (converges), `False` is naive
  (drifts).
- **HyperConnections** (`core/mhc.py`) — identity/residual/gate streams mixed
  with manifold-constrained weights, then projected onto the unit sphere.
- **Stability** (`core/stability.py`) — activation norm, drift, collapse,
  spectral-radius estimation.
- **Model** (`core/model.py`) — skill-parametrized scorer (0.5 random -> 1.0
  expert) over the oracle convergence signal.
- **Oracle / Corpus** (`world/`) — diagonal-dominant matrices whose top
  eigenvector is the ground truth.
