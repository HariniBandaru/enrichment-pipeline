"""Normalisation of the provider's inconsistent v2 payloads.

The provider (per API.md and observed behaviour) returns the same logical field
in several shapes. We centralise every quirk here so the rest of the pipeline
deals only with a clean :class:`~enrichment.models.Company`.

Observed variety:
- ``employeeCount``: number (1200), numeric string ("3852"), banded string
  ("1,000-5,000"), or null.
- ``industry``: a single string or an array of strings.
- ``location``: an object {city, country} or a plain city string.
- ``foundedYear`` / ``annualRevenueUsd``: sometimes omitted.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from .models import Company

_BAND_RE = re.compile(r"^\s*[\d,]+\s*[-–]\s*[\d,]+\s*$")
_INT_RE = re.compile(r"^\s*[\d,]+\s*$")


def normalize_domain(value: str) -> str:
    """Lower-case, trim, and strip an obvious scheme/path if present."""

    d = value.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = d.split("/", 1)[0]
    return d.strip()


def is_plausible_domain(value: str) -> bool:
    """Cheap structural check: at least one dot and no whitespace.

    We deliberately keep this permissive — the provider is the source of truth
    for whether a company exists (NO_MATCH). We only reject input that clearly
    can't be a domain (e.g. "not a domain") so it's flagged rather than sent.
    """

    if not value or any(c.isspace() for c in value):
        return False
    if "." not in value:
        return False
    label = value.split(".")
    return all(part for part in label)


def normalize_employee_count(raw: Any) -> tuple[Optional[int], Optional[str]]:
    """Return (exact_count, band_label).

    - Exact int or numeric string -> (int, None)
    - Banded string ("1,000-5,000") -> (None, "1,000-5,000") — we do NOT invent
      a midpoint; the band is the honest representation.
    - null / unparseable -> (None, None)
    """

    if raw is None:
        return None, None
    if isinstance(raw, bool):  # guard: bool is an int subclass
        return None, None
    if isinstance(raw, int):
        return raw, None
    if isinstance(raw, float):
        return int(raw), None
    if isinstance(raw, str):
        s = raw.strip()
        if not s:
            return None, None
        if _INT_RE.match(s):
            return int(s.replace(",", "")), None
        if _BAND_RE.match(s):
            return None, s
        # Unknown textual form: preserve it as a band-ish label rather than drop.
        return None, s
    return None, None


def normalize_industries(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw] if raw.strip() else []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    return [str(raw)]


def normalize_location(raw: Any) -> tuple[Optional[str], Optional[str]]:
    """Return (city, country)."""

    if raw is None:
        return None, None
    if isinstance(raw, str):
        s = raw.strip()
        return (s or None), None
    if isinstance(raw, dict):
        city = raw.get("city")
        country = raw.get("country")
        city = city.strip() if isinstance(city, str) and city.strip() else None
        country = (
            country.strip() if isinstance(country, str) and country.strip() else None
        )
        return city, country
    return None, None


def _opt_int(raw: Any) -> Optional[int]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(raw)
    if isinstance(raw, str) and _INT_RE.match(raw.strip()):
        return int(raw.strip().replace(",", ""))
    return None


def normalize_company(domain: str, data: dict[str, Any]) -> Company:
    """Build a clean Company from a v2 ``data`` object.

    We trust ``domain`` (the value we queried) over ``data.domain`` so a record
    is always attributable to its input even if the provider echoes something
    slightly different.
    """

    count, band = normalize_employee_count(data.get("employeeCount"))
    city, country = normalize_location(data.get("location"))
    return Company(
        domain=domain,
        name=(data.get("name") or None),
        employee_count=count,
        employee_range=band,
        industries=normalize_industries(data.get("industry")),
        city=city,
        country=country,
        founded_year=_opt_int(data.get("foundedYear")),
        annual_revenue_usd=_opt_int(data.get("annualRevenueUsd")),
    )
