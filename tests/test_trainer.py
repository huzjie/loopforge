# -*- coding: utf-8 -*-
import unittest

from loopforge.training.trainer import SkillTrainer


class TestTrainer(unittest.TestCase):
    def test_skill_increases(self):
        t = SkillTrainer(0.5, 0.05)
        step = t.train_step()
        self.assertGreater(step["skill"], 0.5)

    def test_solve_rate_improves(self):
        t = SkillTrainer(0.5, 0.5)
        step = t.train_step()
        self.assertGreaterEqual(step["solved_rate_after"], step["solved_rate_before"])


if __name__ == "__main__":
    unittest.main()
