# -*- coding: utf-8 -*-
import unittest

from loopforge.utils.rng import (stable_float, stable_int, stable_vector,
                                 stable_choice, stable_matrix, stable_shuffle)


class TestRng(unittest.TestCase):
    def test_deterministic(self):
        self.assertEqual(stable_float("a"), stable_float("a"))
        self.assertEqual(stable_int("b", 0, 100), stable_int("b", 0, 100))

    def test_range(self):
        for i in range(50):
            v = stable_float("k%d" % i)
            self.assertTrue(0.0 <= v <= 1.0)

    def test_vector_length(self):
        self.assertEqual(len(stable_vector("v", 8)), 8)

    def test_choice(self):
        self.assertIn(stable_choice("c", [1, 2, 3]), [1, 2, 3])

    def test_matrix_shape(self):
        m = stable_matrix("m", 3, 4)
        self.assertEqual(len(m), 3)
        self.assertEqual(len(m[0]), 4)

    def test_shuffle_no_mutate(self):
        items = [1, 2, 3, 4]
        out = stable_shuffle("s", items)
        self.assertEqual(sorted(items), sorted(out))
        self.assertEqual(items, [1, 2, 3, 4])


if __name__ == "__main__":
    unittest.main()
