# -*- coding: utf-8 -*-
import unittest

from loopforge.core.loop_block import LoopedBlock
from loopforge.utils.linalg import norm2


class TestLoopBlock(unittest.TestCase):
    def setUp(self):
        self.matrix = [[0.9, 0.0], [0.0, 0.5]]

    def test_normalize_keeps_unit_norm(self):
        block = LoopedBlock(self.matrix, normalize=True)
        v, trace = block.forward([1.0, 0.0], 5)
        self.assertAlmostEqual(norm2(v), 1.0)
        self.assertEqual(len(trace), 6)

    def test_naive_contracts(self):
        block = LoopedBlock(self.matrix, normalize=False)
        v, _ = block.forward([1.0, 0.0], 5)
        # dominant eigenvalue 0.9 -> norm shrinks toward zero
        self.assertLess(norm2(v), 1.0)

    def test_params_independent_of_loops(self):
        block = LoopedBlock(self.matrix, normalize=True)
        self.assertEqual(block.params, 2 * 2 + 2)
        # params does not depend on loop count (that is the whole point)
        self.assertEqual(block.params, 6)

    def test_trace_length(self):
        block = LoopedBlock(self.matrix, normalize=True)
        _, trace = block.forward([1.0, 0.0], 0)
        self.assertEqual(len(trace), 1)


if __name__ == "__main__":
    unittest.main()
