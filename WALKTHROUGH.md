# Walkthrough (PR-style write-up)

> This is the "open it for review" write-up the assignment asks for, as the
> written alternative to a Loom. It explains the design, the trade-offs, and the
> one thing I'd want a reviewer to look at hardest.

## What this does

Reads company domains, enriches each via the provider API, and writes structured
results plus an operator-facing run summary. Built to be correct on the messy
cases and safe at 100k+ scale.

```
domains.csv --> validate + dedup --> batch + rate-limit + retry --> normalize --> results.jsonl + summary.json
```

## How to run / review it

```bash
pip install -r requirements.txt
node starter-kit/mock-provider.js                      # terminal 1
python3 -m enrichment -i starter-kit/domains.csv \
  -o out/results.jsonl -s out/summary.json             # terminal 2
python3 -m unittest discover -s tests -v               # tests (pipeline test needs httpx)
```

The input/normalization tests are stdlib-only; the pipeline orchestration test
expects the runtime dependency (`httpx`) to be installed.

## Design, and the "why" behind each choice

**Async + bounded concurrency (Python, one dependency).** The work is I/O-bound,
so async fits and keeps the code small. `httpx` is the only runtime dependency;
the pure parsing/normalization tests are stdlib-only, while the pipeline test
expects the runtime dependency to be installed.

**I investigated the provider before trusting the docs.** Probing with curl
surfaced the key finding: the documented max of **25 domains/batch is unusable**
— the rate-limit bucket holds ~20 tokens and a batch spends one per domain
atomically, so a 25-batch always 429s. Usable batch size is <=20; I default to
10, and the CLI/config now fail fast if you set an impossible batch size.

**Client-side rate limiter (the most important part — please look here).** The
provider is ~20 capacity / ~10 tokens per second. Instead of firing fast and
reacting to 429s, the client keeps its own token bucket and takes a token per
domain before each request, so we ride just under the limit. 429s are handled on
a *separate* budget from real errors, because being throttled isn't a failure —
it just means "wait". Code: `AsyncTokenBucket` and `enrich_batch` in
[enrichment/provider.py](enrichment/provider.py).

**Never trust the HTTP status alone.** A 200 batch can contain per-domain errors,
including `NO_MATCH`. The client parses each item's `status` and matches results
back to the *requested* domain (case-insensitively) so a mismatched echo can't
drop a record.

**Normalize the messy fields honestly.** `industry` -> always a list; `location`
-> `(city, country)`; `employeeCount` -> an exact `employee_count` only when the
provider gives a real number, otherwise `employee_range` for banded strings. I
deliberately don't invent a band midpoint — that would fabricate precision.
Code: [enrichment/normalize.py](enrichment/normalize.py).

**No silent data loss.** Every invalid input row, including blank domain cells,
ends with a terminal outcome, and every unique valid domain ends with a
terminal outcome plus an `occurrences` count for duplicates. Failures carry a
code + reason.

**Streaming I/O.** Input is read row-by-row; only a bounded number of batch
tasks exist at once; results are written as each batch completes. Memory stays
flat with respect to in-flight work and output buffering, though dedup still
requires holding the set of unique domains for the run.

## Scale: measured, not claimed

I ran synthetic inputs end-to-end. This is the part I'm most glad I checked,
because the 40-row happy path hid a real bug. These are individual probe runs;
exact figures vary because the mock intentionally injects random failures:

| Input | Success | Failed | Peak RSS | Notes |
|-------|---------|--------|----------|-------|
| 40    | 94.4%   | 0      | ~35 MB   | baseline |
| 600 (before limiter) | **9.2%** | 543 | 37 MB | 540 exhausted on 429 |
| 600 (with limiter)   | **96.5%** | 0 | 36 MB | ~8 domains/s |

At ~8/s the pipeline sits just under the provider's ~10/s ceiling, so a 100k run
is ~3–3.5 hours — bound by the provider, not our code. In-flight request and
output-buffer memory stay bounded; run-wide de-duplication still grows with the
number of unique domains.

## Part B — code review

`starter-kit/review_me.ts` is annotated with prioritized `// REVIEW:` comments.
The issues I'd block on, most important first:

1. **Silent data loss** — errors become `null` and are filtered out; the caller
   never learns which domains failed or why.
2. **Won't scale** — `Promise.all` fans out one request per domain (thundering
   herd at 100k), ignores the batch API.
3. **Unbounded retry** — `while (true)` with no cap, no backoff, no `Retry-After`.
4. **Wrong data** — missing `X-Provider-Version: 2` silently returns the v1 shape.
5. **Secret leak** — the bearer token is logged.
6. **`parseInt(employeeCount)`** silently corrupts banded/null values.

## What I'd do next with another day

- **Checkpoint/resume** for multi-hour 100k runs (a manifest of completed
  domains) so an interruption doesn't restart from zero.
- **Bounded input streaming** through a queue for inputs too large to hold even
  a domain set in memory (10M+).
- **An end-to-end test against the live mock in CI** to complement the existing
  unit and provider-client (fake-transport) tests.
- **A distributed rate limiter for multi-worker runs.** The `AsyncTokenBucket`
  here is deliberately minimal — it's the same token-bucket idea a library like
  [Bottleneck](https://github.com/SGrondin/bottleneck) exposes via `reservoir` +
  `maxConcurrent`, just the slice I needed with zero dependencies. It's correct
  for a single process, but its state lives in one process's memory: run two
  copies and each assumes the full budget, so together they'd exceed the
  provider's bucket and start getting throttled. To scale horizontally I'd move
  to a shared limiter — Bottleneck's Redis-backed clustering (Node) or an
  equivalent Redis token bucket (Python) — so all workers draw from one budget.
  I'd also seed the rate from the provider's own `Retry-After` / rate-limit
  headers instead of a probed constant, so it self-tunes if the limit changes.

See [DECISIONS.md](DECISIONS.md) for the fuller trade-off log and
[AI_LOG.md](AI_LOG.md) for where I corrected the AI.
