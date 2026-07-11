# Decisions & Trade-offs

Short and real. What I chose, why, what I assumed, and what I'd do next.

## Key decisions

**Python + async `httpx`, single dependency.** The problem is I/O-bound (waiting
on the provider), so async with bounded concurrency fits well and keeps the code
small. I kept dependencies to one (`httpx`) and wrote the tests against the
standard library so the whole thing runs with almost no setup.

**Batch endpoint, but sized to the *rate limiter*, not the docs.** The docs allow
25 domains/batch. Probing the provider showed the token bucket has capacity ~20
and refills ~10/sec, and a batch consumes one token per domain atomically — so a
25-domain batch *always* 429s and can never recover (it can't accumulate 25
tokens). I defaulted to `batch_size=10`, `concurrency=4`, with a separate
client-side limiter keeping aggregate demand under the provider bucket, which
paces cleanly with the refill and keeps 429 churn near zero.
This single change took the sample run from 28% → 92% success.

**Client-side rate limiter — pace under the limit, don't bounce off it.** The
provider bucket is ~20 capacity / ~10 tokens per second. Rather than fire fast
and react to 429s, the client has its own token bucket (`AsyncTokenBucket`,
tuned to 9/s, burst 20) and takes one token per domain *before* each request.
This was not a nice-to-have: without it, a 600-domain run collapsed to **9.2%
success (543 of 600 exhausted on 429)** because batches kept losing the race for
tokens and burned their retry budget. With it, the same run is **96.5% success,
zero failures**, at ~8 domains/s (near the provider's hard ceiling) with flat
~36 MB memory. See the "scale" section below.

**Retry only what's retryable, and don't let throttling burn the error budget.**
5xx, network errors, timeouts, and per-domain `TEMPORARY` are retried
(exponential backoff + jitter). 429s are handled on a *separate* budget
(`max_rate_limit_waits`), honouring `Retry-After` — a throttle means "wait your
turn", not "you failed", so it must not consume the finite transient-error
budget. Terminal outcomes — `NO_MATCH`, `UNAUTHORIZED`, `MISSING_DOMAIN` — are
*not* retried. 401 fails every domain in the batch immediately (config problem,
not transient).

**Don't trust the HTTP status alone.** Per the docs and confirmed by testing, a
200 batch can contain per-domain errors, including `NO_MATCH`. The client parses
per-domain `status` and matches results back to the domain we *requested*
(case-insensitively), so a mismatched echo can't silently drop a record.

**Explicit terminal outcome for every input row or unique valid domain.** Every
invalid input row, including blank domain cells, is emitted directly, and every
unique valid domain ends as
`success | no_match | failed` with `occurrences` recording duplicates. Failures
carry a code + detail. Nothing is filtered away silently — this was the top
requirement.

**Normalise the messy fields into an honest shape.** `industry` → always a list;
`location` → `(city, country)`; `employeeCount` → an exact `employee_count` when
the provider gives a real number, otherwise `employee_range` for banded strings
(`"1,000-5,000"`). I deliberately do **not** invent a midpoint for bands — that
would fabricate precision the provider never gave. I trust the queried domain
over the echoed `data.domain` so records stay attributable.

**Streaming I/O for scale.** Input is read row-by-row; unique domains are
materialised once per run; only a bounded number of batch tasks are kept in
flight at once; and results are written to JSONL as each batch completes.
Memory stays flat with respect to in-flight work and output buffering, but still
grows with the number of unique domains because the dedup set is held in memory
(measured: ~36 MB at 600 domains). JSONL (not a single JSON array) is chosen
precisely because it streams.

**Scale, measured (not just claimed).** I generated synthetic inputs and ran
them end-to-end. These are observations from individual probe runs, not a
deterministic benchmark; the mock intentionally injects random failures:

| Input | Success | Failed | Peak RSS | Throughput |
|-------|---------|--------|----------|------------|
| 40    | 94.4%   | 0      | ~35 MB   | ~13/s      |
| 600 (before limiter) | 9.2% | 543 | 37 MB | — |
| 600 (with limiter)   | 96.5% | 0  | 36 MB | ~8/s   |

At ~8 domains/s the pipeline sits just under the provider's ~10/s ceiling, so a
100k run is roughly 3–3.5 hours — bound by the *provider's* rate limit, not by
our code. Memory growth is driven mainly by the number of unique domains kept
for dedup, not by the number of concurrent tasks or the output format.

**De-dup case-insensitively, but keep the count.** `stripe.com` / `Stripe.com`
collapse to one provider call with `occurrences: 3` recorded, so we don't pay to
enrich the same company twice while still reflecting the input faithfully.

## Assumptions (where the spec was ambiguous)

- A "domain" is validated only structurally (has a dot, no whitespace). Whether a
  company *exists* is the provider's call (`NO_MATCH`), not ours — so validation
  stays permissive and only rejects clearly-broken input like `not a domain`.
- JSONL + a JSON summary is a reasonable "structured format of your choosing";
  it's greppable, streamable, and trivial to load into anything downstream.
- A blank `domain` cell is invalid input and should be surfaced, not skipped.
- The default token in config is fine for the mock; in production it would come
  from the environment (`--token` / `PROVIDER_TOKEN` are supported for that).
- Deduping across a run is desirable. If the caller genuinely wanted per-row
  output including duplicates, `occurrences` already carries enough to expand it.

## Things I noticed about the provider

- **25/batch is a documented-but-unusable limit** given the bucket capacity of
  ~20 (see above). This is the single most important operational finding.
- HTTP 200 with `status: "error"` is real (e.g. `NO_MATCH`); trusting the HTTP
  code alone would misclassify these as successes.
- Field shapes genuinely vary per record (number vs string vs banded employee
  counts; string vs array industries), so normalisation isn't optional.
- Omitting the version header silently downgrades you to v1 with *different field
  names* — a quiet correctness trap.

## Known limitations / what I'd do next with another day

- **Resumability / checkpointing.** Output streams, but a re-run re-does
  everything. I'd add a manifest of already-completed domains so a 100k job can
  resume after interruption.
- **Backpressure from input.** Currently all unique domains are chunked up front
  (a set of 100k domains is only a few MB, so fine), but a truly huge input would
  benefit from streaming chunks through a bounded queue rather than materialising
  the full list.
- **Richer failure taxonomy.** `EXHAUSTED` currently collapses "exhausted on 429"
  vs "exhausted on 5xx"; the detail string distinguishes them, but splitting the
  codes would make the summary even more actionable.
- **More tests.** The highest-risk logic is already covered: normalisation and
  dedup, plus a provider-client test against a fake transport (429/`Retry-After`,
  partial batches, `NO_MATCH`) and a pipeline back-pressure test. Next I'd add an
  end-to-end test against the live mock in CI.

## Where I stopped and why

Scoped to the brief's ~3–4h: correct handling of the hard cases (rate limits,
transient failures, messy data, no silent loss) and choices that make 100k safe,
without gold-plating. The client-side limiter and checkpointing are the two
things I'd reach for first if this were going to production.
