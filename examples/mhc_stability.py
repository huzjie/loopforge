# -*- coding: utf-8 -*-
"""Demonstrate why manifold-constrained hyper-connections are needed at depth.

Compare two deep recurrences on the same transition matrix:
- naive:    h = A h            (un-normalized) -> drifts out of the healthy band
- mHC:      multi-stream mix constrained on a manifold -> stays unit-norm stable
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loopforge.core.loop_block import LoopedBlock
from loopforge.core.mhc import HyperConnections
from loopforge.core.stability import healthy, activation_norm
from loopforge.world.oracle import make_batch

task = make_batch()[0]
matrix = task["matrix"]
init = task["init"]
loops = 32

naive = LoopedBlock(matrix, normalize=False)
h_naive, _ = naive.forward(init, loops)

mhc = HyperConnections()
h_mhc, _ = mhc.forward(init, matrix, loops)

print("task=%s loops=%d" % (task["id"], loops))
print("  naive:  norm=%.4f  healthy=%s" % (activation_norm(h_naive), healthy(h_naive)))
print("  mHC:    norm=%.4f  healthy=%s" % (activation_norm(h_mhc), healthy(h_mhc)))
print()
print("mHC keeps signal propagation stable at depth; the naive recurrence drifts.")
