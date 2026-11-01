# -*- coding: utf-8 -*-
import unittest

from loopforge.world.oracle import ground_truth, convergence_of, signal_for, make_batch


class TestOracle(unittest.TestCase):
    def test_ground_truth_unit(self):
        v = ground_truth(make_batch()[0])
        self.assertAlmostEqual(sum(x * x for x in v) ** 0.5, 1.0, places=3)

    def test_ground_truth_cached(self):
        t = make_batch()[0]
        self.assertIs(ground_truth(t), ground_truth(t))

    def test_convergence_monotonic(self):
        t = make_batch()[0]
        lo = convergence_of(t, 1)
        hi = convergence_of(t, 32)
        self.assertGreater(hi, lo)

    def test_convergence_reaches_one(self):
        t = make_batch()[0]
        self.assertAlmostEqual(convergence_of(t, 200), 1.0, places=3)

    def test_signal_in_range(self):
        t = make_batch()[0]
        self.assertTrue(0.0 <= signal_for(t, 8) <= 1.0)


if __name__ == "__main__":
    unittest.main()
