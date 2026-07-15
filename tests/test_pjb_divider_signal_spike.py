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
