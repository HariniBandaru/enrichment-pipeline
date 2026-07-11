// review_me.ts
//
// PART B — Code review.
//
// A teammate generated the function below with an AI assistant and opened a PR.
// It "works" against the mock provider for a handful of domains and they'd like
// to merge it. Review it as you would a real PR: leave comments inline (a `//
// REVIEW:` line above the relevant code is fine) covering correctness, scale,
// data quality, failure handling, and anything else you'd block or push back on.
//
// You do NOT need to rewrite it. We care about what you catch and how you
// prioritize it. Do not run it against the provider before reading it — read
// first, the way you would in a real review.

// REVIEW (summary of what I'd block on, most important first):
//   1. Silent data loss — errors become null and are filtered out; no caller
//      ever learns which domains failed or why. This is the biggest issue.
//   2. Won't scale — Promise.all fans out one request per domain with no
//      concurrency limit (thundering herd at 100k) and ignores the batch API.
//   3. Unbounded retry — `while (true)` with no cap, no backoff, and no
//      Retry-After handling can spin forever and worsen a rate-limit event.
//   4. Wrong/deprecated data — missing `X-Provider-Version: 2` header silently
//      returns the v1 shape, so the parsing below reads the wrong fields.
//   5. Secret leak — the bearer token is logged.
// Details inline below.

type Company = {
  domain: string;
  name: string;
  employees: number;
  industry: string | string[];
};

// REVIEW: Config (URL + token) is hard-coded. The token is a secret and should
// come from the environment / a secrets manager, not source control.
const PROVIDER_URL = "http://localhost:4000";
const PROVIDER_TOKEN = "demo-token-abc123";

export async function enrichDomains(domains: string[]): Promise<Company[]> {
  // REVIEW: Never log credentials. This writes the bearer token to stdout/log
  // aggregation in plaintext. Log the count only.
  console.log(`Enriching ${domains.length} domains with token ${PROVIDER_TOKEN}`);

  // REVIEW: Promise.all maps every domain to a concurrent request. At 40 this is
  // fine; at 100k it opens ~100k sockets at once — it will exhaust FDs/memory,
  // hammer the provider, and trigger sustained 429s. Needs bounded concurrency
  // (a pool/semaphore) and should use POST /v1/enrich/batch (<=25, and in
  // practice <=20 due to the bucket capacity) to cut round-trips.
  // REVIEW: Also, Promise.all rejects on the first throw — one unexpected error
  // discards all in-flight work. Prefer allSettled or per-item error capture.
  const results = await Promise.all(
    domains.map(async (domain) => {
      // REVIEW: `domain` is interpolated into the URL unencoded. Use
      // encodeURIComponent(domain) to avoid breakage/injection on odd input.
      // REVIEW: Unbounded retry loop. No max attempts, no backoff, no jitter.
      // On a real outage this becomes a tight hot loop that amplifies load.
      while (true) {
        try {
          // REVIEW: Missing `X-Provider-Version: 2` header → provider serves the
          // DEPRECATED v1 format, whose fields differ (companyName/employees vs
          // name/employeeCount). The parsing below then silently reads wrong or
          // undefined fields. This is a correctness bug, not just a style nit.
          // REVIEW: No request timeout. A slow response (the docs warn some are)
          // will hang this task indefinitely with no AbortController.
          const res = await fetch(`${PROVIDER_URL}/v1/enrich?domain=${domain}`, {
            headers: { Authorization: `Bearer ${PROVIDER_TOKEN}` },
          });

          // REVIEW: Retries 429/5xx but ignores the `Retry-After` header, so it
          // retries immediately and keeps tripping the limiter. Honor it.
          // REVIEW: `continue` retries forever with zero delay — see the
          // unbounded-loop note above. Should back off and give up after N.
          if (res.status === 429 || res.status >= 500) {
            // Provider is busy or erroring — just try again.
            continue;
          }

          // REVIEW: `body: any` throws away all type safety on an external,
          // untrusted payload. Validate/parse into a known shape.
          const body: any = await res.json();
          // REVIEW: The provider returns HTTP 200 with `status: "error"` (e.g.
          // NO_MATCH). This code never checks `body.status`, so a NO_MATCH gets
          // treated as success and dereferences a missing `data` below.
          const data = body.data;

          // REVIEW: If `data` is undefined (error body / v1 shape), `data.domain`
          // throws — which is then swallowed by the catch and turned into a
          // dropped domain. No null guard on `data`.
          return {
            // REVIEW: `parseInt(data.employeeCount)` is wrong for this field:
            //   - number 1200          -> parseInt coerces to string first (ok-ish)
            //   - banded "1,000-5,000" -> parseInt returns 1 (silently corrupt!)
            //   - null                 -> NaN (silently corrupt)
            //   - "3852"               -> 3852 (ok)
            // Banded/null values need explicit handling, not parseInt.
            domain: data.domain,
            name: data.name,
            employees: parseInt(data.employeeCount),
            // REVIEW: `data.industry` may be a string OR string[] (docs). Passed
            // through untouched, so downstream consumers must special-case both.
            // Normalize to one shape (e.g. always string[]).
            industry: data.industry,
          };
        } catch (e) {
          // REVIEW: This is the critical failure-handling bug. Every error —
          // network, JSON parse, undefined deref — is swallowed and returned as
          // null, then filtered out below. The caller cannot distinguish "no
          // such company" from "we failed to fetch it". Requirement is explicit:
          // no silent data loss. Capture the error + domain and surface it.
          return null;
        }
      }
    })
  );

  // REVIEW: `filter(Boolean)` silently discards every failed domain, so the
  // returned array is shorter than the input with no record of what's missing.
  // The function should return per-domain outcomes (ok/failed + reason), or at
  // minimum also return the failures, so an operator can see and act on them.
  return results.filter(Boolean) as Company[];
}
