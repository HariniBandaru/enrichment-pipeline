# Enrichment Pipeline

Reads a list of company domains, enriches each one via the provider API, writes
structured results, and emits an operator-facing run summary. Built to behave
well at both ~40 rows and 100k+ (bounded concurrency, batching, streaming I/O,
no silent data loss).

- **Language:** Python 3.10+
- **Only dependency:** `httpx` (async HTTP client). The pure parsing and
  normalization tests use the standard library; full pipeline coverage expects
  the runtime dependency to be installed.

## Quick start

```bash
# 1. (Optional) create a venv, then install the one dependency
pip install -r requirements.txt

# 2. In one terminal, start the mock provider (from the repo root)
node starter-kit/mock-provider.js        # http://localhost:4000

# 3. In another terminal, run the pipeline
python3 -m enrichment \
  --input starter-kit/domains.csv \
  --output out/results.jsonl \
  --summary out/summary.json
```

You'll see a summary printed to the console, with full results in
`out/results.jsonl` and the machine-readable summary in `out/summary.json`.

## Run the tests

```bash
python3 -m unittest discover -s tests -v
```

This runs the full suite. The input/normalization tests are stdlib-only; the
pipeline test imports the runtime package and therefore expects `httpx` to be
installed.

## CLI options

```
--input, -i        Input CSV / domain list (required)
--output, -o       JSONL results path        (default: out/results.jsonl)
--summary, -s      JSON run-summary path      (default: out/summary.json)
--provider-url     Override provider base URL (default: http://localhost:4000)
--token            Override bearer token      (default: demo-token-abc123)
--batch-size       Domains per batch request  (default: 10)
--concurrency      Max in-flight requests     (default: 4)
--max-retries      Transient-error retries    (default: 5)
```

All of these can also be set via env vars (`PROVIDER_URL`, `PROVIDER_TOKEN`,
`PROVIDER_VERSION`,
`ENRICH_BATCH_SIZE`, `ENRICH_CONCURRENCY`, `ENRICH_TIMEOUT`,
`ENRICH_MAX_RETRIES`). The client-side rate limiter can be retuned to a
different provider bucket without editing source via `ENRICH_RATE_LIMIT`
(tokens/sec), `ENRICH_RATE_BURST` (bucket capacity), and `ENRICH_MAX_RATE_WAITS`
(429-wait budget). Invalid runtime settings fail fast with exit code `2`
(for example `--batch-size 0`, or a batch size above the provider's usable token
bucket capacity of `20`). Exit code is non-zero if a non-empty run enriches
nothing, so it's safe to wire into cron/CI.

## Input

- A CSV with a `domain` header column, **or** a plain one-domain-per-line file.
- Duplicate domains are collapsed case-insensitively (`Stripe.com` == `stripe.com`)
  and counted (`occurrences` in the output).
- Rows that can't be a domain, including empty `domain` cells, are flagged as
  `invalid_input` in the output rather than sent to the provider.

## Output

Example output from the bundled input is included in
`out/sample-results.jsonl` and `out/sample-summary.json`. Regular run outputs in
`out/` remain git-ignored.

**`results.jsonl`** — one JSON object per line (streams well at scale). Every
invalid input row gets its own terminal `outcome`, and every unique valid domain
gets one terminal record with an `occurrences` count for duplicates:
`success`, `no_match`, `invalid_input`, or `failed`. Example success:

```json
{
  "input_value": "stripe.com",
  "domain": "stripe.com",
  "outcome": "success",
  "occurrences": 3,
  "attempts": 1,
  "company": {
    "domain": "stripe.com",
    "name": "Stripe",
    "employee_count": 3852,
    "employee_range": null,
    "industries": ["Logistics", "Manufacturing"],
    "city": "Toronto",
    "country": "CA",
    "founded_year": 2003,
    "annual_revenue_usd": 84800000
  }
}
```

A failure carries an `error_code` and `error_detail` so it's actionable:

```json
{"input_value": "render.com", "domain": "render.com", "outcome": "failed",
 "attempts": 6, "error_code": "EXHAUSTED",
 "error_detail": "retryable error persisted past max_retries"}
```

**`summary.json`** — counts by outcome, a breakdown of *why* failures failed,
enrichment success rate (`succeeded / (succeeded + no_match + failed)`), elapsed
time, and throughput. Invalid input is reported separately and excluded from
the rate. This is the artefact an operator reads first.

## How it handles the provider's real-world behaviour

- **Versioning:** always sends `X-Provider-Version: 2` (v1 has a different shape).
- **Rate limits:** a **client-side token bucket** paces requests just under the
  provider's limit (so we avoid 429s rather than bounce off them), and 429s are
  handled on a separate budget from real errors so throttling can't cause
  failures. Batch size is tuned to the provider's bucket (see `DECISIONS.md` —
  the documented max of 25/batch is not usable). This is what keeps a 100k run
  near the throughput ceiling with a high success rate. In one synthetic probe
  run, this measured 96.5% at 600 domains versus 9.2% without the limiter;
  exact figures vary because the mock intentionally injects random failures.
- **Transient failures / 5xx / timeouts:** retried up to `--max-retries` with
  backoff + jitter; per-domain `TEMPORARY` is retried, terminal codes
  (`NO_MATCH`, etc.) are not.
- **Slow responses:** per-request timeout so one slow call can't stall a worker.
- **Messy data:** `employeeCount` (int / banded string / null), `industry`
  (string or array), `location` (object or string) are all normalised; see
  `enrichment/normalize.py`.
- **No silent data loss:** invalid rows, including blank domain cells, are
  emitted directly, and valid duplicate domains are represented explicitly via a
  single result plus `occurrences`.

## Layout

```
enrichment/
  cli.py         # argument parsing + entrypoint
  pipeline.py    # orchestration: input -> provider -> normalise -> sink + summary
  provider.py    # async client: batching, retries, backoff, rate-limit handling
  normalize.py   # provider payload normalisation (the messy-data logic)
  inputs.py      # CSV reading, validation, case-insensitive dedup
  models.py      # Company / Result / Outcome
  summary.py     # run-summary aggregation + rendering
  config.py      # all tunables in one place
tests/           # stdlib unittest: normalisation + input parsing
                 # plus a pipeline orchestration test (expects httpx installed)
```

See `DECISIONS.md` for trade-offs and known limitations, and `AI_LOG.md` for how
AI was used on this.
