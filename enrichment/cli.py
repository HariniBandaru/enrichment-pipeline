"""Command-line entrypoint.

Usage:
    python3 -m enrichment --input starter-kit/domains.csv \
        --output out/results.jsonl --summary out/summary.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import replace
from typing import Optional

from .config import Config
from .models import Result


def _build_config(args: argparse.Namespace) -> Config:
    base = Config.from_env()
    return replace(
        base,
        base_url=args.provider_url or base.base_url,
        token=args.token or base.token,
        batch_size=base.batch_size if args.batch_size is None else args.batch_size,
        max_concurrency=(
            base.max_concurrency if args.concurrency is None else args.concurrency
        ),
        max_retries=base.max_retries if args.max_retries is None else args.max_retries,
    )


def _make_writer(output_path: str):
    """Return (sink, close) writing newline-delimited JSON.

    JSONL is chosen deliberately: it streams (one record per line), so we can
    write results as they complete and the output stays usable on a 100k run
    without buffering everything in memory.
    """

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    fh = open(output_path, "w", encoding="utf-8")

    def sink(result: Result) -> None:
        fh.write(json.dumps(result.to_json(), ensure_ascii=False) + "\n")

    return sink, fh.close


def _run_pipeline(*args, **kwargs):
    from .pipeline import run_pipeline

    return run_pipeline(*args, **kwargs)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="enrichment",
        description="Enrich company domains via the provider API.",
    )
    parser.add_argument(
        "--input", "-i", required=True, help="Path to input CSV / domain list."
    )
    parser.add_argument(
        "--output", "-o", default="out/results.jsonl",
        help="Path for JSONL results (default: out/results.jsonl).",
    )
    parser.add_argument(
        "--summary", "-s", default="out/summary.json",
        help="Path for the JSON run summary (default: out/summary.json).",
    )
    parser.add_argument("--provider-url", default=None, help="Override provider URL.")
    parser.add_argument("--token", default=None, help="Override bearer token.")
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--max-retries", type=int, default=None)
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 2

    try:
        config = _build_config(args)
    except ValueError as exc:
        print(f"error: invalid configuration: {exc}", file=sys.stderr)
        return 2
    sink, close = _make_writer(args.output)
    try:
        summary = asyncio.run(_run_pipeline(args.input, sink, config))
    finally:
        close()

    os.makedirs(os.path.dirname(os.path.abspath(args.summary)), exist_ok=True)
    with open(args.summary, "w", encoding="utf-8") as fh:
        json.dump(summary.to_json(), fh, indent=2)
        fh.write("\n")

    print(summary.render())
    print(f"\nresults -> {args.output}\nsummary -> {args.summary}")

    # Non-zero exit only when no domain reached a successful terminal enrichment
    # outcome. `no_match` is a valid business result, not an operational failure.
    if summary.unique_domains > 0 and (summary.succeeded + summary.no_match) == 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
