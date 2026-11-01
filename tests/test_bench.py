# -*- coding: utf-8 -*-
import unittest

from loopforge import bench


class TestBench(unittest.TestCase):
    def test_skill_monotonic(self):
        rows = bench.run_all()
        self.assertGreater(rows[-1]["solve_recall"], rows[0]["solve_recall"])
        self.assertGreater(rows[-1]["solve_accuracy"], rows[0]["solve_accuracy"])
        self.assertLessEqual(rows[-1]["false_flag_rate"], rows[0]["false_flag_rate"])

    def test_loop_curve_monotonic(self):
        curve = bench.run_loop_curve()
        self.assertGreater(curve[-1]["mean_convergence"], curve[0]["mean_convergence"])
        self.assertGreater(curve[-1]["param_efficiency"], curve[0]["param_efficiency"])
        self.assertGreaterEqual(curve[-1]["collapse_gap"], curve[0]["collapse_gap"])

    def test_expert_recall_one(self):
        rows = bench.run_all([1.0])
        self.assertAlmostEqual(rows[0]["solve_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
