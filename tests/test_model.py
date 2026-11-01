# -*- coding: utf-8 -*-
import unittest

from loopforge.core.model import Model


class TestModel(unittest.TestCase):
    def test_boost_zero_at_random(self):
        self.assertAlmostEqual(Model(0.5).boost(), 0.0)

    def test_boost_sign(self):
        self.assertAlmostEqual(Model(1.0).boost(), 1.0)
        self.assertAlmostEqual(Model(0.0).boost(), -1.0)

    def test_confidence_expert_equals_signal(self):
        self.assertAlmostEqual(Model(1.0).confidence(0.9, 0.2), 0.9)
        self.assertAlmostEqual(Model(1.0).confidence(0.1, 0.2), 0.1)

    def test_confidence_random_equals_noise(self):
        self.assertAlmostEqual(Model(0.5).confidence(0.9, 0.3), 0.3)

    def test_flag_expert(self):
        m = Model(1.0)
        self.assertTrue(m.flag(0.9, 0.5))
        self.assertFalse(m.flag(0.1, 0.5))

    def test_clamped(self):
        self.assertEqual(Model(2.0).skill, 1.0)
        self.assertEqual(Model(-1.0).skill, 0.0)


if __name__ == "__main__":
    unittest.main()
