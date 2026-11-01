# -*- coding: utf-8 -*-
import unittest

from loopforge.utils.yamlish import yaml_loads


class TestYamlish(unittest.TestCase):
    def test_scalars(self):
        d = yaml_loads("name: demo\nversion: 1\nratio: 0.5\nflag: true\nnothing: null\n")
        self.assertEqual(d["name"], "demo")
        self.assertEqual(d["version"], 1)
        self.assertEqual(d["ratio"], 0.5)
        self.assertTrue(d["flag"])
        self.assertIsNone(d["nothing"])

    def test_nested_map(self):
        d = yaml_loads("loop:\n  loops: 8\n  dim: 8\n")
        self.assertEqual(d["loop"]["loops"], 8)
        self.assertEqual(d["loop"]["dim"], 8)

    def test_block_list(self):
        d = yaml_loads("alpha:\n  - 0.5\n  - 0.3\n  - 0.2\n")
        self.assertEqual(d["alpha"], [0.5, 0.3, 0.2])

    def test_block_list_then_map(self):
        d = yaml_loads("mhc:\n  alpha:\n    - 0.5\n    - 0.5\n  epsilon: 0.001\n")
        self.assertEqual(d["mhc"]["alpha"], [0.5, 0.5])
        self.assertEqual(d["mhc"]["epsilon"], 0.001)

    def test_inline_flow(self):
        d = yaml_loads("tags: [a, b, c]\nmap: {x: 1, y: 2}\n")
        self.assertEqual(d["tags"], ["a", "b", "c"])
        self.assertEqual(d["map"], {"x": 1, "y": 2})

    def test_comments_skipped(self):
        d = yaml_loads("# hi\nkey: value\n")
        self.assertEqual(d["key"], "value")


if __name__ == "__main__":
    unittest.main()
