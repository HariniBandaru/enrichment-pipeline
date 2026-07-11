"""Pipeline orchestration: input -> provider -> normalise -> output + summary.

Design for scale (100k+):
- Unique domains are chunked into batches of ``batch_size``.
- Only a bounded number of batch tasks exist at once, so memory and in-flight
  request work stay bounded.
- Results are written to the output sink *as each batch completes* (streaming),
  so we never hold the full result set in memory and partial progress survives a
  crash.

Run-wide de-duplication still retains one entry per unique domain.
"""

from __future__ import annotations

import asyncio
import time
from typing import Callable, Optional

from .config import Config
from .inputs import DedupedInput, load_deduped
from .models import Company, Outcome, Result
from .normalize import normalize_company
from .provider import ProviderClient, RawResult
from .summary import RunSummary

# A sink receives one terminal Result at a time (e.g. a JSONL writer).
ResultSink = Callable[[Result], None]


def _classify(raw: RawResult, occurrences: int, input_value: str) -> Result:
    """Turn a raw provider result into a terminal Result record."""

    if raw.ok and raw.data is not None:
        company: Company = normalize_company(raw.domain, raw.data)
        return Result(
            input_value=input_value,
            domain=raw.domain,
            outcome=Outcome.SUCCESS,
            company=company,
            attempts=raw.attempts,
            occurrences=occurrences,
        )

    if raw.error_code == "NO_MATCH":
        return Result(
            input_value=input_value,
            domain=raw.domain,
            outcome=Outcome.NO_MATCH,
            error_code="NO_MATCH",
            error_detail=raw.error_detail or "no company for this domain",
            attempts=raw.attempts,
            occurrences=occurrences,
        )

    return Result(
        input_value=input_value,
        domain=raw.domain,
        outcome=Outcome.FAILED,
        error_code=raw.error_code or "UNKNOWN",
        error_detail=raw.error_detail,
        attempts=raw.attempts,
        occurrences=occurrences,
    )


async def _run_chunk(
    client: ProviderClient,
    chunk: list[str],
    deduped: DedupedInput,
) -> list[Result]:
    raws = await client.enrich_batch(chunk)
    results: list[Result] = []
    for raw in raws:
        occ = deduped.occurrences.get(raw.domain, 1)
        input_value = deduped.first_raw.get(raw.domain, raw.domain)
        results.append(_classify(raw, occ, input_value))
    return results


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield items[i : i + size]


async def run_pipeline(
    input_path: str,
    sink: ResultSink,
    config: Optional[Config] = None,
) -> RunSummary:
    """Enrich every domain in ``input_path``, streaming results to ``sink``."""

    config = config or Config()
    started = time.monotonic()

    deduped = load_deduped(input_path)
    summary = RunSummary(
        total_input_rows=deduped.total_rows,
        unique_domains=len(deduped.unique_domains),
        duplicate_rows=sum(v - 1 for v in deduped.occurrences.values()),
    )

    # Invalid input is a terminal, visible outcome — never silently dropped.
    for raw_value in deduped.invalid_rows:
        r = Result(
            input_value=raw_value,
            domain=None,
            outcome=Outcome.INVALID_INPUT,
            error_code="INVALID_INPUT",
            error_detail="not a plausible domain",
        )
        summary.record(r)
        sink(r)

    async with ProviderClient(config) as client:
        chunk_iter = iter(_chunks(deduped.unique_domains, config.batch_size))
        in_flight: dict[asyncio.Task[list[Result]], list[str]] = {}

        def _schedule_next() -> bool:
            try:
                chunk = next(chunk_iter)
            except StopIteration:
                return False
            task = asyncio.create_task(_run_chunk(client, chunk, deduped))
            in_flight[task] = chunk
            return True

        def _record(result: Result) -> None:
            summary.record(result)
            sink(result)

        target_in_flight = max(1, config.max_concurrency)
        while len(in_flight) < target_in_flight and _schedule_next():
            pass

        while in_flight:
            done, _ = await asyncio.wait(
                set(in_flight), return_when=asyncio.FIRST_COMPLETED
            )
            auth_failed = False
            for task in done:
                in_flight.pop(task)
                for result in await task:
                    _record(result)
                    auth_failed = auth_failed or result.error_code == "UNAUTHORIZED"

            if auth_failed:
                # Authentication is global configuration, so later batches cannot
                # recover. Cancel outstanding work and emit a terminal result for
                # every domain that was not sent, preserving no-silent-loss.
                pending_items = list(in_flight.items())
                for task, _chunk in pending_items:
                    task.cancel()
                pending_outcomes = await asyncio.gather(
                    *(task for task, _chunk in pending_items),
                    return_exceptions=True,
                )
                for (task, chunk), outcome in zip(pending_items, pending_outcomes):
                    in_flight.pop(task, None)
                    if isinstance(outcome, list):
                        for result in outcome:
                            _record(result)
                    else:
                        for domain in chunk:
                            _record(
                                _classify(
                                    RawResult(
                                        domain,
                                        ok=False,
                                        error_code="UNAUTHORIZED",
                                        error_detail=(
                                            "run stopped after provider "
                                            "authentication failed"
                                        ),
                                    ),
                                    deduped.occurrences.get(domain, 1),
                                    deduped.first_raw.get(domain, domain),
                                )
                            )
                for remaining_chunk in chunk_iter:
                    for domain in remaining_chunk:
                        _record(
                            _classify(
                                RawResult(
                                    domain,
                                    ok=False,
                                    error_code="UNAUTHORIZED",
                                    error_detail=(
                                        "run stopped after provider "
                                        "authentication failed"
                                    ),
                                ),
                                deduped.occurrences.get(domain, 1),
                                deduped.first_raw.get(domain, domain),
                            )
                        )
                break

            while len(in_flight) < target_in_flight and _schedule_next():
                pass

    summary.elapsed_seconds = time.monotonic() - started
    return summary
