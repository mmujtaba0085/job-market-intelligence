"""
One-off backfill: recover real company names for existing Himalayas rows
broken by the upstream companyName="name" placeholder issue (see
src/collectors/himalayas_collector.py for background).

Fixing company changes a row's canonical_hash (title+company+description),
so normal re-crawl dedup can't self-heal these in place - it would insert a
fresh duplicate instead of updating the broken row. This script updates the
existing rows directly.

Three passes:
  1. Rows that already got a real himalayas.app URL from the URL self-heal
     (upsert_job) but still have the broken company: the company slug is
     right there in the URL path, no HTTP request needed.
  2. Rows still stuck with a synthetic himalayas://<hash> URL (no URL to
     recover a slug from): paginate the live API and match by exact title,
     recovering both url and company together when a match is found. Bounded
     to a fixed number of pages - jobs that have since expired/rotated off
     the live feed simply won't be found there.
  3. Whatever's still broken after passes 1-2: we already have the job's
     own description text stored locally (raw_description) from when it
     was first collected - no need for the live listing to still exist.
     Himalayas' description template links to the company's own profile
     page near the top ("The mission of <a href=.../companies/x>X</a> is
     ..."), so pull the first such link's anchor text directly, without
     needing to already know the slug.

Usage:
    python scripts/backfill_himalayas_company.py
"""

import re
import sqlite3
import time

import requests

from config.settings import DB_PATH

_BASE_URL = "https://himalayas.app/jobs/api"
_UA = "Mozilla/5.0 (compatible; JobMarketIntelligenceBot/1.0; +research)"
_PAGE_LIMIT = 20
_MAX_PAGES_PASS2 = 500  # ~10,000 most-recent live listings
_URL_SLUG_RE = re.compile(r"/companies/([^/]+)/jobs/")
_DESC_COMPANY_LINK_RE = re.compile(r'href="https://himalayas\.app/companies/[^/"]+/?"[^>]*>([^<]+)<')


def _slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def _recover_from_description(slug: str, description: str) -> str:
    match = re.search(
        rf'href="https://himalayas\.app/companies/{re.escape(slug)}/?"[^>]*>([^<]+)<',
        description or "",
    )
    return match.group(1).strip() if match else ""


def backfill_pass1(conn: sqlite3.Connection) -> int:
    """Rows with a real URL already: pull the slug straight out of it."""
    cur = conn.execute(
        "SELECT job_id, url FROM jobs WHERE source_name='Himalayas' "
        "AND lower(trim(company))='name' AND url LIKE 'https://%'"
    )
    rows = cur.fetchall()
    fixed = 0
    for job_id, url in rows:
        m = _URL_SLUG_RE.search(url)
        if not m:
            continue
        name = _slug_to_name(m.group(1))
        conn.execute("UPDATE jobs SET company=? WHERE job_id=?", (name, job_id))
        fixed += 1
    conn.commit()
    print(f"Pass 1 (URL slug extraction): fixed {fixed}/{len(rows)}")
    return fixed


def backfill_pass2(conn: sqlite3.Connection) -> int:
    """Rows with no real URL: match by title against the live catalog."""
    cur = conn.execute(
        "SELECT job_id, title FROM jobs WHERE source_name='Himalayas' "
        "AND lower(trim(company))='name' AND url LIKE 'himalayas://%'"
    )
    targets = {title: job_id for job_id, title in cur.fetchall()}
    print(f"Pass 2: {len(targets)} rows need a live-catalog match (scanning up to {_MAX_PAGES_PASS2} pages)")

    fixed = 0
    offset = 0
    for page in range(_MAX_PAGES_PASS2):
        if not targets:
            break
        try:
            resp = requests.get(
                _BASE_URL, params={"limit": _PAGE_LIMIT, "offset": offset},
                headers={"User-Agent": _UA}, timeout=15,
            )
        except requests.RequestException as exc:
            print(f"  page {page}: request error {exc}, stopping")
            break
        if resp.status_code != 200:
            print(f"  page {page}: HTTP {resp.status_code}, stopping")
            break
        jobs = resp.json().get("jobs", [])
        if not jobs:
            break

        for item in jobs:
            title = item.get("title") or ""
            if title not in targets:
                continue
            slug = item.get("companySlug") or ""
            if not slug:
                continue
            name = _recover_from_description(slug, item.get("description")) or _slug_to_name(slug)
            url = item.get("applicationLink") or item.get("guid") or ""
            job_id = targets.pop(title)
            if url:
                conn.execute(
                    "UPDATE jobs SET company=?, url=? WHERE job_id=?", (name, url, job_id)
                )
            else:
                conn.execute("UPDATE jobs SET company=? WHERE job_id=?", (name, job_id))
            fixed += 1

        offset += _PAGE_LIMIT
        if page % 25 == 0:
            conn.commit()
            print(f"  page {page}: {fixed} fixed so far, {len(targets)} remaining")
        time.sleep(0.5)

    conn.commit()
    print(f"Pass 2 done: fixed {fixed}, {len(targets)} not found in scanned window")
    return fixed


def backfill_pass3(conn: sqlite3.Connection) -> int:
    """Whatever's still broken: recover from our own already-stored description."""
    cur = conn.execute(
        "SELECT job_id, raw_description FROM jobs WHERE source_name='Himalayas' "
        "AND lower(trim(company))='name'"
    )
    rows = cur.fetchall()
    fixed = 0
    for job_id, description in rows:
        match = _DESC_COMPANY_LINK_RE.search(description or "")
        if not match:
            continue
        name = match.group(1).strip()
        if not name:
            continue
        conn.execute("UPDATE jobs SET company=? WHERE job_id=?", (name, job_id))
        fixed += 1
    conn.commit()
    print(f"Pass 3 (stored description): fixed {fixed}/{len(rows)}")
    return fixed


def main() -> None:
    conn = sqlite3.connect(str(DB_PATH))
    try:
        f1 = backfill_pass1(conn)
        f2 = backfill_pass2(conn)
        f3 = backfill_pass3(conn)
        print(f"Total fixed: {f1 + f2 + f3}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
