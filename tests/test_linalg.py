# -*- coding: utf-8 -*-
import unittest

from loopforge.utils.linalg import (dot, matvec, norm2, normalize, cos, clamp,
                                    add, scale, weighted_sum, transpose)


class TestLinalg(unittest.TestCase):
    def test_dot(self):
        self.assertEqual(dot([1, 2, 3], [4, 5, 6]), 32)

    def test_matvec(self):
        self.assertEqual(matvec([[1, 0], [0, 1]], [3, 4]), [3, 4])

    def test_norm2(self):
        self.assertAlmostEqual(norm2([3, 4]), 5.0)

    def test_normalize_unit(self):
        v = normalize([3.0, 4.0])
        self.assertAlmostEqual(norm2(v), 1.0)

    def test_cos_same(self):
        self.assertAlmostEqual(cos([1, 0], [1, 0]), 1.0)

    def test_cos_orthogonal(self):
        self.assertAlmostEqual(cos([1, 0], [0, 1]), 0.0)

    def test_clamp(self):
        self.assertEqual(clamp(5.0), 1.0)
        self.assertEqual(clamp(-5.0), 0.0)
        self.assertEqual(clamp(0.5), 0.5)

    def test_weighted_sum(self):
        self.assertEqual(weighted_sum([[1, 2], [3, 4]], [0.5, 0.5]), [2.0, 3.0])

    def test_transpose(self):
        self.assertEqual(transpose([[1, 2], [3, 4]]), [[1, 3], [2, 4]])


if __name__ == "__main__":
    unittest.main()
