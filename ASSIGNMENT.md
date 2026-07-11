# Senior Engineer Take-Home — Enrichment Pipeline

Thanks for taking the time. This should take **about 3–4 hours**. Please don't
pour a weekend into it — we've scoped it so a focused half-day is plenty, and
we'd rather see sharp judgment on the core than a gold-plated everything.

## About AI use — read this first

**We build with AI every day, and we want you to use it here.** Copilot, Cursor,
Claude, ChatGPT — whatever you actually work with. Using AI well is part of the
job, not a workaround, and pretending otherwise would tell us nothing useful.

So we're not testing whether you can hand-write a retry loop. We're testing the
things AI *doesn't* do for you:

- **Judgment** — what to build, what to skip, which trade-offs to make.
- **Discernment** — catching where the AI is wrong, incomplete, or confidently
  misleading.
- **Communication** — explaining a design so a teammate can trust and extend it.

The exercise and the follow-up call are built around those. A submission that's
100% AI-generated with no fingerprints of your own thinking tends to be easy to
spot, and it doesn't do well. A submission where you clearly drove the tool and
made the calls does. Bring your judgment; let the AI type.

## The scenario

A big part of what we do at Orbital is enrich lists of companies by calling
external data providers. Those providers are rate-limited, occasionally flaky,
and return messy, inconsistent data. Doing this *well* — correctly, at scale,
without silent data loss — is real, load-bearing work here.

You've been handed a new provider and asked to build the enrichment step.

## Your task

In `starter-kit/` you'll find a **mock provider API** (`mock-provider.js`), its
docs (`API.md`), and a sample input (`domains.csv`). The mock behaves like a real
vendor: auth, rate limits, transient failures, slow responses, and data that
isn't uniform. Read `API.md`, then get your hands dirty with it.

### Part A — Build the enrichment pipeline

Build a small program (CLI or service, **any language/stack you like**) that:

1. Reads a list of company domains from an input file (`domains.csv` is a
   starting point; assume the real one could be much larger).
2. Enriches each domain via the provider.
3. Writes out the enriched results in a structured format of your choosing.
4. Produces a **run summary**: how many succeeded, how many failed, and *why*
   the failures failed — enough that an operator could act on it.

Handle the provider's real-world behavior sensibly. Part of the exercise is
discovering what "sensibly" means here by reading the docs and testing — we've
left that deliberately open. A few things we *do* care about:

- **It should behave well at both small and large scale.** You'll test against
  ~40 domains, but design as if the input could be **100k+**. You don't need to
  actually run 100k; we care about the choices that make it safe to.
- **No silent data loss.** If something can't be enriched, that fact should be
  visible in the output, not swallowed.
- **It should be runnable by us.** A short README with exact commands.

Where the requirements are ambiguous, **make a call and write down why** (see
`DECISIONS.md` below). We left gaps on purpose; how you close them is signal.

### Part B — Code review

Open `starter-kit/review_me.ts`. A teammate generated it with AI and wants to
merge it. **Review it** — leave inline comments (a `// REVIEW:` note above the
relevant line is fine) on what you'd block, fix, or question, and note what
matters most. This should take ~20–30 minutes. Read before you run it.

## What to hand back

A git repo (or zip) containing:

1. **Your code** for Part A, plus a **README** with exact run instructions.
2. **`review_me.ts`** with your review comments (Part B).
3. **`DECISIONS.md`** — the important one. Keep it short and real:
   - Key decisions and the trade-offs behind them.
   - Assumptions you made where the task was ambiguous.
   - Anything odd you noticed about the provider and how you handled it.
   - **Known limitations** and what you'd do next with another day.
4. **`AI_LOG.md`** — how you worked with AI:
   - Which tools you used and roughly how.
   - **At least three moments where you corrected, overrode, or distrusted the
     AI** — what it suggested, what you did instead, and why. (This is the part
     we read most closely. Be specific and honest — "it was all fine" is a miss.)
5. A **short walkthrough**: either a **~5-minute Loom/video** or a **PR-style
   write-up** explaining your design as if you were opening this for review.

## How we evaluate this

We want to be transparent about what earns points, so you can spend your time
where it counts:

- **Judgment & trade-offs** — the *why* behind your choices, especially on the
  ambiguous and messy parts. This matters more than lines of code.
- **Correctness on the hard cases** — failures, rate limits, inconsistent data.
- **Design for scale** — 40 rows and 100k rows are different problems.
- **AI collaboration** — evidence you drove the tool and caught its mistakes.
- **Code-review acuity** (Part B) — what you catch and how you prioritize.
- **Communication** — can we read `DECISIONS.md` / watch the walkthrough and
  understand your thinking without you in the room.

We are **not** grading on: framework choices, test coverage percentage, a
polished CLI, or handling scenarios that can't happen. Simple and correct beats
clever and sprawling. If you're deciding whether to build something, ask "would
a senior engineer say this is worth it here?" — and if not, skip it and say so.

## The follow-up call

If we move forward, there's a **~45-minute call** where we'll walk through your
submission together — how it works, why you made certain calls, and a couple of
"what would you change if…" extensions. It's a conversation, not a quiz. The best
prep is simply having made your own decisions and being able to talk about them.

## Logistics

- **Time:** ~3–4 hours. If you hit the ceiling, stop and note what you'd do next
  in `DECISIONS.md` — knowing where to stop is senior signal too.
- **Stack:** your choice. Use what you're fastest in.
- **The provider:** treat `mock-provider.js` as an opaque external service. Use
  it via HTTP and `API.md`; please don't read or modify its source (in real life
  you wouldn't have it, and it gives away the parts you're meant to discover).
- **Questions?** If something is genuinely blocking, email us. But note that
  "the spec is ambiguous here, so I assumed X" is a valid and welcome answer —
  we don't need you to resolve every ambiguity with us first.

Have fun with it. We're excited to see how you think.
