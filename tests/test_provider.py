"""Provider client transport/retry tests without the real httpx dependency."""

from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enrichment.config import Config  # noqa: E402
from enrichment.provider import ProviderClient  # noqa: E402


class FakeResponse:
    def __init__(self, status_code, body, headers=None):
        self.status_code = status_code
        self._body = body
        self.headers = headers or {}

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class ScriptedAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []
        self.closed = False

    async def post(self, path, json):
        self.calls.append((path, json))
        if not self._responses:
            raise AssertionError("unexpected extra provider call")
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self):
        self.closed = True


class TestProviderClient(unittest.TestCase):
    def _run(self, coro):
        return asyncio.run(coro)

    def _config(self, **kwargs):
        return Config(
            batch_size=2,
            max_concurrency=1,
            rate_limit_per_sec=1000.0,
            rate_limit_burst=20,
            backoff_base=0.0,
            backoff_max=0.001,
            backoff_jitter=0.0,
            **kwargs,
        )

    def test_retries_after_429_then_succeeds(self):
        client = ScriptedAsyncClient(
            [
                FakeResponse(429, {}, headers={"Retry-After": "0"}),
                FakeResponse(
                    200,
                    {
                        "status": "ok",
                        "results": [
                            {
                                "domain": "a.com",
                                "status": "ok",
                                "data": {"domain": "a.com", "name": "A"},
                            },
                            {
                                "domain": "b.com",
                                "status": "ok",
                                "data": {"domain": "b.com", "name": "B"},
                            },
                        ],
                    },
                ),
            ]
        )
        provider = ProviderClient(self._config(), client=client)

        results = self._run(provider.enrich_batch(["a.com", "b.com"]))

        self.assertEqual([r.domain for r in results], ["a.com", "b.com"])
        self.assertTrue(all(r.ok for r in results))
        self.assertEqual([r.attempts for r in results], [2, 2])
        self.assertEqual(len(client.calls), 2)

    def test_400_bad_batch_size_is_terminal_not_retried(self):
        client = ScriptedAsyncClient(
            [
                FakeResponse(
                    400,
                    {"status": "error", "code": "BAD_BATCH_SIZE", "message": "too big"},
                )
            ]
        )
        provider = ProviderClient(self._config(), client=client)

        results = self._run(provider.enrich_batch(["a.com", "b.com"]))

        self.assertEqual(len(client.calls), 1)
        self.assertEqual(
            [(r.domain, r.ok, r.error_code, r.attempts) for r in results],
            [
                ("a.com", False, "BAD_BATCH_SIZE", 1),
                ("b.com", False, "BAD_BATCH_SIZE", 1),
            ],
        )

    def test_partial_batch_retries_retryable_and_missing_items_only(self):
        client = ScriptedAsyncClient(
            [
                FakeResponse(
                    200,
                    {
                        "status": "ok",
                        "results": [
                            {
                                "domain": "a.com",
                                "status": "ok",
                                "data": {"domain": "a.com", "name": "A"},
                            },
                            {
                                "domain": "b.com",
                                "status": "error",
                                "code": "TEMPORARY",
                                "message": "try again",
                            },
                        ],
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "status": "ok",
                        "results": [
                            {
                                "domain": "b.com",
                                "status": "ok",
                                "data": {"domain": "b.com", "name": "B"},
                            },
                            {
                                "domain": "c.com",
                                "status": "error",
                                "code": "NO_MATCH",
                                "message": "not found",
                            },
                        ],
                    },
                ),
            ]
        )
        provider = ProviderClient(self._config(max_retries=2), client=client)

        results = self._run(provider.enrich_batch(["a.com", "b.com", "c.com"]))

        self.assertEqual(
            [(r.domain, r.ok, r.error_code, r.attempts) for r in results],
            [
                ("a.com", True, None, 1),
                ("b.com", True, None, 2),
                ("c.com", False, "NO_MATCH", 2),
            ],
        )
        self.assertEqual(
            client.calls,
            [
                ("/v1/enrich/batch", {"domains": ["a.com", "b.com", "c.com"]}),
                ("/v1/enrich/batch", {"domains": ["b.com", "c.com"]}),
            ],
        )

    def test_retries_item_marked_retryable_with_unknown_code(self):
        client = ScriptedAsyncClient(
            [
                FakeResponse(
                    200,
                    {
                        "status": "ok",
                        "results": [
                            {
                                "domain": "a.com",
                                "status": "error",
                                "code": "UPSTREAM_BUSY",
                                "retryable": True,
                            }
                        ],
                    },
                ),
                FakeResponse(
                    200,
                    {
                        "status": "ok",
                        "results": [
                            {
                                "domain": "a.com",
                                "status": "ok",
                                "data": {"domain": "a.com", "name": "A"},
                            }
                        ],
                    },
                ),
            ]
        )
        provider = ProviderClient(self._config(max_retries=1), client=client)

        results = self._run(provider.enrich_batch(["a.com"]))

        self.assertEqual(len(client.calls), 2)
        self.assertTrue(results[0].ok)
        self.assertEqual(results[0].attempts, 2)


if __name__ == "__main__":
    unittest.main()
