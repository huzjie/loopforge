# Examples

Runnable, dependency-free demonstrations of loopforge's two core ideas.

| Script | What it shows |
|---|---|
| `loop_convergence.py` | "More loops -> better": power-iteration convergence to the ground-truth eigenvector as the loop count grows (compute-for-params). |
| `mhc_stability.py` | Why hyper-connections matter: the naive deep recurrence drifts/collapses while the mHC-constrained recurrence stays stable. |

Run from the repo root:

```bash
python examples/loop_convergence.py
python examples/mhc_stability.py
```
