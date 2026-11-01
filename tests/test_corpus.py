# -*- coding: utf-8 -*-
import unittest

from loopforge.world.corpus import make_batch, build_matrix


class TestCorpus(unittest.TestCase):
    def test_batch_size(self):
        self.assertEqual(len(make_batch()), 5)

    def test_matrix_symmetric(self):
        m = build_matrix(6, "t")
        for i in range(6):
            for j in range(6):
                self.assertAlmostEqual(m[i][j], m[j][i])

    def test_diagonal_dominant(self):
        m = build_matrix(6, "t")
        for i in range(6):
            self.assertGreater(m[i][i], 0.0)

    def test_unique_top_eigenvalue(self):
        # diagonal strictly decreases -> unique dominant eigenvalue
        m = build_matrix(6, "t")
        self.assertGreater(m[0][0], m[1][1])
        self.assertGreater(m[1][1], m[5][5])

    def test_init_normalized(self):
        task = make_batch()[0]
        self.assertAlmostEqual(sum(v * v for v in task["init"]) ** 0.5, 1.0)


if __name__ == "__main__":
    unittest.main()
