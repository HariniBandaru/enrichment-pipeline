"""Pipeline orchestration tests.

These cover the most important scale property in the repo: the pipeline should
only keep a bounded number of batch tasks in flight, rather than creating one
task per chunk for the entire input.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    from enrichment.models import Result  # noqa: E402
    from enrichment.pipeline import run_pipeline  # noqa: E402
    from enrichment.provider import RawResult  # noqa: E402
except ModuleNotFoundError as exc:  # pragma: no cover - environment-specific
    if exc.name == "httpx":
        Result = RawResult = run_pipeline = None
    else:  # pragma: no cover
        raise

from enrichment.config import Config  # noqa: E402


@unittest.skipIf(run_pipeline is None, "httpx not installed")
class TestPipeline(unittest.TestCase):
    def _write(self, text: str) -> str:
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix=".csv", delete=False, encoding="utf-8"
        )
        tmp.write(text)
        tmp.close()
        return tmp.name

    def test_pipeline_bounds_in_flight_chunk_tasks(self):
        import enrichment.pipeline as pipeline_module

        path = self._write("domain\na.com\nb.com\nc.com\nd.com\ne.com\n")
        seen_results: list[Result] = []
        active = 0
        peak_active = 0

        class FakeProviderClient:
            def __init__(self, config):
                self.config = config

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def enrich_batch(self, domains):
                nonlocal active, peak_active
                active += 1
                peak_active = max(peak_active, active)
                await asyncio.sleep(0.01)
                active -= 1
                return [
                    RawResult(
                        domain=domain,
                        ok=True,
                        data={"domain": domain, "name": domain.upper()},
                        attempts=1,
                    )
                    for domain in domains
                ]

        original_client = pipeline_module.ProviderClient
        pipeline_module.ProviderClient = FakeProviderClient
        try:
            summary = asyncio.run(
                run_pipeline(
                    path,
                    seen_results.append,
                    Config(batch_size=1, max_concurrency=2),
                )
            )
        finally:
            pipeline_module.ProviderClient = original_client

        self.assertEqual(peak_active, 2)
        self.assertEqual(summary.succeeded, 5)
        self.assertEqual(len(seen_results), 5)

    def test_auth_failure_stops_scheduling_and_reports_remaining_domains(self):
        import enrichment.pipeline as pipeline_module

        path = self._write("domain\na.com\nb.com\nc.com\nd.com\ne.com\n")
        seen_results: list[Result] = []
        calls: list[list[str]] = []

        class UnauthorizedProviderClient:
            def __init__(self, config):
                self.config = config

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def enrich_batch(self, domains):
                calls.append(domains)
                return [
                    RawResult(
                        domain=domain,
                        ok=False,
                        error_code="UNAUTHORIZED",
                        error_detail="provider returned 401",
                        attempts=1,
                    )
                    for domain in domains
                ]

        original_client = pipeline_module.ProviderClient
        pipeline_module.ProviderClient = UnauthorizedProviderClient
        try:
            summary = asyncio.run(
                run_pipeline(
                    path,
                    seen_results.append,
                    Config(batch_size=1, max_concurrency=1),
                )
            )
        finally:
            pipeline_module.ProviderClient = original_client

        self.assertEqual(calls, [["a.com"]])
        self.assertEqual(summary.failed, 5)
        self.assertEqual(len(seen_results), 5)
        self.assertTrue(
            all(result.error_code == "UNAUTHORIZED" for result in seen_results)
        )



if __name__ == "__main__":
    unittest.main()
