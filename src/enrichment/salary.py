"""Structured and text salary parsing shared by collectors."""

from __future__ import annotations

import re

from src.enrichment.auto_enrich import extract_salary

_PERIOD_PATTERNS = [
    (r"\b(per\s+year|annual(?:ly)?|/yr|p\.?a\.?)\b", "year"),
    (r"\b(per\s+month|monthly|/mo)\b", "month"),
    (r"\b(per\s+week|weekly|/wk)\b", "week"),
    (r"\b(per\s+day|daily|/day)\b", "day"),
    (r"\b(per\s+hour|hourly|/hr)\b", "hour"),
]


def parse_salary(
    raw: str | None,
    salary_min=None,
    salary_max=None,
    currency: str | None = None,
    period: str | None = None,
    estimated: bool = False,
) -> dict:
    text = (raw or "").strip()
    parsed_min, parsed_max, parsed_currency = extract_salary(text)

    def number(value, fallback):
        try:
            return float(value) if value not in (None, "") else fallback
        except (TypeError, ValueError):
            return fallback

    normalized_period = (period or "").strip().lower() or None
    if not normalized_period:
        for pattern, value in _PERIOD_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                normalized_period = value
                break

    return {
        "salary_min": number(salary_min, parsed_min),
        "salary_max": number(salary_max, parsed_max),
        "currency": (currency or parsed_currency or "").upper() or None,
        "salary_raw": text or None,
        "salary_period": normalized_period,
        "salary_is_estimated": bool(estimated),
    }
