# Benchmark

Two monotonic dimensions, both reproducible (deterministic seeds, stdlib only).

## Skill (0.5 random -> 1.0 expert), loops fixed at 8

| skill | recall | precision | fpr | accuracy | f1 |
|---|---|---|---|---|---|
| 0.5 | 0.200 | 1.000 | 0.000 | 0.200 | 0.333 |
| 0.6 | 0.400 | 1.000 | 0.000 | 0.400 | 0.571 |
| 0.7 | 0.800 | 1.000 | 0.000 | 0.800 | 0.889 |
| 0.8 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |
| 0.9 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |
| 1.0 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 |

## Loop count (compute-for-params)

| loops | convergence | eff. depth | param-efficiency | mHC health | collapse gap |
|---|---|---|---|---|---|
| 1 | 0.5990 | 1 | 0.0085 | 1.000 | 0 |
| 2 | 0.7629 | 2 | 0.0169 | 1.000 | 4 |
| 4 | 0.9220 | 4 | 0.0339 | 1.000 | 5 |
| 8 | 0.9909 | 8 | 0.0678 | 1.000 | 5 |
| 16 | 0.9999 | 16 | 0.1356 | 1.000 | 5 |
| 32 | 1.0000 | 32 | 0.2712 | 1.000 | 5 |

Regenerate with `python -m loopforge.cli bench`.
