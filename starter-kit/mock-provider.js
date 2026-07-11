#!/usr/bin/env node
/**
 * Orbital take-home — mock enrichment provider.
 *
 * A deliberately realistic third-party company-data API: rate limits, transient
 * failures, messy schemas, and a couple of behaviors you'll only find by reading
 * the docs and testing. Treat it as an opaque external service — see API.md and
 * discover the rest yourself. Please don't read or edit this file; in real life
 * you wouldn't have the provider's source, and reading it removes half the point
 * of the exercise. (We may ask on the call how you figured out its behavior.)
 *
 * Run:  node mock-provider.js        # listens on http://localhost:4000
 * Port: PORT=5000 node mock-provider.js
 *
 * Zero dependencies. Requires Node 18+ (uses only built-ins).
 */

'use strict';

const http = require('http');
const { URL } = require('url');

const PORT = Number(process.env.PORT || 4000);
const DEBUG = process.env.PROVIDER_DEBUG === '1';

// ---- Rate limiter: global token bucket -------------------------------------
// Capacity 20, refills 10 tokens/sec. Each enriched domain costs 1 token
// (a batch of N costs N). Requests that can't be fully served get 429.
const BUCKET = { capacity: 20, tokens: 20, refillPerSec: 10, last: Date.now() };

function takeTokens(cost) {
  const now = Date.now();
  const elapsed = (now - BUCKET.last) / 1000;
  BUCKET.tokens = Math.min(BUCKET.capacity, BUCKET.tokens + elapsed * BUCKET.refillPerSec);
  BUCKET.last = now;
  if (BUCKET.tokens >= cost) {
    BUCKET.tokens -= cost;
    return true;
  }
  return false;
}

// ---- Deterministic per-domain behavior (FNV-1a hash) -----------------------
function hash(s) {
  let h = 2166136261 >>> 0;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619) >>> 0;
  }
  return h;
}

function classify(domain) {
  const h = hash(domain.toLowerCase().trim());
  return {
    // ~5% of domains: the provider has no match. Note the HTTP status these
    // come back with when you test them.
    errorBody: h % 20 === 0,
    // ~14% of domains: intermittently unavailable. Sometimes fails, sometimes
    // works — on the same input.
    transient: h % 7 === 0,
    // Which of several equally-valid-but-inconsistent shapes this record uses.
    schemaVariant: h % 4,
  };
}

// ---- Record builders -------------------------------------------------------
function baseRecord(domain) {
  const h = hash(domain);
  const name = domain.split('.')[0].replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return { domain, _h: h, name };
}

// v2 (current) success shape — intentionally inconsistent across records.
function v2Record(domain) {
  const { _h, name } = baseRecord(domain);
  const { schemaVariant } = classify(domain);
  const rec = { domain, name, provider_version: 2 };

  // employeeCount: number | banded string | null  (pick your normalization)
  if (schemaVariant === 0) rec.employeeCount = (_h % 5000) + 5;
  else if (schemaVariant === 1) rec.employeeCount = ['1-10', '11-50', '51-200', '201-1,000', '1,000-5,000'][_h % 5];
  else if (schemaVariant === 2) rec.employeeCount = null;
  else rec.employeeCount = String((_h % 5000) + 5);

  // industry: string | string[]
  const industries = ['SaaS', 'Fintech', 'Logistics', 'Healthcare', 'Manufacturing'];
  rec.industry = schemaVariant % 2 === 0 ? industries[_h % industries.length] : [industries[_h % industries.length], industries[(_h + 2) % industries.length]];

  // location: object | string
  const city = ['Austin', 'Berlin', 'Toronto', 'Singapore', 'London'][_h % 5];
  rec.location = schemaVariant === 1 ? `${city}` : { city, country: ['US', 'DE', 'CA', 'SG', 'GB'][_h % 5] };

  // foundedYear: sometimes absent
  if (schemaVariant !== 2) rec.foundedYear = 1980 + (_h % 44);

  // revenue in whole USD
  rec.annualRevenueUsd = ((_h % 900) + 1) * 100000;

  return rec;
}

// v1 (deprecated) shape — what you get if you forget the version header.
// Subtly different: `companyName` not `name`, revenue in *thousands*.
function v1Record(domain) {
  const v2 = v2Record(domain);
  return {
    domain,
    companyName: v2.name,
    provider_version: 1,
    employees: v2.employeeCount,
    industry: v2.industry,
    annualRevenueThousands: Math.round((v2.annualRevenueUsd || 0) / 1000),
  };
}

// Resolve one domain to a { httpStatus, body } — used by both endpoints.
// (Batch callers flatten per-item results; see handleBatch.)
function resolveDomain(domain, versionHeader) {
  const { errorBody, transient } = classify(domain);

  if (errorBody) {
    // No match. Look closely at the status code this returns.
    return { itemStatus: 'error', body: { domain, status: 'error', code: 'NO_MATCH', message: 'No company found for domain' } };
  }
  if (transient && Math.random() < 0.5) {
    return { itemStatus: 'transient', body: { domain, status: 'error', code: 'TEMPORARY', retryable: true, message: 'Upstream temporarily unavailable' } };
  }
  const record = versionHeader === '2' ? v2Record(domain) : v1Record(domain);
  return { itemStatus: 'ok', body: { domain, status: 'ok', data: record } };
}

// ---- HTTP plumbing ---------------------------------------------------------
function send(res, status, body, headers = {}) {
  const payload = JSON.stringify(body);
  res.writeHead(status, { 'content-type': 'application/json', ...headers });
  res.end(payload);
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    let data = '';
    req.on('data', (c) => {
      data += c;
      if (data.length > 1_000_000) reject(new Error('payload too large'));
    });
    req.on('end', () => {
      if (!data) return resolve({});
      try {
        resolve(JSON.parse(data));
      } catch {
        reject(new Error('invalid json'));
      }
    });
  });
}

async function maybeDelay() {
  // Mostly fast; occasionally very slow (test your timeouts).
  const ms = Math.random() < 0.03 ? 3000 + Math.random() * 2000 : 40 + Math.random() * 120;
  await new Promise((r) => setTimeout(r, ms));
}

const server = http.createServer(async (req, res) => {
  let url;
  try {
    url = new URL(req.url, `http://localhost:${PORT}`);
  } catch {
    return send(res, 400, { status: 'error', code: 'BAD_REQUEST' });
  }

  // Debug/ground-truth endpoint (evaluators only; enable with PROVIDER_DEBUG=1).
  if (DEBUG && url.pathname === '/debug/classify') {
    const domain = url.searchParams.get('domain') || '';
    return send(res, 200, { domain, ...classify(domain) });
  }

  if (url.pathname === '/health') return send(res, 200, { status: 'ok' });

  // Auth: any non-empty bearer token is accepted; a missing one is rejected.
  const auth = req.headers['authorization'] || '';
  if (!/^Bearer\s+\S+/.test(auth)) {
    return send(res, 401, { status: 'error', code: 'UNAUTHORIZED', message: 'Provide an Authorization: Bearer <token> header' });
  }

  const version = String(req.headers['x-provider-version'] || '1');

  // GET /v1/enrich?domain=example.com
  if (req.method === 'GET' && url.pathname === '/v1/enrich') {
    const domain = (url.searchParams.get('domain') || '').trim();
    if (!domain) return send(res, 400, { status: 'error', code: 'MISSING_DOMAIN' });
    if (!takeTokens(1)) return send(res, 429, { status: 'error', code: 'RATE_LIMITED' }, { 'retry-after': '1' });
    await maybeDelay();
    const r = resolveDomain(domain, version);
    if (r.itemStatus === 'transient') return send(res, 500, r.body); // transient → HTTP 5xx here
    return send(res, 200, r.body); // note: NO_MATCH also returns 200
  }

  // POST /v1/enrich/batch   { "domains": ["a.com", "b.com"] }   (max 25)
  if (req.method === 'POST' && url.pathname === '/v1/enrich/batch') {
    let payload;
    try {
      payload = await readJson(req);
    } catch (e) {
      return send(res, 400, { status: 'error', code: 'BAD_REQUEST', message: e.message });
    }
    const domains = Array.isArray(payload.domains) ? payload.domains : null;
    if (!domains) return send(res, 400, { status: 'error', code: 'MISSING_DOMAINS' });
    if (domains.length === 0 || domains.length > 25) {
      return send(res, 400, { status: 'error', code: 'BAD_BATCH_SIZE', message: 'Send 1..25 domains per batch' });
    }
    if (!takeTokens(domains.length)) return send(res, 429, { status: 'error', code: 'RATE_LIMITED' }, { 'retry-after': '1' });
    await maybeDelay();
    // Batch always returns HTTP 200; failures are per-item in `results`.
    const results = domains.map((d) => {
      const r = resolveDomain(String(d).trim(), version);
      return r.body;
    });
    return send(res, 200, { status: 'ok', count: results.length, results });
  }

  return send(res, 404, { status: 'error', code: 'NOT_FOUND' });
});

server.listen(PORT, () => {
  console.log(`[mock-provider] listening on http://localhost:${PORT}`);
  if (DEBUG) console.log('[mock-provider] DEBUG on: GET /debug/classify?domain=<d>');
});
