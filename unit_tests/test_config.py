from pathlib import Path
import unittest

from retrieval_arena.config import load_config, parse_config
from retrieval_arena.errors import ValidationError


class ConfigTests(unittest.TestCase):
    def test_load_example_config_resolves_paths(self):
        config = load_config("configs/experiment.example.yaml")
        self.assertEqual(config.experiment_name, "toy_faq_reconstruction")
        self.assertTrue(config.datasets[0].path.exists())
        self.assertTrue(config.tests[0].build_context.exists())


    def test_parse_config_rejects_duplicate_tests(self):
        raw = {
            "experiment_name": "x",
            "datasets": [{"name": "d", "path": "."}],
            "tests": [{"name": "t", "image": "i"}, {"name": "t", "image": "i2"}],
        }
        with self.assertRaisesRegex(ValidationError, "Duplicate test"):
            parse_config(raw, Path("config.yaml"))


    def test_parse_config_rejects_bad_threshold(self):
        raw = {"experiment_name": "x", "datasets": [{"name": "d", "path": "."}], "tests": [{"name": "t", "image": "i"}], "scoring": {"match_threshold": 2}}
        with self.assertRaisesRegex(ValidationError, "match_threshold"):
            parse_config(raw, Path("config.yaml"))


if __name__ == "__main__":
    unittest.main()