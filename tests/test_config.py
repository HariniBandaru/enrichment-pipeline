"""Runtime configuration validation tests."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enrichment.config import Config  # noqa: E402


class TestConfig(unittest.TestCase):
    def test_config_rejects_batch_larger_than_provider_burst(self):
        with self.assertRaises(ValueError):
            Config(batch_size=21)

    def test_config_rejects_non_positive_concurrency(self):
        with self.assertRaises(ValueError):
            Config(max_concurrency=0)

    def test_from_env_reads_rate_limiter_knobs(self):
        env = {
            "ENRICH_RATE_LIMIT": "4.5",
            "ENRICH_RATE_BURST": "12",
            "ENRICH_MAX_RATE_WAITS": "7",
        }
        with mock.patch.dict(os.environ, env, clear=False):
            config = Config.from_env()
        self.assertEqual(config.rate_limit_per_sec, 4.5)
        self.assertEqual(config.rate_limit_burst, 12)
        self.assertEqual(config.max_rate_limit_waits, 7)

    def test_from_env_defaults_when_unset(self):
        for key in ("ENRICH_RATE_LIMIT", "ENRICH_RATE_BURST", "ENRICH_MAX_RATE_WAITS"):
            os.environ.pop(key, None)
        config = Config.from_env()
        self.assertEqual(config.rate_limit_per_sec, Config.rate_limit_per_sec)
        self.assertEqual(config.rate_limit_burst, Config.rate_limit_burst)
        self.assertEqual(config.max_rate_limit_waits, Config.max_rate_limit_waits)


if __name__ == "__main__":
    unittest.main()
