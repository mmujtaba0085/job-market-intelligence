# Warm Redesign (Track 1 + Track 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reskin `templates/base.html` (the shared layout used by all 25 other templates) with the warm amber/terracotta palette from the design spec, add working dark-mode support, then produce a detailed design prompt for an external tool to redesign the 13 public-facing page templates.

**Architecture:** `base.html` is a single self-contained Jinja2 template with an inline `<style>` block defining CSS custom properties under `:root` (light mode). This plan updates those property *values* only (no renaming), adds a parallel `[data-theme="dark"]` block, and wires up a working theme toggle. No other template is touched by this plan — the 13 public templates are Track 2's job, delivered as a prompt document, not implemented here.

**Tech Stack:** Flask + Jinja2, no JS framework, no build step, no CSS preprocessor — plain CSS custom properties and vanilla JS.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-07-08-warm-redesign-design.md` — read it before starting if context is needed
- CSS custom property *names* in `base.html` must not change — only values (things like `var(--accent)` are used throughout `base.html` and would need updating everywhere if names changed; they don't need to)
- No structural change to `base.html` beyond: color values, header gradient, and (see note below) dark-mode support — nothing else in the file's layout/markup changes
- Brand mark stays as the 📊 emoji placeholder — not decided in this plan, left to Track 2
- `python -m pytest tests -q` must show the same pass/fail counts after each task as before this plan started (124 passed, 1 pre-existing unrelated failure — `test_login_rejects_external_next_target`, not touched by this work)
- No browser/visual-testing tool is available in this environment — verification is via grep (values present/absent) and a scripted Flask test-client request, not visual inspection
- **Spec clarification found during planning:** the spec's dark-mode color table only lists the variables `auth/login.html` already defines standalone (`--bg-base`, `--bg-surface`, `--bg-elevated`, `--border`, `--text-primary`, `--text-secondary`, `--accent`, `--accent-hover`, `--accent-bg`, `--danger`, `--danger-bg`, `--success`, `--success-bg`, `--purple`, `--purple-bg`) — because that table was written against login.html's smaller variable set, not base.html's fuller one. `base.html` also defines `--bg-hover`, `--border-subtle`, `--text-muted`, `--warning`, `--warning-bg` which have no dark-mode value in the spec. Task 2 below derives dark-mode values for these five, following the same lightening/warming relationship the spec's other dark values establish relative to their light counterparts. This is documented inline in Task 2 rather than treated as a silent gap.
- **Spec clarification found during planning:** the spec assumes "the existing cookie-based toggle... keeps working unchanged," but only `auth/login.html` currently has a working toggle (cookie `jmi_theme`, `toggleTheme()`) — `base.html` has zero dark-mode support today (hardcoded `data-theme="light"`, and a CSS class `.theme-toggle-unused` that is dead code — a leftover `.theme-toggle:hover` selector references a class name that doesn't exist anywhere in the file, confirming a toggle was removed from `base.html` at some point in the past). Task 2 adds real dark-mode support to `base.html` for the first time, reusing login.html's exact cookie name and JS pattern so behavior is consistent app-wide once this lands.

---

### Task 1: Update light-mode color variables and header gradient in `base.html`

**Files:**
- Modify: `templates/base.html:14-40` (the `:root` block)

**Interfaces:**
- Produces: the new value for every `--*` custom property under `:root`, which Task 2 and Track 2's prompt both reference by name

- [ ] **Step 1: Replace the `:root` block**

Find this block (lines 14-40):

```css
        :root {
            --bg-base:      #f6f8fa;
            --bg-surface:   #ffffff;
            --bg-elevated:  #f0f3f7;
            --bg-hover:     #e8ecf1;
            --border:       #d0d7de;
            --border-subtle:#e8ecf1;
            --text-primary: #1f2328;
            --text-secondary:#57606a;
            --text-muted:   #8c959f;
            --accent:       #0969da;
            --accent-hover: #0550ae;
            --accent-bg:    rgba(9,105,218,0.07);
            --success:      #1a7f37;
            --success-bg:   rgba(26,127,55,0.08);
            --warning:      #9a6700;
            --warning-bg:   rgba(154,103,0,0.08);
            --danger:       #cf222e;
            --danger-bg:    rgba(207,34,46,0.08);
            --purple:       #8250df;
            --purple-bg:    rgba(130,80,223,0.08);
            --shadow-sm:    0 1px 3px rgba(31,35,40,0.12);
            --shadow-md:    0 4px 12px rgba(31,35,40,0.12);
            --shadow-lg:    0 8px 24px rgba(31,35,40,0.12);
            --header-bg:    linear-gradient(135deg, #0550ae 0%, #0969da 100%);
            --header-border: rgba(255,255,255,0.15);
        }
```

Replace with:

```css
        :root {
            --bg-base:      #FBF6EF;
            --bg-surface:   #FFFDF9;
            --bg-elevated:  #F5EBDD;
            --bg-hover:     #F0E4D0;
            --border:       #E8D9C3;
            --border-subtle:#F0E4D0;
            --text-primary: #3D2B1F;
            --text-secondary:#7A6A58;
            --text-muted:   #A69885;
            --accent:       #C1552C;
            --accent-hover: #A6431F;
            --accent-bg:    rgba(193,85,44,0.08);
            --success:      #4C7A3D;
            --success-bg:   rgba(76,122,61,0.08);
            --warning:      #A6740A;
            --warning-bg:   rgba(166,116,10,0.08);
            --danger:       #C0392B;
            --danger-bg:    rgba(192,57,43,0.08);
            --purple:       #8B5A83;
            --purple-bg:    rgba(139,90,131,0.08);
            --shadow-sm:    0 1px 3px rgba(31,35,40,0.12);
            --shadow-md:    0 4px 12px rgba(31,35,40,0.12);
            --shadow-lg:    0 8px 24px rgba(31,35,40,0.12);
            --header-bg:    linear-gradient(135deg, #C1552C 0%, #E08E4F 100%);
            --header-border: rgba(255,255,255,0.15);
        }
```

(`--shadow-*` and `--header-border` are unchanged, per the spec — warm-tinted shadows read muddy, and the header border works fine as-is.)

- [ ] **Step 2: Verify old cool-blue values are gone**

Run: `grep -c "#0969da\|#0550ae\|#f6f8fa" templates/base.html`
Expected: `0`

- [ ] **Step 3: Verify new warm values are present**

Run: `grep -c "#C1552C\|#FBF6EF\|#E08E4F" templates/base.html`
Expected: `3` or more (each appears at least once; `#C1552C` appears twice — `--accent` and inside `--header-bg`)

- [ ] **Step 4: Confirm the app still renders this page correctly**

Run this from the repo root (adjust nothing — it logs in via a direct session write, bypassing the login form, since we only need to confirm `base.html` renders without a Jinja error and carries the new colors):

```bash
python -c "
import sys
sys.path.insert(0, '.')
import web_viewer
app = web_viewer.app
app.config['TESTING'] = True
client = app.test_client()
with client.session_transaction() as sess:
    sess['user_id'] = 1
r = client.get('/dashboard')
print('status:', r.status_code)
html = r.get_data(as_text=True)
assert r.status_code == 200
assert '#C1552C' in html, 'new accent color missing from rendered page'
assert '#0969da' not in html, 'old accent color still present'
print('OK: dashboard renders with new warm palette')
"
```

Expected output: `status: 200` then `OK: dashboard renders with new warm palette`

- [ ] **Step 5: Run the full test suite**

Run: `python -m pytest tests -q`
Expected: `124 passed`, `1 failed` (the pre-existing `test_login_rejects_external_next_target` failure — unrelated to this change, already present before this plan)

- [ ] **Step 6: Commit**

```bash
git add templates/base.html
git commit -m "feat: apply warm amber/terracotta palette to base.html (light mode)"
```

---

### Task 2: Add dark-mode support to `base.html` with a working toggle

**Files:**
- Modify: `templates/base.html` (add a `[data-theme="dark"]` block after `:root`; add toggle button markup; fix the dead `.theme-toggle-unused` CSS; add the toggle script)

**Interfaces:**
- Consumes: the `:root` block from Task 1 (this task adds a sibling block, doesn't touch Task 1's values)
- Produces: `data-theme="dark"` attribute toggling on `<html>`, cookie `jmi_theme` (shared with `auth/login.html`'s existing implementation — same name, same value semantics: `"light"` or `"dark"`), global `window.toggleTheme()` function

- [ ] **Step 1: Add the dark-mode CSS block right after the `:root` block**

Find (the closing brace of the block Task 1 just edited):

```css
            --header-bg:    linear-gradient(135deg, #C1552C 0%, #E08E4F 100%);
            --header-border: rgba(255,255,255,0.15);
        }
```

Replace with (adds a new block immediately after):

```css
            --header-bg:    linear-gradient(135deg, #C1552C 0%, #E08E4F 100%);
            --header-border: rgba(255,255,255,0.15);
        }

        [data-theme="dark"] {
            --bg-base:      #211812;
            --bg-surface:   #2C2119;
            --bg-elevated:  #382A1F;
            --bg-hover:     #443423;
            --border:       #4A392C;
            --border-subtle:#443423;
            --text-primary: #F5E9DC;
            --text-secondary:#C4AD97;
            --text-muted:   #8A7862;
            --accent:       #E08E4F;
            --accent-hover: #EDA968;
            --accent-bg:    rgba(224,142,79,0.12);
            --success:      #8FB37A;
            --success-bg:   rgba(143,179,122,0.12);
            --warning:      #D9A441;
            --warning-bg:   rgba(217,164,65,0.12);
            --danger:       #E0685A;
            --danger-bg:    rgba(224,104,90,0.12);
            --purple:       #C99BC0;
            --purple-bg:    rgba(201,155,192,0.12);
        }
```

(`--bg-hover`, `--border-subtle`, `--text-muted`, `--warning`, `--warning-bg` are the five values not specified by the spec's table — derived here per the Global Constraints note above: `--bg-hover`/`--border-subtle` follow the light-mode pattern of being identical to each other, one step brighter than `--bg-elevated`; `--text-muted` sits dimmer than `--text-secondary`, warmer than pure gray; `--warning`/`--warning-bg` follow the same brightening the spec applied to `--accent` for dark mode, at the 0.12 alpha every other dark `-bg` variable uses. `--shadow-*`, `--header-bg`, and `--header-border` are intentionally absent from this block — they're not in the spec's dark table, and both stay correct unchanged: shadows work identically in both modes, and the header keeps its brand-color gradient regardless of theme.)

- [ ] **Step 2: Fix the dead theme-toggle CSS**

Find (currently dead code — note the class name mismatch between the two selectors):

```css
        /* (theme toggle removed) */
        .theme-toggle-unused {
            width: 36px; height: 36px;
            border: 1px solid var(--header-border);
            border-radius: 8px;
            background: rgba(255,255,255,0.08);
            color: white;
            cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            font-size: 16px;
            transition: background 0.15s;
        }
        .theme-toggle:hover { background: rgba(255,255,255,0.16); }
```

Replace with:

```css
        .theme-toggle {
            width: 36px; height: 36px;
            border: 1px solid var(--header-border);
            border-radius: 8px;
            background: rgba(255,255,255,0.08);
            color: white;
            cursor: pointer;
            display: flex; align-items: center; justify-content: center;
            font-size: 16px;
            transition: background 0.15s;
        }
        .theme-toggle:hover { background: rgba(255,255,255,0.16); }
```

- [ ] **Step 3: Add the toggle button to the header**

Find:

```html
                <div class="header-actions">
                    {% if g.current_user %}
```

Replace with:

```html
                <div class="header-actions">
                    <button class="theme-toggle" id="themeToggle" onclick="toggleTheme()" title="Toggle light/dark theme">🌙</button>
                    {% if g.current_user %}
```

- [ ] **Step 4: Add the toggle script**

Find (the closing of the existing timestamp-localizing script block, right before `{% block extra_scripts %}`):

```html
    })();
    </script>
    {% block extra_scripts %}{% endblock %}
```

Replace with:

```html
    })();
    </script>
    <script>
    (function(){
        const saved = document.cookie.match(/jmi_theme=([^;]+)/)?.[1];
        if (saved) document.documentElement.setAttribute("data-theme", saved);
        const btn = document.getElementById("themeToggle");
        if (btn) btn.textContent = document.documentElement.getAttribute("data-theme") === "dark" ? "☀️" : "🌙";
    })();

    window.toggleTheme = function(){
        const html = document.documentElement;
        const next = html.getAttribute("data-theme") === "dark" ? "light" : "dark";
        html.setAttribute("data-theme", next);
        document.cookie = `jmi_theme=${next};path=/;max-age=31536000;SameSite=Lax`;
        const btn = document.getElementById("themeToggle");
        if (btn) btn.textContent = next === "dark" ? "☀️" : "🌙";
    };
    </script>
    {% block extra_scripts %}{% endblock %}
```

(Uses the literal ☀️/🌙 characters, matching `auth/login.html`'s existing script exactly — same two emoji, same ternary structure.)

Note the default stays **light**: the `<html data-theme="light">` attribute (unchanged, still hardcoded at the top of the file) is only overridden by this script if a `jmi_theme` cookie is already present — e.g. because the user toggled it on `auth/login.html` at some point, which uses the same cookie name. First-time visitors with no cookie see light mode, same as today.

- [ ] **Step 5: Verify the dark-mode block and toggle wiring exist**

Run: `grep -c 'data-theme="dark"' templates/base.html`
Expected: `1`

Run: `grep -c "toggleTheme" templates/base.html`
Expected: `2` (the `onclick="toggleTheme()"` call and the `window.toggleTheme = function` definition)

Run: `grep -c "theme-toggle-unused" templates/base.html`
Expected: `0` (dead class fully removed)

- [ ] **Step 6: Confirm dark mode is reachable and correctly colored**

```bash
python -c "
import sys
sys.path.insert(0, '.')
import web_viewer
app = web_viewer.app
app.config['TESTING'] = True
client = app.test_client()
with client.session_transaction() as sess:
    sess['user_id'] = 1
client.set_cookie('jmi_theme', 'dark')
r = client.get('/dashboard')
html = r.get_data(as_text=True)
assert r.status_code == 200
assert 'data-theme=\"dark\"' not in html.split('<head>')[0], 'server should not force dark in the initial tag — client script applies it'
assert '[data-theme=\"dark\"]' in html, 'dark mode CSS block missing'
assert '#211812' in html, 'dark bg-base value missing'
print('OK: dark-mode CSS block present and correctly valued')
"
```

Expected output: `OK: dark-mode CSS block present and correctly valued`

- [ ] **Step 7: Run the full test suite again**

Run: `python -m pytest tests -q`
Expected: `124 passed`, `1 failed` (same pre-existing failure as Task 1 — no new failures)

- [ ] **Step 8: Commit**

```bash
git add templates/base.html
git commit -m "feat: add working dark-mode support to base.html"
```

---

**Checkpoint before Task 3:** per the spec's testing section, local visual verification by the user should happen before the Track 2 prompt is written — a broken or illegible palette shouldn't be the reference the design tool builds from. Start the local dev server (`python web_viewer.py`) and ask the user to look at `/dashboard` (and toggle dark mode) in an actual browser before proceeding to Task 3.

---

### Task 3: Write the Track 2 design prompt document

**Files:**
- Create: `docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md`

**Interfaces:**
- Consumes: the exact hex/rgba values from Task 1 and Task 2 (this document must match them exactly — it's describing real, already-committed code, not proposing new values)

- [ ] **Step 1: Write the prompt document**

Create `docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md` with this exact content:

````markdown
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

13 redesigned template files, plus a brand-mark proposal (shown applied in
`login.html`, described precisely enough to copy into `base.html`).
````

- [ ] **Step 2: Verify the required sections are present**

Run: `grep -c "^## " "docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md"`
Expected: `7` (What this product is, The palette is already live, The task, Brand mark, Copy tone, Hard technical constraints, Deliverable — the top-level `# Project brief` title itself doesn't match this `##`-anchored pattern)

Run: `grep -c "job_detail.html\|jobs_list.html\|skills_intelligence.html\|companies_intelligence.html\|titles_analytics.html\|metrics.html\|api_docs.html\|auth/login.html\|auth/my_keys.html\|auth/change_password.html\|index.html" "docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md"`
Expected: `11` or more (all 13 files are named at least once; `dashboard.html` and `skills.html` are substrings of other filenames already counted above, so aren't separately grepped here — confirm by eye that both appear in the file too)

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md
git commit -m "docs: add warm-redesign design prompt for the 13 public templates"
```

- [ ] **Step 4: Deliver the prompt to the user**

Paste the full contents of `docs/superpowers/specs/2026-07-08-warm-redesign-prompt.md`
into the chat response as a copyable markdown block, so the user can hand it directly
to the external design tool without needing to open the file themselves.
