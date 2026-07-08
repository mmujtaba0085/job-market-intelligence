# Project brief: warm redesign of public-facing pages (Job Market Intelligence)

You have access to `templates/` (all subfolders). Here's the context you need.

## What this product is

Job Market Intelligence is a job-market analytics platform. It aggregates public job
postings from ~20 sources, normalizes and deduplicates them, extracts skills from
listings, and serves everything through a server-rendered BI web dashboard —
KPI cards, trend charts, skill/company/title drill-downs, filterable job search, plus
an admin backend for managing the data pipeline (not in scope for this task — see
below).

## The palette is already live — read `base.html` first

Unlike an earlier design pass, this is not a blank-slate color decision. The shared
layout, `templates/base.html`, has **already been updated** with a warm
amber/terracotta palette — read its `:root` and `[data-theme="dark"]` CSS blocks
before doing anything else. Every page you touch must use these exact custom
properties (`var(--accent)`, `var(--bg-surface)`, etc.) — do not invent new color
variables or hardcode hex values that duplicate what these already provide:

**Light mode** (`:root`): `--bg-base: #FBF6EF` (warm ivory), `--bg-surface: #FFFDF9`,
`--bg-elevated: #F5EBDD`, `--bg-hover: #F0E4D0`, `--border: #E8D9C3`,
`--text-primary: #3D2B1F` (warm brown, not black), `--text-secondary: #7A6A58`,
`--text-muted: #A69885`, `--accent: #C1552C` (terracotta), `--accent-hover: #A6431F`,
`--success: #4C7A3D` (warm sage), `--warning: #A6740A` (gold), `--danger: #C0392B`
(warm brick red), `--purple: #8B5A83` (muted plum, used sparingly), `--header-bg` is
a `linear-gradient(135deg, #C1552C 0%, #E08E4F 100%)` terracotta→amber gradient.

**Dark mode** (`[data-theme="dark"]`, toggled via the `jmi_theme` cookie and a
header button that already works): warm brown-black `--bg-base: #211812`, cream text
`--text-primary: #F5E9DC`, brighter amber `--accent: #E08E4F` for contrast — same
variable names, warm-shifted values.

Your job on every page: use `var(--whatever)`, never hardcode a hex value that
duplicates one of these. If a page needs a color that doesn't map to any existing
variable, that's a signal to ask rather than invent one silently.

## The task

Redesign these 13 templates so their content areas match the warm identity
`base.html`'s header/nav already establishes:

- `dashboard.html` — main BI dashboard (KPI cards, trend charts, geo/source breakdowns)
- `jobs_list.html` — searchable/filterable job listings
- `job_detail.html` — single job detail page
- `skills.html` — skills list
- `skills_intelligence.html` — skill drill-down (trends, co-occurrence, companies)
- `companies_intelligence.html` — company drill-down
- `titles_analytics.html` — normalized job-title analytics
- `metrics.html` — weekly metrics view
- `api_docs.html` — public API documentation page
- `auth/login.html` — sign-in page
- `auth/my_keys.html` — self-service API key management
- `auth/change_password.html` — self-service password change
- `index.html` — currently unreferenced by any route (`/` redirects straight to
  `/dashboard` in Python) — theme it for consistency in case it's ever revived, but
  it's not currently visitor-facing, so treat it as lowest priority of the 13

**Two scope notes:**

1. `auth/login.html` is the one page in this list that does **not** extend
   `base.html` — it's a standalone file with its own copy of the theme variables
   (currently still on the old cool-blue values) and its own working dark-mode
   toggle script, separate from `base.html`'s. Bring its variable values in line
   with `base.html`'s exactly, OR refactor it to extend `base.html` now that
   `base.html` has the right palette — your call, but tell us which you did and why.
2. Every other template in this list already extends `base.html` via
   `{% extends "base.html" %}` and already inherits the new warm header/nav
   automatically — you're only touching each page's `{% block content %}` (and
   `{% block extra_styles %}` for page-specific CSS), not the shared header/footer.

**Do not touch** any template outside this list of 13 — admin/pipeline/data-quality
tooling is intentionally staying in its current dense, utilitarian style.

## Brand mark

The header and login card currently use a plain 📊 emoji as the brand mark
(`.header-brand .brand-icon` in `base.html`, and its own copy in `login.html`).
**Propose a replacement** that fits the warm identity — your call on style (a small
inline SVG mark is one option, since emoji render inconsistently across platforms
and a custom mark can be exactly color-matched to the gradient, but you're not
limited to that). Show the proposed mark applied in your redesign of `login.html`
and describe precisely enough (SVG source, or exact emoji/character) that it can be
copied into `base.html`'s single shared instance afterward.

## Icons

Beyond the header's brand mark, these 13 pages lean heavily on emoji as ad-hoc icons
throughout — section headers, buttons, and status indicators (a non-exhaustive
sample: 📈/📉 for trend direction, 🌍/🏢 for geography/companies, 🔍 for search,
🔗 for links, 💼 for jobs, 📋 for lists, 🔒 for security, 💡/🧠 for insights, plus
several more scattered across `dashboard.html` and `skills_intelligence.html`
alone). This reads inconsistent and a little unpolished — emoji render differently
across OS/browser, so the same page looks different depending on who's viewing it.

Replace this pattern with something more practical and consistent. Given the "no
build step" constraint below, small inline SVG icons matching the palette are the
natural fit — an icon font or library would need an npm package or CDN link, neither
of which is available here. You don't need a 1:1 replacement for every emoji removed;
consolidate similar meanings (e.g. one consistent "trend" icon that flips for
up/down, rather than two unrelated-looking emoji) rather than mechanically swapping
each one. Where an emoji is purely decorative and conveys no real meaning — not a
status, category, or direction — it's fine to just drop it instead of replacing it
with an icon.

## Copy tone

Warmth extends to wording, not just visuals, but selectively:

- Friendlier headings where low-risk — e.g. login's "Sign In" heading could become
  "Welcome back" (the *button* should stay "Sign In" — action labels shouldn't get
  cute)
- Empty states: generic "No data" → something like "Nothing here yet — check back
  soon"
- Jargon-y titles can soften where they don't lose meaning — e.g. "BI Dashboard"
  doesn't have to stay that literal
- **Do not** touch security-relevant text — login/auth error messages ("Invalid
  username or password.", "Too many failed attempts...") must stay exactly as they
  are; that's not a place for warmth at the cost of clarity
- Propose copy alongside layout for each page — this isn't a mandate to rewrite
  every string, use judgment on what's worth softening

## Hard technical constraints — do not break these

- Flask + Jinja2 server-rendered app, no JS framework, no build step. Every template
  is a single self-contained `.html` file — inline `<style>`, vanilla JS only if
  needed. Don't introduce React/Vue/npm/webpack/Tailwind CDN/etc.
- Use the CSS custom properties already defined in `base.html` (see above) — don't
  hardcode colors that duplicate them
- Preserve every existing Jinja block structure, route-supplied template variable
  (anything referenced as `{{ variable_name }}`), form field `name=` attributes, and
  `id=` attributes that JavaScript in the page currently targets — you're
  restyling, not changing what data flows in or what the backend receives back
- Mobile-responsive — match or improve on the current responsiveness, don't regress it

## Deliverable

13 redesigned template files, a brand-mark proposal (shown applied in `login.html`,
described precisely enough to copy into `base.html`), and whatever icon set replaces
the emoji throughout — described precisely enough (SVG source, or a short list of
the icons used and where) that it can be reused consistently if we extend this
redesign to other pages later.
