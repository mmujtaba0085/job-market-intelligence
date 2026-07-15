"""
tests/test_pakistanjobsbank_divider_rows.py
──────────────────────────────────────────────
Regression test for a real bug: _parse_date_page() only validated that a
`tr.job-ad` row had cells, an anchor, and non-empty anchor text before
treating it as a job ad. The site marks section dividers/category headers
with a "===...===" text convention (already filtered out of position <li>
text), but that same check was never applied to the row's own anchor text -
a divider row (no real position list) fell through to using the divider
text itself as a job title.
"""
from datetime import date

from src.collectors.pakistanjobsbank_collector import PakistanJobsBankCollector


def _make_collector():
    """__new__() bypasses BaseCollector.__init__ (no config/rate-limit
    state, no HTTP client) - fine for this file, since these tests only
    exercise _parse_date_page()'s row-filtering logic, not real fetching.
    _wait() and _fetch_ad_detail() are stubbed out (no-op / dummy values)
    so a normal (non-divider) row doesn't need real network access or a
    real rate-limit clock to parse correctly."""
    collector = PakistanJobsBankCollector.__new__(PakistanJobsBankCollector)
    collector._wait = lambda: None
    collector._fetch_ad_detail = lambda ad_url: (None, None)
    return collector


def _row_html(anchor_text: str, positions: list[str] | None = None) -> str:
    positions = positions if positions is not None else ["Software Engineer", "QA Engineer"]
    position_lis = "".join(f"<li>{p}</li>" for p in positions)
    return f"""
    <tr class="job-ad">
        <td>
            <div>05-Jul-2026 (Sunday) - Nawa-i-Waqt</div>
            <div>in Sahiwal, Punjab</div>
            <a href="/Ad/12345/">{anchor_text}</a>
        </td>
        <td>
            <ul class="Positions">{position_lis}</ul>
        </td>
    </tr>
    """


def test_normal_ad_row_still_parses_correctly():
    html = f"<html><body><table>{_row_html('Bureau Veritas Careers')}</table></body></html>"
    collector = _make_collector()
    jobs = collector._parse_date_page(html, date(2026, 7, 5))
    assert len(jobs) == 2
    assert jobs[0].parsed_fields["title"] == "Software Engineer"
    assert jobs[1].parsed_fields["title"] == "QA Engineer"


def test_divider_row_is_skipped_not_stored_as_a_job():
    divider_row = _row_html("=== ENGINEERING JOBS ===", positions=[])
    real_row = _row_html("Bureau Veritas Careers", positions=["Software Engineer"])
    html = f"<html><body><table>{divider_row}{real_row}</table></body></html>"
    collector = _make_collector()
    jobs = collector._parse_date_page(html, date(2026, 7, 5))
    assert len(jobs) == 1
    assert jobs[0].parsed_fields["title"] == "Software Engineer"
    titles = [j.parsed_fields["title"] for j in jobs]
    assert not any("===" in t for t in titles)


def test_divider_row_with_no_positions_produces_zero_jobs():
    html = f"<html><body><table>{_row_html('=== CATEGORY HEADER ===', positions=[])}</table></body></html>"
    collector = _make_collector()
    jobs = collector._parse_date_page(html, date(2026, 7, 5))
    assert jobs == []


def test_divider_text_inside_positions_list_still_filtered_pre_existing_behavior():
    """Pre-existing, unrelated to this fix: a "===...===" <li> inside an
    otherwise-real ad's position list was already correctly excluded."""
    html = f"""<html><body><table>
    <tr class="job-ad">
        <td>
            <div>05-Jul-2026 (Sunday) - Nawa-i-Waqt</div>
            <div>in Sahiwal, Punjab</div>
            <a href="/Ad/12345/">Bureau Veritas Careers</a>
        </td>
        <td>
            <ul class="Positions">
                <li>=== NEW POSITIONS ===</li>
                <li>Software Engineer</li>
            </ul>
        </td>
    </tr>
    </table></body></html>"""
    collector = _make_collector()
    jobs = collector._parse_date_page(html, date(2026, 7, 5))
    assert len(jobs) == 1
    assert jobs[0].parsed_fields["title"] == "Software Engineer"
