# enrichment-pipeline
Production-minded Python enrichment pipeline that reads company domains, calls a flaky rate-limited provider, normalizes inconsistent data, and writes JSONL results with an actionable run summary. Includes bounded concurrency, batching, retries, rate limiting, no silent data loss, tests, and 100k+ scale considerations.
