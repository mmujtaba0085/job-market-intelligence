# PJB Categorization Accuracy — Design Spec

## Goal

Give Pakistan Jobs Bank (PJB) postings a `field_category_id` trustworthy enough to distinguish IT from non-IT (engineering, medical, labor, education, etc.) reliably. This is the hard prerequisite for the Pakistan dashboard package — a "Top IT Jobs" widget, a "Top Hiring IT Companies" widget, and Pakistan+IT visitor auto-tagging (`docs/superpowers/specs/2026-07-16-pakistan-dashboard-package-design.md`, once written) — all of which need to trust that tag rather than just have it populated.

## Non-goals

- The geo-detection feature, the Top IT Jobs widget, and the Top Hiring IT Companies widget themselves — a separate, later spec, brainstormed after this ships (its design depends on knowing what a reliable PJB category tag actually looks like in practice).
- Re-litigating the general classifier's taxonomy/keyword choices beyond the word-boundary substring bug already fixed this session (commit `1205da4`). Other softer ambiguities noticed along the way (e.g. "Assistant Manager - Engineering & Maintenance" routing to `it.product` via a legitimate-but-imprecise `title_tokens:engineering manager` match) are real but out of scope here — they're taxonomy-precision questions, not the PJB-specific gap this spec addresses.
- Full re-classification of every source. Scoped specifically to Pakistan Jobs Bank (`source_name = 'Pakistan Jobs Bank'`, ~11,800 jobs as of this session).

## Grounding data (measured directly against production during brainstorming)

- 40 random real PJB titles were pulled and run through `classify_job()`. Clear non-IT titles (Vascular Surgeon, General Teacher, Chowkidar, Plumber, Structural Engineer) already classify correctly. Clear IT titles using standard English tech terms ("Network Administrator", ".Net Developer") mostly classify correctly, or produce correct evidence but fall just under the confidence threshold (e.g. `.99 confidence` needed `top_score >= 2.0 and confidence >= 0.62`; ".Net Developer" alone inside a longer title scored 0.599 - just short) and fall through to the existing Groq queue rather than being lost.
- ~10% of the sampled titles were ad-headline fallbacks ("Walled City of Lahore Authority Jobs May 2026 Apply Online Civil Engineers & Others WCLA Latest") rather than clean position titles - `_parse_date_page()`'s documented "rare" fallback (`titles = [...] or [ad_title]`, used when an ad's `<ul class="Positions">` is empty) is not actually rare in practice. This degrades ANY title-based classification approach equally, regardless of which is chosen below, since the "title" in these cases is marketing copy, not a job title.
- IT job titles are a small minority of PJB volume by design - the source deliberately covers every category, not just tech (see `pakistanjobsbank_collector.py`'s own module docstring). Low absolute IT-tagged-PJB-job counts are expected even with perfect categorization; the goal is precision (an IT tag actually means IT), not raising IT's share of PJB volume.

## Unresolved question this sub-project exists to answer

PJB's date-archive pages appear (from the collector's existing code comments and test fixtures) to group ads under section-header "===...===" divider rows - e.g. `=== ENGINEERING JOBS ===` - which `_parse_date_page()` currently discards entirely as noise. If real, and if labels are consistent enough to map to IT vs. non-IT, this is a stronger per-job category signal than any title-based approach, because it comes from the source's own editorial categorization rather than an inference. **This session could not verify it**: a WebFetch attempt and a direct `curl` fetch of two real date-archive pages both failed to reach the actual job-listing table (the page's calendar-navigation shell dominates the fetched content before real rows appear), and neither is proof the divider convention doesn't work in practice for the app's own collector, which has been ingesting real PJB jobs successfully all session using `requests.get()` with the same `_UA` header. Task 1 below resolves this with the app's own proven fetch path instead of an ad-hoc one.

## Task 1: Verify the divider-header signal (the spike)

A read-only diagnostic script, NOT a change to the collector's real parsing behavior. Purpose: get a real, quantified answer before committing engineering effort to Task 2.

**Method:**
1. Pick ~25 real dates spread across the already-backfilled window (`data/pakistanjobsbank_state.json`'s `oldest_date_crawled`..`newest_date_crawled` range gives the actual bounds already proven to have content - sample across that range, not arbitrary guesses).
2. For each date, fetch via the exact same mechanism `_fetch_date_page()` already uses (`requests.get()`, same `_UA`, same URL pattern) - this is the proven-working path, not a new one.
3. Parse with BeautifulSoup, walking `tr.job-ad` rows in document order (same selector `_parse_date_page()` already uses). For each row, capture whether its anchor text is a divider (`starts_with("===") and endswith("===")`) or a real ad. Do NOT discard dividers in this diagnostic - record each one's exact raw text, its position in the page, and how many real ad rows follow it before either the next divider or the end of the table.
4. Aggregate across all sampled dates: total divider rows found, count of distinct divider label strings (normalized: stripped of the `===` wrapper and whitespace), and for each distinct label, how many ad rows it preceded in total.
5. For the 5 most common distinct labels, manually spot-check 3-4 of the real ad titles that fell under each one - do they plausibly belong to that label's category (e.g. does "ENGINEERING JOBS" actually precede engineering-sounding titles), or does the label look orthogonal to job category (e.g. a newspaper name, a city, a date marker)?

**Report:** total dates sampled, total divider rows found (0 is a valid, actionable answer - it means the convention doesn't hold at the scale this app actually crawls, or applies only to some newspapers/date ranges), the distinct-label list with counts, and the spot-check findings. This is investigation only - no code changes to the collector itself, no commits to production behavior.

## Task 2+: build the chosen approach (scoped after Task 1 reports back)

Deliberately not fully specified here - the right shape depends on real evidence Task 1 doesn't have yet. Three candidate approaches, to be combined or chosen from based on Task 1's findings:

**A. Divider-header signal** (only viable if Task 1 finds real, consistent, category-mapping labels): capture each divider's label, propagate it to every ad row beneath it until the next divider, and map recognized labels to `field_category_id` values (or a subset - e.g. only labels that clearly say "IT"/"ENGINEERING"/"MEDICAL" map directly; anything else still falls through to B/C below).

**B. PJB-tuned keyword heuristics**: a supplementary keyword layer tuned to real Pakistani job-market phrasing rather than the general English-tech-jargon list - informed directly by ingested PJB data (BPS/PPS government pay-grade codes, common local IT titles already seen in the 40-title sample, etc.). Testable immediately against already-ingested data, independent of Task 1's outcome.

**C. Heavier Groq routing for this source**: PJB already falls through to the existing Groq fallback queue when local classification isn't confident (per the already-shipped classification pipeline, `docs/superpowers/specs/2026-07-13-hybrid-job-classification-pipeline-design.md`) - investigate whether PJB jobs are actually reaching that queue reliably, or getting stuck/skipped, and whether the prompt has enough context (title + minimal `raw_description`) to classify accurately. Independent of Task 1's outcome.

The most likely outcome is B+C as the reliable core (both independently testable now, neither depends on Task 1), with A folded in as an additional confidence signal only if Task 1 confirms it's real - never the sole mechanism, so nothing depends entirely on the piece that couldn't be verified during brainstorming.

## Definition of done

PJB jobs ingested going forward get a `field_category_id` at a precision bar to be set once Task 1's and a first Task-2-approach's real numbers are in (this spec deliberately does not invent a target accuracy percentage without evidence to back it) - concretely, IT-tagged PJB jobs should be IT on manual spot-check, and obviously-non-IT PJB titles (surgeon, teacher, chowkidar - already working today) must not regress.
