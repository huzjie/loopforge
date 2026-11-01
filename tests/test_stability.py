# -*- coding: utf-8 -*-
import unittest

from loopforge.core.stability import (activation_norm, collapsed, healthy,
                                      growth, spectral_radius)


class TestStability(unittest.TestCase):
    def test_activation_norm(self):
        self.assertAlmostEqual(activation_norm([3, 4]), 5.0)

    def test_collapsed_constant(self):
        self.assertTrue(collapsed([1.0, 1.0, 1.0]))

    def test_not_collapsed(self):
        self.assertFalse(collapsed([1.0, 2.0, 3.0]))

    def test_healthy_in_band(self):
        self.assertTrue(healthy([1.0, 0.0]))

    def test_unhealthy_drift(self):
        self.assertFalse(healthy([0.01, 0.02], lo=0.5, hi=2.0))
        self.assertFalse(healthy([100.0, 100.0], lo=0.5, hi=2.0))

    def test_growth(self):
        self.assertEqual(growth([1.0, 2.0, 4.0]), [2.0, 2.0])

    def test_spectral_radius_identity(self):
        self.assertAlmostEqual(spectral_radius([[1, 0], [0, 1]]), 1.0, places=3)


if __name__ == "__main__":
    unittest.main()
