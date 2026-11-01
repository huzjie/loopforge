# -*- coding: utf-8 -*-
"""Demonstrate compute-for-params: more loops -> better convergence.

The looped block re-runs the SAME matrix (fixed parameters) more times; each
pass is one normalized matrix-vector product (power iteration), so the state
converges to the ground-truth top eigenvector. Parameter count never changes,
only the compute budget (loop count) does.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loopforge.world.oracle import make_batch, signal_for

task = make_batch()[0]
print("task=%s dim=%d params=%d" % (task["id"], task["dim"], task["dim"] ** 2 + task["dim"]))
print("loop count -> convergence (cos to ground truth):")
print("  loops   convergence")
for loops in (1, 2, 4, 8, 16, 32):
    print("  %5d   %.4f" % (loops, signal_for(task, loops)))
print()
print("Same parameters, deeper effective model. That is compute-for-params.")
