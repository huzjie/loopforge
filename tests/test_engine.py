# -*- coding: utf-8 -*-
import unittest

from loopforge.core.engine import Engine
from loopforge.config import load_config
from loopforge.world.oracle import make_batch


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = Engine(load_config())

    def test_run_all(self):
        result = self.engine.run_all()
        self.assertEqual(result["summary"]["tasks"], 5)
        self.assertIn("mean_convergence", result["summary"])

    def test_run_task_shape(self):
        task = make_batch()[0]
        r = self.engine.run_task(task)
        self.assertIn("convergence", r)
        self.assertIn("confidence", r)
        self.assertIn("solved", r)

    def test_expert_flags_solved(self):
        cfg = load_config()
        cfg["model"]["skill"] = 1.0
        engine = Engine(cfg)
        result = engine.run_all()
        self.assertEqual(result["summary"]["flagged"], 5)

    def test_stability_report(self):
        sr = self.engine.stability_report(32)
        self.assertEqual(sr["mhc_healthy"], 5)
        self.assertLessEqual(sr["naive_healthy"], 5)
        self.assertEqual(sr["collapse_gap"], sr["mhc_healthy"] - sr["naive_healthy"])

    def test_render_markdown(self):
        result = self.engine.run_all()
        md = self.engine.render_report(result)
        self.assertIn("Loopforge", md)


if __name__ == "__main__":
    unittest.main()
