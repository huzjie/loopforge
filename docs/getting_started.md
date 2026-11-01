# Getting Started

loopforge is a zero-dependency (stdlib-only) reference implementation of Looped
Transformers with Manifold-Constrained Hyper-Connections (mHC), inspired by
SenseNova Loop-9B's "32 layers, 56 passes" trick.

## Quick start

```bash
python -m loopforge.cli doctor     # smoke check
python -m loopforge.cli bench      # monotonic benchmark
python -m loopforge.cli run        # run all tasks
python -m loopforge.cli stability  # mHC vs naive deep recurrence
```

## Serve over HTTP

```bash
python -m loopforge.cli serve --port 8431
curl http://127.0.0.1:8431/health
curl http://127.0.0.1:8431/v1/bench
```

## Run the examples

```bash
python examples/loop_convergence.py
python examples/mhc_stability.py
```

No pip install required; everything imports from the standard library.
