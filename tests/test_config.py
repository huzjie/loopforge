# -*- coding: utf-8 -*-
import unittest

from loopforge.config import load_config, DEFAULTS


class TestConfig(unittest.TestCase):
    def test_defaults(self):
        cfg = load_config()
        self.assertEqual(cfg["loop"]["loops"], 8)
        self.assertTrue(cfg["mhc"]["enabled"])

    def test_merge_recipe(self):
        import tempfile, os
        with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
            f.write("loop:\n  loops: 32\n")
            path = f.name
        try:
            cfg = load_config(path)
            self.assertEqual(cfg["loop"]["loops"], 32)
            self.assertEqual(cfg["loop"]["dim"], DEFAULTS["loop"]["dim"])
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
