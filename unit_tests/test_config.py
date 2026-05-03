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


    def test_loads_cikm_configs_with_oracle_graph_support(self):
        for path in sorted(Path("configs").glob("cikm_pilot_*.yaml")):
            config = load_config(path)
            tests = {test.name: test for test in config.tests}
            self.assertIn("oracle_graph_support", tests)
            self.assertEqual(tests["oracle_graph_support"].config["experiment_id"], "E005")
            self.assertTrue(tests["oracle_graph_support"].build_context.exists())
            self.assertIn("rag_graph_multihop_rerank", tests)
            self.assertEqual(tests["rag_graph_multihop_rerank"].config["experiment_id"], "E006")
            self.assertTrue(tests["rag_graph_multihop_rerank"].build_context.exists())

    def test_parse_config_loads_read_only_test_volumes(self):
        raw = {
            "experiment_name": "x",
            "datasets": [{"name": "d", "path": "."}],
            "tests": [
                {
                    "name": "rag_embedding_topk",
                    "image": "i",
                    "volumes": [
                        {
                            "host_path": ".",
                            "container_path": "/models/minilm",
                            "read_only": True,
                        }
                    ],
                }
            ],
        }
        config = parse_config(raw, Path("config.yaml"))

        self.assertEqual(len(config.tests[0].volumes), 1)
        self.assertEqual(config.tests[0].volumes[0].container_path, "/models/minilm")
        self.assertTrue(config.tests[0].volumes[0].read_only)

    def test_parse_config_loads_network_disabled_flag(self):
        raw = {
            "experiment_name": "x",
            "datasets": [{"name": "d", "path": "."}],
            "tests": [{"name": "t", "image": "i", "network_disabled": True}],
        }
        config = parse_config(raw, Path("config.yaml"))

        self.assertTrue(config.tests[0].network_disabled)


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
