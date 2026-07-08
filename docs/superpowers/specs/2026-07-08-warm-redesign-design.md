# Warm Redesign — Design Spec

## Context

The app's current visual design (`templates/base.html` and everything that extends
it) is a cool, utilitarian GitHub-style theme — blue accent, grays, `Inter` font.
Functional, but cold. The goal is to make the user-facing parts of the product feel
warm and welcoming, without compromising the density/usability of the internal admin
tooling that runs the pipeline.

Frontend architecture constraint (unchanged by this spec): server-rendered
Flask/Jinja2, no JS framework, no build step. Every template is a single
self-contained `.html` file. `base.html` is the shared layout — 24 of 25 templates
extend it via `{% extends "base.html" %}` (the exception is `auth/login.html`,
currently standalone). It defines the header/nav and a global CSS variable theme
(`:root { --bg-base: ...; --accent: ...; }` etc.) used by every page, including
every admin page.

## Scope

**Redesigned with the warm treatment** (13 templates + the shared layout):
`base.html`, `dashboard.html`, `jobs_list.html`, `job_detail.html`, `skills.html`,
`skills_intelligence.html`, `companies_intelligence.html`, `titles_analytics.html`,
`metrics.html`, `api_docs.html`, `auth/login.html`, `auth/my_keys.html`,
`auth/change_password.html`, `index.html` (currently unused/orphaned — left as-is
functionally, but themed for consistency if ever revived).

**Stays utilitarian/dense, content-wise** (12 templates): `jobs_quality_review.html`,
`admin_dashboard.html`, `admin_pipeline.html`, `admin_pipeline_logs.html`,
`admin_quality.html`, `admin_normalize.html`, `admin_normalize_titles.html`,
`admin_sheets_staging.html`, `admin_sheets_analytics.html`, `auth/admin_users.html`,
`auth/admin_api_keys.html`, `auth/admin_access_logs.html`.

Because every template — warm or utilitarian — extends the same `base.html`, the
**header/nav and global color variables apply everywhere**. Admin pages inherit the
warm header/nav automatically; only their content-area styling (tables, dense forms)
stays unchanged. This was a deliberate choice over maintaining two separate base
layouts: the same logged-in person moves between dashboard and admin, so a
inconsistent header between the two would read as broken, not intentional.

## Color system

Applied as new values for `base.html`'s existing CSS custom properties — no
variable renaming, so nothing that reads `var(--accent)` etc. needs to change.

**Light mode**

| Variable | Current | New |
|---|---|---|
| `--bg-base` | `#f6f8fa` | `#FBF6EF` |
| `--bg-surface` | `#ffffff` | `#FFFDF9` |
| `--bg-elevated` | `#f0f3f7` | `#F5EBDD` |
| `--bg-hover` | `#e8ecf1` | `#F0E4D0` |
| `--border` | `#d0d7de` | `#E8D9C3` |
| `--border-subtle` | `#e8ecf1` | `#F0E4D0` |
| `--text-primary` | `#1f2328` | `#3D2B1F` |
| `--text-secondary` | `#57606a` | `#7A6A58` |
| `--text-muted` | `#8c959f` | `#A69885` |
| `--accent` | `#0969da` | `#C1552C` |
| `--accent-hover` | `#0550ae` | `#A6431F` |
| `--accent-bg` | `rgba(9,105,218,0.07)` | `rgba(193,85,44,0.08)` |
| `--success` | `#1a7f37` | `#4C7A3D` |
| `--success-bg` | `rgba(26,127,55,0.08)` | `rgba(76,122,61,0.08)` |
| `--warning` | `#9a6700` | `#A6740A` |
| `--warning-bg` | `rgba(154,103,0,0.08)` | `rgba(166,116,10,0.08)` |
| `--danger` | `#cf222e` | `#C0392B` |
| `--danger-bg` | `rgba(207,34,46,0.08)` | `rgba(192,57,43,0.08)` |
| `--purple` | `#8250df` | `#8B5A83` |
| `--purple-bg` | `rgba(130,80,223,0.08)` | `rgba(139,90,131,0.08)` |
| `--header-bg` | `linear-gradient(135deg, #0550ae, #0969da)` | `linear-gradient(135deg, #C1552C, #E08E4F)` |
| `--header-border` | `rgba(255,255,255,0.15)` | unchanged |
| `--shadow-sm/md/lg` | neutral black shadows | unchanged (warm shadows read muddy; keep neutral) |

**Dark mode** — currently only `auth/login.html` defines a dark variant; this spec
extends dark mode to the whole app via the same `[data-theme="dark"]` pattern
`login.html` already uses, so the existing cookie-based toggle (`jmi_theme`,
`toggleTheme()`) keeps working unchanged.

| Variable | Current (login.html only) | New |
|---|---|---|
| `--bg-base` | `#0d1117` | `#211812` |
| `--bg-surface` | `#161b22` | `#2C2119` |
| `--bg-elevated` | `#21262d` | `#382A1F` |
| `--border` | `#30363d` | `#4A392C` |
| `--text-primary` | `#e6edf3` | `#F5E9DC` |
| `--text-secondary` | `#8b949e` | `#C4AD97` |
| `--accent` | `#58a6ff` | `#E08E4F` |
| `--accent-hover` | `#79b8ff` | `#EDA968` |
| `--accent-bg` | `rgba(88,166,255,0.1)` | `rgba(224,142,79,0.12)` |
| `--danger` | `#f85149` | `#E0685A` |
| `--danger-bg` | `rgba(248,81,73,0.12)` | `rgba(224,104,90,0.12)` |
| `--success` | `#3fb950` | `#8FB37A` |
| `--success-bg` | `rgba(63,185,80,0.12)` | `rgba(143,179,122,0.12)` |
| `--purple` | `#bc8cff` | `#C99BC0` |
| `--purple-bg` | `rgba(188,140,255,0.12)` | `rgba(201,155,192,0.12)` |

## Header / nav

Background switches from the cool blue gradient to the terracotta→amber gradient
above. Existing white nav-link treatment (`rgba(255,255,255,0.75)` text,
`rgba(255,255,255,0.12)` hover background) is kept as-is — it already has enough
contrast against a mid-tone gradient background and doesn't need to change.

## Brand mark

Currently a plain 📊 emoji in the header and login card. **Left to the design
tool's judgment** — not prescribed here. `base.html` ships with the emoji as a
placeholder until a mark is chosen from what the design tool proposes; swapping it
in afterward is a small follow-up edit, not part of this spec's implementation step.

## Copy tone

Extends to wording, not just visuals — but selectively, not a full copy rewrite:

- Friendlier headings where low-risk: e.g. login's "Sign In" → "Welcome back"
  (the *button* stays "Sign In" — action labels should stay unambiguous)
- Empty states: generic "No data" → something like "Nothing here yet — check back
  soon"
- Jargon-y titles get softened where they don't lose meaning: e.g. "BI Dashboard"
  could become something less jargon-forward
- **Explicitly excluded:** security-relevant text (login/auth error messages) stays
  precise and unchanged — that's not a place to introduce warmth at the cost of
  clarity
- The design prompt gives this guidance and a few examples; it does not mandate
  specific rewrites for every string — the design tool proposes copy alongside
  layout for each of the 13 templates

## Delivery approach

Two tracks, decided over three alternatives (full external delegation; fully
manual implementation; this hybrid) — the hybrid was chosen because `base.html`'s
color system is foundational (every page depends on it, admin included) and is
safer implemented directly than designed blind from a text prompt; the 13
individual page templates are lower-risk to hand to the design tool since they
don't affect shared infrastructure.

**Track 1 — direct implementation (this repo, by Claude Code):**
`base.html`'s CSS custom properties (light + dark) and header gradient are updated
in place, per the tables above. No other structural change to `base.html`. Brand
mark stays as the emoji placeholder.

**Track 2 — external design prompt (deliverable, not implemented here):**
A detailed prompt, scoped to the 13 public templates plus a brand-mark proposal,
referencing the real (already-updated) `base.html` as the working reference for
the palette rather than describing colors in prose. Produced after Track 1 is
verified working. Output from running that prompt through the design tool gets
integrated back into the codebase as a separate, later step — not part of this
spec's implementation.

## Testing / validation

- `python -m pytest tests -q` — this is a pure CSS/template-variable change with no
  route or logic changes, so the existing suite should be unaffected; run it to
  confirm no incidental breakage
- No automated visual testing available (no browser tool in this environment) —
  after Track 1 lands, local visual verification is the user's step before Track 2's
  prompt is written, since a broken or illegible palette shouldn't be the reference
  the design tool builds from

## Out of scope

The "users see repetitive postings from high-volume sources" problem raised
alongside this one is a data/ranking concern (how jobs are selected and ordered for
display), not a visual one. Explicitly not covered by this spec — a separate
brainstorming round.
