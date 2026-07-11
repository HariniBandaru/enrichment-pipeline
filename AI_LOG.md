# AI Log

> Note to reviewer: I used an AI coding assistant heavily on this, as encouraged.
> Below is an honest account of how, and — the part that matters — the moments I
> didn't take its output at face value. Please read this as my own recollection
> of the process; I've kept it specific.

## Tools & how I used them

- **Cursor (agent mode)** for scaffolding the package, writing boilerplate
  (dataclasses, argparse, the JSONL writer), and drafting the docs.
- I drove the design decisions and the provider-behaviour investigation myself,
  and used the AI to type them out fast. I verified everything against the actual
  mock provider rather than trusting descriptions of how it "should" behave.

## Moments I corrected, overrode, or distrusted the AI

**1. The batch size of 25 (docs + AI were both confidently wrong for practice).**
The API docs say up to 25 domains per batch, and the assistant happily defaulted
`batch_size` to 25. Before trusting it, I probed the provider directly with curl:
a cold 25-domain batch returns **429 every time**, while 20 succeeds and 10
sustains. The rate-limit bucket is ~20 capacity refilling ~10/s, and a 25-domain
batch can never accumulate 25 tokens — so the documented maximum is *unusable*.
I overrode the default to `batch_size=10, concurrency=4`. This one correction
moved the sample run from ~28% to ~92% success. Lesson: the docs describe the
API, not its rate limiter; test the thing.

**2. `parseInt(employeeCount)` / fabricating a number for banded values.**
The natural AI-generated approach (and exactly what the Part B code does) is to
coerce `employeeCount` to a single integer. But the field comes back as banded
strings like `"1,000-5,000"` — `parseInt` turns that into `1`, silently corrupt.
The assistant's first suggestion was to take the band midpoint. I rejected that:
inventing `3000` from a band fabricates precision the provider never gave. I kept
two explicit fields instead — `employee_count` (only when it's a real number) and
`employee_range` (the band) — so downstream never mistakes a guess for a fact.

**3. Retrying everything, including things that will never change.**
The assistant's initial retry logic retried on any non-2xx, mirroring the naive
`while (true)` in the review file. I pushed back: `NO_MATCH`, `UNAUTHORIZED`, and
`MISSING_DOMAIN` are terminal — retrying them just burns the budget and slows the
run. I split error codes into retryable (`TEMPORARY`, `RATE_LIMITED`, 5xx, 429,
timeouts) vs terminal, and made 401 fail the batch immediately rather than loop.
I also made sure `Retry-After` is honoured instead of retrying instantly.

**4. Trusting the HTTP status code.**
An early draft branched on `res.ok` and treated a 200 as success. The docs hint —
and testing confirmed — that a 200 batch can carry per-domain `status: "error"`,
including `NO_MATCH`. I reworked the client to parse each item's `status` and
match results back to the domain I *requested* (case-insensitively), so a 200
with an error body is classified correctly and a mismatched echo can't drop a
record.

**5. (Smaller) Silent dedup vs. faithful reporting.**
The assistant deduped domains and moved on. I wanted the output to stay faithful
to the input, so I added an `occurrences` count and kept every invalid row in the
output as `invalid_input` — the brief is explicit about no silent data loss, and
"I quietly threw away 3 rows" is exactly the kind of thing that bites an operator
later.

**6. Not trusting "it's designed for scale" without measuring.**
The assistant (and honestly my own first draft) claimed the design was
scale-ready: bounded concurrency, batching, streaming. Rather than take that at
face value, I generated a 600-domain synthetic input and ran it. It **collapsed
to 9.2% success** — 543 domains exhausted on 429s. The reactive "retry on 429
with a fixed budget" strategy that looked fine at 40 domains fell apart under
sustained load, because a throttle was consuming the same finite retry budget as
real errors. I added a client-side token-bucket limiter and split the 429 budget
from the transient-error budget; the same run went to **96.5% with zero
failures**. Lesson: "designed for scale" is a hypothesis until you run it at
scale — the 40-row happy path hid a load-bearing bug.

## Net

The AI was genuinely useful for speed, but every load-bearing decision here —
batch sizing, what to retry, how to represent messy fields, what counts as a
failure — came from testing the provider and applying judgment, not from
accepting the first suggestion. The Part B review file is a good illustration of
what "accepting the first suggestion" looks like unreviewed.
