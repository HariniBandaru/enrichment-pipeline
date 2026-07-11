"""CLI behavior tests."""

from __future__ import annotations

import sys
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from enrichment.cli import _build_config, main
    from enrichment.summary import RunSummary
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific
    if exc.name == "httpx":
        _build_config = main = RunSummary = None
    else:  # pragma: no cover
        raise


@unittest.skipIf(main is None, "httpx not installed")
class TestCli(unittest.TestCase):
    def _temp_input(self) -> str:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        tmp.write("domain\nstripe.com\n")
        tmp.close()
        return tmp.name

    def test_all_no_match_run_exits_zero(self):
        input_path = self._temp_input()
        summary = RunSummary(total_input_rows=1, unique_domains=1, no_match=1)

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "results.jsonl")
            summary_path = str(Path(tmpdir) / "summary.json")
            with patch("enrichment.cli._run_pipeline", new=AsyncMock(return_value=summary)):
                exit_code = main(
                    [
                        "--input",
                        input_path,
                        "--output",
                        output_path,
                        "--summary",
                        summary_path,
                    ]
                )

        self.assertEqual(exit_code, 0)

    def test_invalid_batch_size_exits_with_usage_error(self):
        input_path = self._temp_input()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = str(Path(tmpdir) / "results.jsonl")
            summary_path = str(Path(tmpdir) / "summary.json")
            exit_code = main(
                [
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                    "--summary",
                    summary_path,
                    "--batch-size",
                    "0",
                ]
            )

        self.assertEqual(exit_code, 2)


class TestCliConfig(unittest.TestCase):
    def test_build_config_preserves_rate_limit_env_settings(self):
        args = Namespace(
            provider_url=None,
            token=None,
            batch_size=None,
            concurrency=None,
            max_retries=None,
        )

        with patch.dict(
            "os.environ",
            {
                "ENRICH_RATE_LIMIT": "7.5",
                "ENRICH_RATE_BURST": "12",
                "ENRICH_MAX_RATE_WAITS": "9",
            },
            clear=True,
        ):
            config = _build_config(args)

        self.assertEqual(config.rate_limit_per_sec, 7.5)
        self.assertEqual(config.rate_limit_burst, 12)
        self.assertEqual(config.max_rate_limit_waits, 9)

    def test_build_config_preserves_explicit_zero_for_validation(self):
        args = Namespace(
            provider_url=None,
            token=None,
            batch_size=0,
            concurrency=0,
            max_retries=None,
        )

        with self.assertRaises(ValueError):
            _build_config(args)


if __name__ == "__main__":
    unittest.main()
