# Acme Data — Enrichment API (mock)

> This is a mock of a real third-party provider. It runs locally and behaves
> like a real vendor API would: it's rate-limited, occasionally unreliable, and
> its data isn't perfectly clean. These docs are what the vendor gives you —
> they're decent but not exhaustive. Expect to confirm details by testing.

## Running it

```bash
node mock-provider.js        # http://localhost:4000
PORT=5000 node mock-provider.js
```

No dependencies, no build. Node 18+.

## Auth

Every request needs a bearer token:

```
Authorization: Bearer <your-token>
```

For this mock, any non-empty token works. A missing/blank token returns `401`.

## Versioning

Send this header on every request:

```
X-Provider-Version: 2
```

**v2 is the current response format.** If you omit the header you'll fall back
to the **deprecated v1 format**, which has a different response shape. New
integrations should always use v2.

## Endpoints

### `GET /v1/enrich?domain=<domain>`

Enrich a single domain.

**v2 success response** (`200`):

```json
{
  "domain": "example.com",
  "status": "ok",
  "data": {
    "domain": "example.com",
    "name": "Example",
    "provider_version": 2,
    "employeeCount": 1200,
    "industry": "SaaS",
    "location": { "city": "Austin", "country": "US" },
    "foundedYear": 2011,
    "annualRevenueUsd": 42000000
  }
}
```

Notes from the field (not all of this is guaranteed per record):

- `employeeCount` may come back as a number (`1200`), a banded string
  (`"1,000-5,000"`), or `null`.
- `industry` may be a single string or an array of strings.
- `location` may be an object or a plain city string.
- `foundedYear` is sometimes omitted.

### `POST /v1/enrich/batch`

Enrich up to **25** domains in one call. Cheaper on round-trips, but each domain
still counts against your rate limit.

```json
{ "domains": ["example.com", "acme.com"] }
```

Response (`200`):

```json
{
  "status": "ok",
  "count": 2,
  "results": [
    { "domain": "example.com", "status": "ok", "data": { "...": "..." } },
    { "domain": "acme.com", "status": "error", "code": "TEMPORARY", "retryable": true }
  ]
}
```

Per-domain outcomes live in `results`. Individual items can fail independently
of the overall request.

## Errors & reliability

- **Rate limits.** You'll get `429` with a `Retry-After` header (seconds) when
  you exceed the limit. The bucket refills continuously.
- **Transient failures.** The upstream is occasionally unavailable and some
  calls fail even on valid domains. Retried calls generally succeed.
- **Slow responses.** A small fraction of calls are slow. Set sensible timeouts.
- **Error codes** you may encounter: `NO_MATCH` (no company for that domain),
  `TEMPORARY` (retryable upstream blip), `RATE_LIMITED`, `UNAUTHORIZED`,
  `MISSING_DOMAIN`, `BAD_BATCH_SIZE`.

Every response body carries a `status` field (`"ok"` or `"error"`). We recommend
checking it. HTTP status codes are... mostly reliable — don't assume the HTTP
code alone tells you whether a given domain succeeded.
