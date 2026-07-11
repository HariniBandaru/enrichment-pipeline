"""Async client for the (mock) enrichment provider.

Responsibilities that are genuinely load-bearing here:

- Always pin ``X-Provider-Version: 2`` (else we silently get the v1 shape).
- Use the batch endpoint (<=25 domains) to cut round-trips.
- Bound in-flight requests with a semaphore (no per-domain fan-out / thundering
  herd at 100k scale).
- Retry *retryable* failures only, with exponential backoff + jitter, and honour
  ``Retry-After`` on 429. Retryable = HTTP 429/5xx, network/timeout errors, and
  per-domain error bodies flagged ``retryable`` (TEMPORARY / RATE_LIMITED).
- Never trust the HTTP status alone: a 200 batch can contain per-domain errors,
  including NO_MATCH (a terminal, non-retryable outcome).

The client returns *raw* per-domain outcomes; normalisation and terminal
classification happen upstream so this layer stays about transport + protocol.
"""

from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import httpx

from .config import Config


class AsyncTokenBucket:
    """A simple async token-bucket rate limiter (client-side pacing).

    Mirrors the provider's own bucket so we ride just under the limit instead of
    hammering it and bouncing off 429s. ``acquire(n)`` blocks until ``n`` tokens
    are available; tokens refill continuously at ``rate`` per second.
    """

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, n: int) -> None:
        # A single acquire can never exceed capacity, or it would wait forever.
        n = min(n, self.capacity)
        while True:
            async with self._lock:
                now = time.monotonic()
                self._tokens = min(
                    self.capacity, self._tokens + (now - self._updated) * self.rate
                )
                self._updated = now
                if self._tokens >= n:
                    self._tokens -= n
                    return
                wait = (n - self._tokens) / self.rate
            await asyncio.sleep(wait)

# Per-domain error codes that are worth retrying.
_RETRYABLE_CODES = {"TEMPORARY", "RATE_LIMITED"}


@dataclass
class RawResult:
    """Raw per-domain outcome from the provider (pre-normalisation)."""

    domain: str
    ok: bool
    data: Optional[dict[str, Any]] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    attempts: int = 0
    retryable: bool = False


class _RetryableHTTP(Exception):
    """Signals a whole-batch retryable condition, carrying an optional delay.

    ``is_rate_limit`` distinguishes a 429 (throttling — doesn't count against the
    error budget) from a genuine transient error (5xx / network — does).
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        is_rate_limit: bool = False,
    ):
        super().__init__(message)
        self.retry_after = retry_after
        self.is_rate_limit = is_rate_limit


class ProviderClient:
    def __init__(self, config: Config, client: Optional[httpx.AsyncClient] = None):
        self.config = config
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=config.base_url,
            timeout=config.request_timeout,
            headers={
                "Authorization": f"Bearer {config.token}",
                "X-Provider-Version": config.provider_version,
                "Content-Type": "application/json",
            },
            limits=httpx.Limits(max_connections=config.max_concurrency),
        )
        self._sem = asyncio.Semaphore(config.max_concurrency)
        self._bucket = AsyncTokenBucket(
            rate=config.rate_limit_per_sec, capacity=config.rate_limit_burst
        )

    async def __aenter__(self) -> "ProviderClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _backoff(self, attempt: int, retry_after: Optional[float]) -> float:
        if retry_after is not None:
            return min(retry_after, self.config.backoff_max)
        delay = min(
            self.config.backoff_base * (2 ** attempt), self.config.backoff_max
        )
        jitter = delay * self.config.backoff_jitter
        return max(0.0, delay + random.uniform(-jitter, jitter))

    async def _post_batch_once(self, domains: list[str]) -> dict[str, RawResult]:
        """One batch call. Raises _RetryableHTTP for whole-batch retryables."""

        try:
            resp = await self._client.post(
                "/v1/enrich/batch", json={"domains": domains}
            )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            raise _RetryableHTTP(f"network error: {exc!r}") from exc

        if resp.status_code == 429:
            retry_after = _parse_retry_after(resp.headers.get("Retry-After"))
            raise _RetryableHTTP("429 rate limited", retry_after, is_rate_limit=True)
        if resp.status_code >= 500:
            raise _RetryableHTTP(f"server error {resp.status_code}")
        if resp.status_code == 401:
            # Auth is a config problem, not a transient one: fail every domain
            # terminally rather than burning the retry budget.
            return _all_error(domains, "UNAUTHORIZED", "provider returned 401")
        if resp.status_code == 400:
            return _parse_client_error(domains, resp)
        if resp.status_code >= 400:
            raise _RetryableHTTP(f"unexpected status {resp.status_code}")

        try:
            body = resp.json()
        except ValueError as exc:
            raise _RetryableHTTP(f"invalid JSON body: {exc!r}") from exc

        return _parse_batch_body(domains, body)

    async def enrich_batch(self, domains: list[str]) -> list[RawResult]:
        """Enrich a chunk (<= batch_size). Retries retryable domains in place.

        Two independent budgets:
        - ``error_attempts`` for genuine transient errors (5xx / TEMPORARY /
          network) — bounded by ``max_retries``.
        - ``rate_limit_waits`` for 429s — bounded by ``max_rate_limit_waits``.
        A throttle isn't a failure, so it must not burn the error budget; that
        separation is what lets large runs ride the limit instead of dying on it.

        Returns one RawResult per input domain (order not guaranteed).
        """

        pending = list(domains)
        results: dict[str, RawResult] = {}
        rounds: dict[str, int] = {d: 0 for d in domains}
        error_attempts = 0
        rate_limit_waits = 0

        while pending:
            for d in pending:
                rounds[d] += 1

            # Client-side pacing: take a token per domain *before* sending, so we
            # stay under the provider's bucket instead of relying on 429 bounce.
            await self._bucket.acquire(len(pending))

            async with self._sem:
                try:
                    round_results = await self._post_batch_once(pending)
                    batch_error: Optional[_RetryableHTTP] = None
                except _RetryableHTTP as exc:
                    round_results = {}
                    batch_error = exc

            if batch_error is not None:
                if batch_error.is_rate_limit:
                    rate_limit_waits += 1
                    if rate_limit_waits > self.config.max_rate_limit_waits:
                        self._finalize_exhausted(
                            results, pending, rounds,
                            "RATE_LIMITED", "throttled past max_rate_limit_waits",
                        )
                        break
                    await asyncio.sleep(self._backoff(0, batch_error.retry_after))
                    continue  # NOT counted against the error budget
                # genuine transient (5xx / network / timeout)
                if error_attempts >= self.config.max_retries:
                    self._finalize_exhausted(
                        results, pending, rounds, "EXHAUSTED", str(batch_error)
                    )
                    break
                await asyncio.sleep(self._backoff(error_attempts, None))
                error_attempts += 1
                continue

            next_pending: list[str] = []
            for d in pending:
                r = round_results.get(d)
                if r is None:
                    r = RawResult(d, ok=False, error_code="MISSING_FROM_RESPONSE",
                                  error_detail="domain absent from batch results")
                r.attempts = rounds[d]
                if not r.ok and (
                    r.retryable
                    or r.error_code in _RETRYABLE_CODES
                    or r.error_code == "MISSING_FROM_RESPONSE"
                ):
                    next_pending.append(d)
                else:
                    results[d] = r

            pending = next_pending
            if pending:
                if error_attempts >= self.config.max_retries:
                    self._finalize_exhausted(
                        results, pending, rounds, "EXHAUSTED",
                        "retryable error persisted past max_retries",
                    )
                    break
                await asyncio.sleep(self._backoff(error_attempts, None))
                error_attempts += 1

        # Preserve the one-result-per-requested-domain contract even if a future
        # retry-path change accidentally fails to finalize one of the domains.
        return [
            results.get(
                d,
                RawResult(
                    d,
                    ok=False,
                    error_code="INTERNAL_MISSING_RESULT",
                    error_detail="provider client produced no terminal result",
                    attempts=rounds.get(d, 0),
                ),
            )
            for d in domains
        ]

    @staticmethod
    def _finalize_exhausted(results, pending, rounds, code, detail) -> None:
        for d in pending:
            results[d] = RawResult(
                d, ok=False, error_code=code, error_detail=detail,
                attempts=rounds[d],
            )


def _all_error(domains: list[str], code: str, detail: str) -> dict[str, RawResult]:
    return {
        d: RawResult(d, ok=False, error_code=code, error_detail=detail)
        for d in domains
    }


def _parse_client_error(
    domains: list[str], resp: httpx.Response
) -> dict[str, RawResult]:
    """Treat a 400 as terminal caller/config error, not a transient outage."""

    try:
        body = resp.json()
    except ValueError:
        return _all_error(domains, "BAD_REQUEST", "provider returned 400")

    if isinstance(body, dict):
        code = str(body.get("code") or "BAD_REQUEST")
        detail = str(body.get("message") or "provider returned 400")
        if code in _RETRYABLE_CODES or body.get("retryable") is True:
            raise _RetryableHTTP(detail)
        return _all_error(domains, code, detail)

    return _all_error(domains, "BAD_REQUEST", "provider returned 400")


def _parse_retry_after(value: Optional[str]) -> Optional[float]:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _parse_batch_body(
    domains: list[str], body: dict[str, Any]
) -> dict[str, RawResult]:
    """Map a v2 batch body into per-domain RawResults.

    We key by the domain *we requested* and match provider items back to it, so a
    mismatched echo can't drop a record.
    """

    if not isinstance(body, dict):
        return {
            d: RawResult(d, ok=False, error_code="BAD_BODY",
                         error_detail="batch body was not an object")
            for d in domains
        }

    if body.get("status") == "error":
        # Whole-batch application error surfaced with HTTP 200.
        code = str(body.get("code") or "BATCH_ERROR")
        return {
            d: RawResult(d, ok=False, error_code=code,
                         error_detail="batch-level error status")
            for d in domains
        }

    items = body.get("results") or []
    by_domain: dict[str, RawResult] = {}
    remaining = list(domains)

    for item in items:
        if not isinstance(item, dict):
            continue
        dom = item.get("domain")
        # Match to a requested domain (case-insensitively) so we can attribute it.
        target = _match_domain(dom, remaining) or dom
        if target in remaining:
            remaining.remove(target)
        by_domain[target] = _item_to_result(target, item)

    for d in remaining:
        by_domain[d] = RawResult(
            d, ok=False, error_code="MISSING_FROM_RESPONSE",
            error_detail="domain absent from batch results",
        )
    return by_domain


def _match_domain(dom: Any, candidates: list[str]) -> Optional[str]:
    if not isinstance(dom, str):
        return None
    low = dom.strip().lower()
    for c in candidates:
        if c.lower() == low:
            return c
    return None


def _item_to_result(domain: str, item: dict[str, Any]) -> RawResult:
    status = item.get("status")
    if status == "ok":
        data = item.get("data")
        if isinstance(data, dict):
            return RawResult(domain, ok=True, data=data)
        return RawResult(domain, ok=False, error_code="BAD_DATA",
                         error_detail="ok status but missing data object")
    code = str(item.get("code") or "UNKNOWN")
    return RawResult(domain, ok=False, error_code=code,
                     error_detail=item.get("message"),
                     retryable=item.get("retryable") is True)
