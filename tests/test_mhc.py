# -*- coding: utf-8 -*-
import unittest

from loopforge.core.mhc import HyperConnections, sigmoid
from loopforge.utils.linalg import norm2


class TestMhc(unittest.TestCase):
    def test_sigmoid_midpoint(self):
        self.assertAlmostEqual(sigmoid(0.0), 0.5)

    def test_constrain_unit_sum(self):
        hc = HyperConnections()
        alpha = hc._constrain([0.5, 0.3, 0.2])
        self.assertAlmostEqual(sum(alpha), 1.0)

    def test_constrain_bounds(self):
        hc = HyperConnections(epsilon=1e-3)
        alpha = hc._constrain([1.0, 0.0, 0.0])
        self.assertAlmostEqual(sum(alpha), 1.0)
        for a in alpha:
            self.assertGreater(a, 0.0)   # no stream is fully killed
            self.assertLessEqual(a, 1.0)  # no stream fully dominates

    def test_apply_stable_norm(self):
        hc = HyperConnections()
        matrix = [[0.9, 0.0], [0.0, 0.5]]
        h = [1.0, 0.0]
        for _ in range(20):
            h = hc.apply(h, matrix)
        self.assertAlmostEqual(norm2(h), 1.0)

    def test_forward_stable(self):
        hc = HyperConnections()
        matrix = [[0.9, 0.0], [0.0, 0.5]]
        h, trace = hc.forward([1.0, 0.0], matrix, 16)
        self.assertAlmostEqual(norm2(h), 1.0)
        self.assertEqual(len(trace), 17)


if __name__ == "__main__":
    unittest.main()
