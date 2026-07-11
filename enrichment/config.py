"""Runtime configuration for the enrichment pipeline.

Everything tunable lives here so the behaviour that matters at scale
(concurrency, batch size, retry budget, timeouts) is explicit and in one place.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    base_url: str = "http://localhost:4000"
    token: str = "demo-token-abc123"
    # The provider serves a deprecated v1 shape unless we pin version 2.
    provider_version: str = "2"

    # Concurrency + batching. Each domain counts against the rate limit whether
    # sent singly or in a batch, so batching only saves round-trips. We keep the
    # number of *in-flight requests* bounded rather than fanning out per-domain.
    #
    # IMPORTANT (discovered by probing, not documented): the provider's rate
    # limiter is a token bucket of capacity ~20, refilling ~10 tokens/sec, and a
    # batch consumes one token per domain *atomically*. A 25-domain batch (the
    # documented max) therefore ALWAYS 429s — it can never gather 25 tokens. So
    # the usable batch size is <= 20.
    #
    # We proactively pace requests with a *client-side* token bucket mirroring
    # the provider's (see rate_limit_* below), so at 100k scale we ride just
    # under the limit instead of bouncing off 429s. Concurrency can therefore be
    # a bit higher — the limiter, not the semaphore, is the real governor.
    batch_size: int = 10
    max_concurrency: int = 4

    # Client-side rate limiter, tuned to the observed provider bucket. We stay a
    # hair under (9.0/s vs the provider's ~10/s) so clock/latency drift doesn't
    # push us over. This is what keeps a 100k run near the ceiling with a high
    # success rate rather than exhausting retries on 429s.
    rate_limit_per_sec: float = 9.0
    rate_limit_burst: int = 20

    # Per-request network timeout (seconds). A small fraction of provider calls
    # are deliberately slow; we cap them rather than let them stall a worker.
    request_timeout: float = 10.0

    # Retry budget for *genuine* transient errors (5xx / TEMPORARY / timeouts).
    # Rate-limit (429) waits do NOT count against this budget — being throttled
    # isn't a failure, it just means "wait your turn". They're bounded separately
    # by max_rate_limit_waits so a sustained throttle still can't loop forever.
    max_retries: int = 5
    max_rate_limit_waits: int = 25
    backoff_base: float = 0.5  # seconds; exponential: base * 2**attempt
    backoff_max: float = 30.0
    backoff_jitter: float = 0.3  # +/- fraction of the computed delay

    def __post_init__(self) -> None:
        if self.batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        if self.batch_size > 25:
            raise ValueError("batch_size must be <= 25 (provider API limit)")
        if self.batch_size > self.rate_limit_burst:
            raise ValueError(
                "batch_size must be <= rate_limit_burst; larger batches cannot "
                "fit in the provider token bucket"
            )
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if self.rate_limit_per_sec <= 0:
            raise ValueError("rate_limit_per_sec must be > 0")
        if self.rate_limit_burst < 1:
            raise ValueError("rate_limit_burst must be >= 1")
        if self.request_timeout <= 0:
            raise ValueError("request_timeout must be > 0")
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.max_rate_limit_waits < 0:
            raise ValueError("max_rate_limit_waits must be >= 0")
        if self.backoff_base < 0:
            raise ValueError("backoff_base must be >= 0")
        if self.backoff_max <= 0:
            raise ValueError("backoff_max must be > 0")
        if not 0 <= self.backoff_jitter <= 1:
            raise ValueError("backoff_jitter must be between 0 and 1")

    @classmethod
    def from_env(cls) -> "Config":
        """Build config from environment, falling back to defaults."""

        def _get(name: str, default):
            return os.environ.get(name, default)

        return cls(
            base_url=_get("PROVIDER_URL", cls.base_url),
            token=_get("PROVIDER_TOKEN", cls.token),
            provider_version=_get("PROVIDER_VERSION", cls.provider_version),
            batch_size=int(_get("ENRICH_BATCH_SIZE", cls.batch_size)),
            max_concurrency=int(_get("ENRICH_CONCURRENCY", cls.max_concurrency)),
            request_timeout=float(_get("ENRICH_TIMEOUT", cls.request_timeout)),
            max_retries=int(_get("ENRICH_MAX_RETRIES", cls.max_retries)),
            # Rate-limiter pacing knobs. Exposed so an operator can retune to a
            # different provider bucket at scale without editing source.
            rate_limit_per_sec=float(
                _get("ENRICH_RATE_LIMIT", cls.rate_limit_per_sec)
            ),
            rate_limit_burst=int(_get("ENRICH_RATE_BURST", cls.rate_limit_burst)),
            max_rate_limit_waits=int(
                _get("ENRICH_MAX_RATE_WAITS", cls.max_rate_limit_waits)
            ),
        )
