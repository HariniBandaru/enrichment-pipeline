"""Run summary aggregation — the operator-facing artefact.

The summary answers: how many succeeded, how many failed, and *why*, in enough
detail that someone on call could act (e.g. "80% RATE_LIMITED -> lower
concurrency" vs "mostly NO_MATCH -> input list quality").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from .models import Outcome, Result


@dataclass
class RunSummary:
    total_input_rows: int = 0
    unique_domains: int = 0
    duplicate_rows: int = 0
    invalid_input: int = 0
    succeeded: int = 0
    no_match: int = 0
    failed: int = 0
    failure_reasons: Counter = field(default_factory=Counter)
    elapsed_seconds: float = 0.0

    def record(self, result: Result) -> None:
        if result.outcome is Outcome.SUCCESS:
            self.succeeded += 1
        elif result.outcome is Outcome.NO_MATCH:
            self.no_match += 1
        elif result.outcome is Outcome.INVALID_INPUT:
            self.invalid_input += 1
        elif result.outcome is Outcome.FAILED:
            self.failed += 1
            self.failure_reasons[result.error_code or "UNKNOWN"] += 1

    def to_json(self) -> dict[str, Any]:
        processed = self.succeeded + self.no_match + self.failed
        return {
            "total_input_rows": self.total_input_rows,
            "unique_domains": self.unique_domains,
            "duplicate_rows": self.duplicate_rows,
            "invalid_input": self.invalid_input,
            "enrichment_attempted": processed,
            "succeeded": self.succeeded,
            "no_match": self.no_match,
            "failed": self.failed,
            "failure_reasons": dict(self.failure_reasons.most_common()),
            "success_rate": round(self.succeeded / processed, 4) if processed else 0.0,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "throughput_domains_per_sec": (
                round(processed / self.elapsed_seconds, 1)
                if self.elapsed_seconds > 0
                else 0.0
            ),
        }

    def render(self) -> str:
        j = self.to_json()
        lines = [
            "Enrichment run summary",
            "======================",
            f"  input rows        : {j['total_input_rows']}",
            f"  unique domains    : {j['unique_domains']}",
            f"  duplicate rows    : {j['duplicate_rows']}",
            f"  invalid input     : {j['invalid_input']}",
            "  --",
            f"  succeeded         : {j['succeeded']}",
            f"  no_match          : {j['no_match']}",
            f"  failed            : {j['failed']}",
            f"  success rate      : {j['success_rate'] * 100:.1f}%",
        ]
        if j["failure_reasons"]:
            lines.append("  failure reasons:")
            for code, count in j["failure_reasons"].items():
                lines.append(f"      {code:<24} {count}")
        lines.append(
            f"  elapsed           : {j['elapsed_seconds']}s "
            f"({j['throughput_domains_per_sec']} domains/s)"
        )
        return "\n".join(lines)
