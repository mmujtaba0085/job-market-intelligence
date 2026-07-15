# PJB Divider-Signal Spike Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only diagnostic script that samples ~25 real Pakistan Jobs Bank date-archive pages, records every section-divider row's label alongside the ad titles that follow it, and reports whether the divider convention is real, consistent, and category-meaningful enough to use as a signal in a later categorization task.

**Architecture:** A standalone one-off script (`scripts/pjb_divider_signal_spike.py`) with its parsing/aggregation logic factored into pure, unit-testable functions, plus a `main()` driver that does the actual network fetching and prints a findings report to stdout. No production code, database, or collector state is touched.

**Tech Stack:** Python 3.12, `requests`, `beautifulsoup4` (both already dependencies of this repo — same libraries `src/collectors/pakistanjobsbank_collector.py` uses).

## Global Constraints

- Never write "Co-Authored-By: Claude" into any commit in this repo.
- Every pytest invocation must use `--basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp` (Windows temp-dir `PermissionError` workaround).
- This is a **read-only diagnostic**. The script must never write to `data/pakistanjobsbank_state.json`, never call any `src.storage.db` write function, and must not be imported by or wired into `src/orchestrator.py` or any real ingestion path. It is a one-off, run by hand, not scheduled.
- Reuse the proven-working fetch mechanism: same URL pattern (`f"{_BASE_URL}/Jobs-in-Pakistan/{day.isoformat()}/"`), same `_UA` string, same `requests.get()` call shape as `src/collectors/pakistanjobsbank_collector.py::_fetch_date_page()`. This session's own ad-hoc fetch attempts (a WebFetch call and a plain `curl`) both failed to reach real listing content; the collector's own mechanism has been ingesting real PJB jobs successfully all session, so this plan copies that mechanism rather than inventing a new one.
- Sample dates come from the real, current crawl bounds: `oldest_date_crawled: "2025-10-09"`, `newest_date_crawled: "2026-07-15"` (read directly from the production VPS's `data/pakistanjobsbank_state.json` during planning — this is real state, not a placeholder).
- Deliverable is a findings **report** (label vocabulary, counts, spot-check samples), not a change to production behavior. Nothing in this task modifies `src/collectors/pakistanjobsbank_collector.py`.

---

### Task 1: Divider-signal diagnostic script

**Files:**
- Create: `scripts/pjb_divider_signal_spike.py`
- Test: `tests/test_pjb_divider_signal_spike.py`

**Interfaces:**
- Produces: `_extract_rows(html: str) -> list[dict]` — each dict is either `{"type": "divider", "label": str}` or `{"type": "ad", "title": str}`, in document order.
- Produces: `_aggregate_by_label(rows: list[dict]) -> dict[str, dict]` — maps a label (or the literal string `"(none)"` for ads with no preceding divider) to `{"count": int, "sample_titles": list[str]}` (capped at 5 sample titles per label).
- Produces: `main()` — the live-fetch driver; not unit tested (I/O), same convention as this codebase's existing `_fetch_*` collector methods.

- [ ] **Step 1: Write the failing tests for `_extract_rows`**

Create `tests/test_pjb_divider_signal_spike.py`:

```python
"""
tests/test_pjb_divider_signal_spike.py
─────────────────────────────────────────
Unit tests for the pure parsing/aggregation functions in
scripts/pjb_divider_signal_spike.py (the PJB categorization spike - see
docs/superpowers/specs/2026-07-16-pjb-categorization-design.md, Task 1).
Covers only the parsing/aggregation logic against synthetic HTML - the
live-fetch driver (main()) is I/O and isn't unit tested, matching this
codebase's existing convention (src/collectors/*.py's own _fetch_* methods
aren't unit tested either, only their parsing logic is).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.pjb_divider_signal_spike import _aggregate_by_label, _extract_rows


def _row_html(anchor_text: str) -> str:
    return f'<tr class="job-ad"><td><a href="/Ad/1/">{anchor_text}</a></td></tr>'


def test_extract_rows_classifies_divider_and_ad_rows():
    html = f"<table>{_row_html('=== ENGINEERING JOBS ===')}{_row_html('Software Engineer')}</table>"
    rows = _extract_rows(html)
    assert rows == [
        {"type": "divider", "label": "ENGINEERING JOBS"},
        {"type": "ad", "title": "Software Engineer"},
    ]


def test_extract_rows_skips_rows_with_no_anchor_or_empty_text():
    html = '<table><tr class="job-ad"><td>no anchor here</td></tr></table>'
    assert _extract_rows(html) == []


def test_extract_rows_preserves_document_order_across_multiple_dividers():
    html = (
        "<table>"
        + _row_html("=== A ===")
        + _row_html("Job 1")
        + _row_html("=== B ===")
        + _row_html("Job 2")
        + "</table>"
    )
    rows = _extract_rows(html)
    assert [r["type"] for r in rows] == ["divider", "ad", "divider", "ad"]


def test_aggregate_by_label_groups_ads_under_most_recent_divider():
    rows = [
        {"type": "divider", "label": "ENGINEERING JOBS"},
        {"type": "ad", "title": "Civil Engineer"},
        {"type": "ad", "title": "Structural Engineer"},
        {"type": "divider", "label": "MEDICAL JOBS"},
        {"type": "ad", "title": "Nurse"},
    ]
    result = _aggregate_by_label(rows)
    assert result["ENGINEERING JOBS"]["count"] == 2
    assert result["ENGINEERING JOBS"]["sample_titles"] == ["Civil Engineer", "Structural Engineer"]
    assert result["MEDICAL JOBS"]["count"] == 1
    assert result["MEDICAL JOBS"]["sample_titles"] == ["Nurse"]


def test_aggregate_by_label_uses_none_bucket_for_ads_before_any_divider():
    rows = [
        {"type": "ad", "title": "Chowkidar"},
        {"type": "divider", "label": "IT JOBS"},
        {"type": "ad", "title": "Developer"},
    ]
    result = _aggregate_by_label(rows)
    assert result["(none)"]["count"] == 1
    assert result["(none)"]["sample_titles"] == ["Chowkidar"]
    assert result["IT JOBS"]["count"] == 1


def test_aggregate_by_label_caps_sample_titles_at_five():
    rows = [{"type": "divider", "label": "X"}] + [
        {"type": "ad", "title": f"Job {i}"} for i in range(8)
    ]
    result = _aggregate_by_label(rows)
    assert result["X"]["count"] == 8
    assert len(result["X"]["sample_titles"]) == 5
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pjb_divider_signal_spike.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: FAIL with `ModuleNotFoundError: No module named 'scripts.pjb_divider_signal_spike'`

- [ ] **Step 3: Write the implementation**

Create `scripts/pjb_divider_signal_spike.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_pjb_divider_signal_spike.py -v --basetemp=C:/Users/moham/AppData/Local/Temp/claude/d--vs-code-Job-Market-Intelligence/a7f807f9-3c99-49f5-9532-b27cedca2513/scratchpad/pytest-basetemp`
Expected: PASS (6 tests)

- [ ] **Step 5: Commit**

```bash
git add scripts/pjb_divider_signal_spike.py tests/test_pjb_divider_signal_spike.py
git commit -m "feat: add PJB divider-signal diagnostic spike script"
```

- [ ] **Step 6: Run the live spike and capture its output**

Run: `python scripts/pjb_divider_signal_spike.py`

This makes ~25 real HTTP requests to pakistanjobsbank.com (one per sampled date, 1 second apart, so this takes roughly 30-60 seconds including page transfer time). Save the full stdout output — it is Task 1's actual deliverable (the findings report), not the script itself. Do not commit the output as a file; report it back in full as part of this task's completion report so the controller can read the real findings and decide Task 2's approach per the spec's decision framework.

**Verification:** the printed report shows, at minimum: how many of the 25 sampled dates actually had at least one divider row, the total count of distinct labels found, and per-label ad-title counts with sample titles. If zero divider rows were found across all 25 dates, that is a complete, valid, actionable finding (Approach A from the spec is ruled out) - report it exactly as plainly as a positive finding would be, with the raw numbers, not a guess about why.

---

## Next steps (not part of this plan)

Task 2 (building the chosen categorization approach) is deliberately not planned here — per `docs/superpowers/specs/2026-07-16-pjb-categorization-design.md`, its shape depends on this spike's real findings. Once Task 1's report is in hand, return to the spec's decision framework (Approach A/B/C) and run a short follow-up planning pass for whichever approach (or combination) the findings support.
