"""Reading and de-duplicating the input domain list.

The CSV is read row by row, while unique normalized domains are retained for
run-wide de-duplication. Memory therefore scales with unique domain count rather
than raw row count.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from typing import Iterator, Optional

from .normalize import is_plausible_domain, normalize_domain


@dataclass
class InputRow:
    raw: str
    normalized: Optional[str]  # None if not a plausible domain
    valid: bool


@dataclass
class DedupedInput:
    """Unique valid domains plus everything needed to report faithfully."""

    unique_domains: list[str] = field(default_factory=list)
    occurrences: dict[str, int] = field(default_factory=dict)
    # First raw spelling seen for each normalised domain (for output display).
    first_raw: dict[str, str] = field(default_factory=dict)
    invalid_rows: list[str] = field(default_factory=list)
    total_rows: int = 0


def iter_rows(path: str) -> Iterator[InputRow]:
    """Yield one InputRow per source row.

    Accepts a headered CSV with a ``domain`` column, or a plain one-domain-per-
    line file. Blank lines are ignored (not counted as rows).
    """

    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        domain_idx = 0
        for i, cells in enumerate(reader):
            if not cells:
                continue
            values = [c.strip() for c in cells]
            if all(not v for v in values):
                continue
            if i == 0:
                # Detect a header row so we don't treat "domain" as a domain.
                lowered = [v.lower() for v in values]
                if "domain" in lowered:
                    domain_idx = lowered.index("domain")
                    continue
            raw = values[domain_idx] if domain_idx < len(values) else ""
            raw = raw.strip()
            if not raw:
                yield InputRow(raw="", normalized=None, valid=False)
                continue
            if is_plausible_domain(raw):
                yield InputRow(raw=raw, normalized=normalize_domain(raw), valid=True)
            else:
                yield InputRow(raw=raw, normalized=None, valid=False)


def load_deduped(path: str) -> DedupedInput:
    """Read the file and collapse to unique normalised domains."""

    result = DedupedInput()
    for row in iter_rows(path):
        result.total_rows += 1
        if not row.valid or row.normalized is None:
            result.invalid_rows.append(row.raw)
            continue
        dom = row.normalized
        if dom not in result.occurrences:
            result.occurrences[dom] = 0
            result.unique_domains.append(dom)
            result.first_raw[dom] = row.raw
        result.occurrences[dom] += 1
    return result
