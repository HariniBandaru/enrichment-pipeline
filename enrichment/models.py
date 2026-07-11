"""Domain models for pipeline inputs and outputs.

The output record is intentionally explicit: every input domain ends up with a
terminal ``outcome`` so nothing is silently dropped. Failures carry a machine
code *and* a human detail so an operator can act on the run summary.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


class Outcome(str, enum.Enum):
    SUCCESS = "success"
    NO_MATCH = "no_match"  # provider has no company for this domain
    INVALID_INPUT = "invalid_input"  # not a usable domain in the source file
    FAILED = "failed"  # exhausted retries / unexpected provider behaviour


@dataclass
class Company:
    """Normalised, provider-agnostic company record."""

    domain: str
    name: Optional[str] = None
    # Employee count is messy: sometimes an exact int, sometimes a band string.
    # We keep both so we never fabricate precision the provider didn't give us.
    employee_count: Optional[int] = None
    employee_range: Optional[str] = None
    industries: list[str] = field(default_factory=list)
    city: Optional[str] = None
    country: Optional[str] = None
    founded_year: Optional[int] = None
    annual_revenue_usd: Optional[int] = None


@dataclass
class Result:
    """Terminal record for a single input domain."""

    input_value: str  # exactly what appeared in the source file
    domain: Optional[str]  # normalised domain (None if uninterpretable)
    outcome: Outcome
    company: Optional[Company] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    attempts: int = 0
    occurrences: int = 1  # how many times this domain appeared in the input

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "input_value": self.input_value,
            "domain": self.domain,
            "outcome": self.outcome.value,
            "occurrences": self.occurrences,
            "attempts": self.attempts,
        }
        if self.company is not None:
            out["company"] = asdict(self.company)
        if self.error_code is not None:
            out["error_code"] = self.error_code
        if self.error_detail is not None:
            out["error_detail"] = self.error_detail
        return out
