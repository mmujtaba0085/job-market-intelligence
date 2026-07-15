"""
scripts/pjb_divider_signal_spike.py
──────────────────────────────────────
Read-only diagnostic for the PJB categorization spike - see
docs/superpowers/specs/2026-07-16-pjb-categorization-design.md, Task 1.

Pakistan Jobs Bank's date-archive pages appear (from src/collectors/
pakistanjobsbank_collector.py's own code comments and test fixtures) to
group ads under section-header "===...===" divider rows, which the real
collector's _parse_date_page() currently discards entirely as noise. This
script does NOT discard them - it walks the same tr.job-ad rows in
document order, keeps every divider's raw label, and reports how many ad
titles fall under each one, so we have real evidence (not a guess) before
deciding whether to build a categorization approach around this signal.

This is a one-off, run by hand - NOT wired into ingestion, and never
writes to data/pakistanjobsbank_state.json or any database. It reuses the
exact fetch mechanism (URL pattern, User-Agent, requests.get() call shape)
that src/collectors/pakistanjobsbank_collector.py::_fetch_date_page() uses,
since that mechanism has been ingesting real PJB jobs successfully all
session, while this session's own ad-hoc WebFetch/curl attempts against
the same site both failed to reach real listing content past its
calendar-navigation shell.

Usage:
    python scripts/pjb_divider_signal_spike.py
"""
from __future__ import annotations

import sys
import time
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import requests
from bs4 import BeautifulSoup

_BASE_URL = "https://www.pakistanjobsbank.com"
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"
_TIMEOUT = 15
_SAMPLE_COUNT = 25
_REQUEST_DELAY_SECONDS = 1.0  # matches BaseCollector's default 60/min rate limit

# Real crawl bounds from data/pakistanjobsbank_state.json on the production
# VPS as of this spike's planning - not a placeholder, the actual range
# this app has already successfully backfilled.
_OLDEST_CRAWLED = date(2025, 10, 9)
_NEWEST_CRAWLED = date(2026, 7, 15)


def _extract_rows(html: str) -> list[dict]:
    """
    Walk tr.job-ad rows in document order, classifying each as a divider
    (anchor text wrapped in "===...===") or a real ad. Mirrors the row
    selection src/collectors/pakistanjobsbank_collector.py::_parse_date_page()
    uses, but keeps dividers instead of skipping them.
    """
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    for row in soup.select("tr.job-ad"):
        anchor = row.find("a", href=True)
        if not anchor:
            continue
        text = anchor.get_text(strip=True)
        if not text:
            continue
        if text.startswith("===") and text.endswith("==="):
            label = text.strip("=").strip()
            rows.append({"type": "divider", "label": label})
        else:
            rows.append({"type": "ad", "title": text})
    return rows


def _aggregate_by_label(rows: list[dict]) -> dict[str, dict]:
    """
    Group ad titles under whichever divider label most recently preceded
    them in document order. Ads appearing before any divider on the page
    are grouped under the literal label "(none)".
    """
    result: dict[str, dict] = {}
    current_label = "(none)"
    for row in rows:
        if row["type"] == "divider":
            current_label = row["label"]
        else:
            bucket = result.setdefault(current_label, {"count": 0, "sample_titles": []})
            bucket["count"] += 1
            if len(bucket["sample_titles"]) < 5:
                bucket["sample_titles"].append(row["title"])
    return result


def _fetch_date_html(day: date) -> str | None:
    url = f"{_BASE_URL}/Jobs-in-Pakistan/{day.isoformat()}/"
    try:
        resp = requests.get(url, headers={"User-Agent": _UA}, timeout=_TIMEOUT)
    except requests.RequestException as exc:
        print(f"  [{day}] request error: {exc}")
        return None
    if resp.status_code != 200:
        print(f"  [{day}] HTTP {resp.status_code}")
        return None
    return resp.text


def _sample_dates(oldest: date, newest: date, count: int) -> list[date]:
    span_days = (newest - oldest).days
    return sorted({
        oldest + timedelta(days=int(i * span_days / (count - 1)))
        for i in range(count)
    })


def main() -> None:
    sample_dates = _sample_dates(_OLDEST_CRAWLED, _NEWEST_CRAWLED, _SAMPLE_COUNT)
    print("=" * 70)
    print("PJB DIVIDER-SIGNAL SPIKE")
    print(f"Sampling {len(sample_dates)} date(s) from {_OLDEST_CRAWLED} to {_NEWEST_CRAWLED}")
    print("=" * 70)

    combined: dict[str, dict] = {}
    total_dividers = 0
    dates_with_dividers = 0
    dates_fetched = 0

    for day in sample_dates:
        html = _fetch_date_html(day)
        time.sleep(_REQUEST_DELAY_SECONDS)
        if html is None:
            continue
        dates_fetched += 1

        rows = _extract_rows(html)
        divider_count = sum(1 for r in rows if r["type"] == "divider")
        total_dividers += divider_count
        if divider_count:
            dates_with_dividers += 1
        print(f"  [{day}] {len(rows)} row(s), {divider_count} divider(s)")

        by_label = _aggregate_by_label(rows)
        for label, bucket in by_label.items():
            merged = combined.setdefault(label, {"count": 0, "sample_titles": []})
            merged["count"] += bucket["count"]
            for t in bucket["sample_titles"]:
                if len(merged["sample_titles"]) < 5 and t not in merged["sample_titles"]:
                    merged["sample_titles"].append(t)

    print("\n" + "=" * 70)
    print("REPORT")
    print("=" * 70)
    print(f"Dates sampled: {len(sample_dates)}  (successfully fetched: {dates_fetched})")
    print(f"Dates with at least one divider row: {dates_with_dividers}")
    print(f"Total divider rows found: {total_dividers}")
    print(f"Distinct labels (including '(none)' for ads with no preceding divider): {len(combined)}")
    print()
    for label, bucket in sorted(combined.items(), key=lambda kv: -kv[1]["count"]):
        print(f"  {label!r}: {bucket['count']} ad(s)")
        for t in bucket["sample_titles"]:
            print(f"      - {t}")


if __name__ == "__main__":
    main()
