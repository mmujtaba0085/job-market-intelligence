"""
tests/test_full_suite.py
Run: pytest tests/test_full_suite.py -v
All tests offline. Live-DB tests skip if data/jobs.sqlite absent.
"""
from __future__ import annotations
import os, re, sqlite3, tempfile
from datetime import datetime, timezone
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parent.parent

def _find_usable_db():
    """Find an openable jobs database (main or shadow fallback)."""
    import sqlite3 as _sq
    for name in ("data/jobs.sqlite", "data/jobs.shadow.sqlite", "data/jobs.db"):
        p = ROOT / name
        if not p.exists():
            continue
        try:
            c = _sq.connect(str(p), timeout=3)
            c.execute("SELECT COUNT(*) FROM jobs")
            c.close()
            return p
        except Exception:
            pass
    return None

DB_PATH = _find_usable_db()
JOBS_DB_EXISTS = DB_PATH is not None


def _fresh_auth_db():
    import sys; sys.path.insert(0, str(ROOT))
    tmp = tempfile.mktemp(suffix=".sqlite")
    import src.auth.models as m
    m.AUTH_DB_PATH = Path(tmp)
    m.init_auth_db()
    return m.get_auth_db(), tmp, m


# ===========================================================================
# 1. AUTH MODELS
# ===========================================================================
class TestAuthModels:
    def setup_method(self):
        import src.auth.models as m
        self._original_auth_db_path = m.AUTH_DB_PATH
        try:
            self.conn, self.tmp, self.m = _fresh_auth_db()
        except Exception:
            m.AUTH_DB_PATH = self._original_auth_db_path
            raise

    def teardown_method(self):
        self.m.AUTH_DB_PATH = self._original_auth_db_path
        self.conn.close()
        try: os.unlink(self.tmp)
        except Exception: pass

    def test_default_admin_created(self):
        assert any(u["role"] == "admin" for u in self.m.list_users())

    def test_create_user(self):
        self.m.create_user("alice", "a@x.com", "pass1234")
        assert any(u["username"] == "alice" for u in self.m.list_users())

    def test_duplicate_raises(self):
        self.m.create_user("dup", "d@x.com", "pass1234")
        with pytest.raises(Exception):
            self.m.create_user("dup", "d2@x.com", "pass1234")

    def test_auth_valid(self):
        self.m.create_user("bob", "b@x.com", "correct")
        assert self.m.authenticate_user("bob", "correct") is not None

    def test_auth_wrong_pass(self):
        self.m.create_user("carol", "c@x.com", "right")
        assert self.m.authenticate_user("carol", "wrong") is None

    def test_auth_inactive(self):
        self.m.create_user("dave", "d@x.com", "pass1234")
        uid = next(u["id"] for u in self.m.list_users() if u["username"] == "dave")
        self.m.update_user(uid, active=0)
        assert self.m.authenticate_user("dave", "pass1234") is None

    def test_api_key_generate(self):
        self.m.create_user("eve", "e@x.com", "pass1234")
        uid = next(u["id"] for u in self.m.list_users() if u["username"] == "eve")
        rec = self.m.generate_api_key(uid, "K1")
        assert rec["key"].startswith("jmi_") and rec["key_prefix"] == rec["key"][:12]

    def test_api_key_auth_valid(self):
        self.m.create_user("frank", "f@x.com", "pass1234")
        uid = next(u["id"] for u in self.m.list_users() if u["username"] == "frank")
        rec = self.m.generate_api_key(uid, "K2")
        assert self.m.authenticate_api_key(rec["key"]) is not None

    def test_api_key_auth_invalid(self):
        assert self.m.authenticate_api_key("jmi_nosuchkey") is None

    def test_revoke(self):
        self.m.create_user("grace", "g@x.com", "pass1234")
        uid = next(u["id"] for u in self.m.list_users() if u["username"] == "grace")
        rec = self.m.generate_api_key(uid, "K3")
        self.m.revoke_api_key(rec["id"])
        assert self.m.authenticate_api_key(rec["key"]) is None

    def test_change_password(self):
        self.m.create_user("henry", "h@x.com", "old")
        uid = next(u["id"] for u in self.m.list_users() if u["username"] == "henry")
        self.m.change_password(uid, "newpass1")
        assert self.m.authenticate_user("henry", "old") is None
        assert self.m.authenticate_user("henry", "newpass1") is not None

    def test_access_log(self):
        self.m.log_access("/ping", "GET", "1.2.3.4", 200, 5, auth_type="session")
        assert any(lg["endpoint"] == "/ping" for lg in self.m.get_access_logs(limit=5))

    def test_access_stats(self):
        s = self.m.get_access_stats()
        assert {"total", "today", "last_hour"} <= s.keys()

    def test_rate_limit_no_requests(self):
        assert self.m.check_rate_limit(9999, 100) is True

    def test_password_salted(self):
        assert self.m._hash_password("x") != self.m._hash_password("x")

    def test_check_password(self):
        h = self.m._hash_password("secret")
        assert self.m._check_password("secret", h) and not self.m._check_password("wrong", h)


def test_auth_db_path_restored_after_test_auth_models_teardown():
    """
    Regression test for a real leak: TestAuthModels.setup_method used to
    overwrite the module-global AUTH_DB_PATH permanently (raw assignment,
    no restore), and teardown_method deleted the temp file without ever
    putting the original path back — leaving a dangling path for whatever
    test ran next in the same pytest session. Proves the fix actually
    restores it, rather than trusting the reorder by inspection.
    """
    import src.auth.models as m
    original = m.AUTH_DB_PATH

    instance = TestAuthModels()
    instance.setup_method()
    assert m.AUTH_DB_PATH != original  # sanity: setup really did redirect it
    instance.teardown_method()

    assert m.AUTH_DB_PATH == original


def test_auth_db_path_restored_even_if_setup_fails():
    """
    Regression test for a narrower edge case the main fix didn't originally
    cover: if _fresh_auth_db() raises partway through setup_method (e.g. a
    permission error opening the temp auth DB), pytest never calls
    teardown_method — so without this fix, the just-saved original
    AUTH_DB_PATH would never be restored, and it would leak pointing at the
    unusable temp path.
    """
    import src.auth.models as m
    from unittest.mock import patch

    original = m.AUTH_DB_PATH

    instance = TestAuthModels()
    with patch("tests.test_full_suite._fresh_auth_db", side_effect=RuntimeError("simulated setup failure")):
        try:
            instance.setup_method()
        except RuntimeError:
            pass  # expected — we're only checking that AUTH_DB_PATH didn't leak

    assert m.AUTH_DB_PATH == original


# ===========================================================================
# 2. LOCATION EXTRACTION
# ===========================================================================
class TestLocationExtraction:
    def x(self, t):
        from src.enrichment.auto_enrich import extract_location_from_text
        return extract_location_from_text(t)

    def test_remote_label(self):
        _, c, _ = self.x("<p>Location: Remote (Candidates in CA, CO)</p>")
        assert c in ("Global", "United States")

    def test_city_state(self):
        _, c, _ = self.x("Location: San Francisco, CA")
        assert c == "United States"

    def test_candidates_in_states(self):
        _, c, _ = self.x("Candidates in CA, CO, FL, MD preferred.")
        assert c == "United States"

    def test_new_york_beats_york(self):
        # 'New York' (multi-word) must win over 'york' (UK single word)
        _, c, _ = self.x("This is a full-time position based in New York.")
        assert c == "United States", f"got {c!r}"

    def test_london_uk(self):
        _, c, _ = self.x("Our office is in London.")
        assert c == "United Kingdom"

    def test_german_cities(self):
        from src.enrichment.auto_enrich import _parse_location_string
        for city in ["Wolfsburg", "Gronau", "Dormagen", "Munich"]:
            _, c = _parse_location_string(city)
            assert c == "Germany", f"{city!r} -> {c!r}"

    def test_toronto_canada(self):
        _, c, _ = self.x("Location: Toronto, Ontario")
        assert c == "Canada"

    def test_berlin_germany(self):
        _, c, _ = self.x("Join our Berlin office.")
        assert c == "Germany"

    def test_sydney_australia(self):
        _, c, _ = self.x("Location: Sydney, Australia")
        assert c == "Australia"

    def test_no_location(self):
        _, c, _ = self.x("We build cool software.")
        assert c is None

    def test_html_stripped(self):
        _, c, _ = self.x("<h3>Location</h3><p>Seattle, WA</p>")
        assert c == "United States"


# ===========================================================================
# 3. REMOTE TYPE EXTRACTION
# ===========================================================================
class TestRemoteType:
    def r(self, t, loc=""):
        from src.enrichment.auto_enrich import extract_remote_type
        return extract_remote_type(t, loc)

    def test_fully_remote(self):
        assert self.r("This is a fully remote position.") == "remote"

    def test_wfh(self):
        assert self.r("Work from home opportunity.") == "remote"

    def test_no_geo(self):
        assert self.r("remote with no geographical restrictions") == "remote"

    def test_hybrid(self):
        assert self.r("2 days in office, 3 remote.") == "hybrid"

    def test_onsite(self):
        assert self.r("Must report to Chicago office daily.") == "on-site"

    def test_city_implies_onsite(self):
        assert self.r("Great job!", loc="Austin, TX") == "on-site"

    def test_empty_none(self):
        from src.enrichment.auto_enrich import extract_remote_type
        assert extract_remote_type("") is None


# ===========================================================================
# 4. SALARY EXTRACTION
# ===========================================================================
class TestSalary:
    def s(self, t):
        from src.enrichment.auto_enrich import extract_salary
        return extract_salary(t)

    def test_range_full(self):
        lo, hi, cur = self.s("Salary: $80,000 - $120,000")
        assert lo == 80000.0 and hi == 120000.0 and cur == "USD"

    def test_k_range(self):
        lo, hi, _ = self.s("$90k to $140k")
        assert lo == 90000.0 and hi == 140000.0

    def test_single(self):
        lo, hi, _ = self.s("$120,000 annual")
        assert lo == 120000.0 and hi is None

    def test_single_k(self):
        lo, _, _ = self.s("$95k base")
        assert lo == 95000.0

    def test_gbp(self):
        lo, hi, cur = self.s("Salary: \xa350,000 - \xa370,000")
        assert cur == "GBP" and lo == 50000.0

    def test_none(self):
        lo, hi, _ = self.s("Competitive salary.")
        assert lo is None and hi is None

    def test_phone_not_salary(self):
        lo, _, _ = self.s("Call 123-456-7890 to apply.")
        assert lo is None

    def test_html(self):
        lo, hi, _ = self.s("<p>$100,000 to $130,000</p>")
        assert lo == 100000.0 and hi == 130000.0


# ===========================================================================
# 5. ENRICH JOB END-TO-END
# ===========================================================================
class TestEnrichJob:
    def _conn(self):
        c = sqlite3.connect(":memory:")
        c.row_factory = sqlite3.Row
        c.execute("""CREATE TABLE jobs (
            job_id INTEGER PRIMARY KEY, title TEXT, company TEXT,
            location TEXT DEFAULT '', country TEXT DEFAULT '',
            remote_type TEXT DEFAULT 'unknown',
            raw_description TEXT DEFAULT '',
            salary_min REAL, salary_max REAL, currency TEXT,
            source_name TEXT DEFAULT '')""")
        c.commit()
        return c

    def test_fills_country(self):
        from src.enrichment.auto_enrich import enrich_job
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (1,'E','A','','Unknown','unknown','Location: San Francisco, CA',NULL,NULL,NULL,'')")
        c.commit()
        r = enrich_job(1, dict(c.execute("SELECT * FROM jobs WHERE job_id=1").fetchone()), c)
        assert "country" in r.changes and r.changes["country"][1] == "United States"

    def test_fills_remote(self):
        from src.enrichment.auto_enrich import enrich_job
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (2,'D','C','NY','United States','unknown','Fully remote position.',NULL,NULL,NULL,'')")
        c.commit()
        r = enrich_job(2, dict(c.execute("SELECT * FROM jobs WHERE job_id=2").fetchone()), c)
        assert "remote_type" in r.changes and r.changes["remote_type"][1] == "remote"

    def test_fills_salary(self):
        from src.enrichment.auto_enrich import enrich_job
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (3,'E','C','','unknown','unknown','Salary: $90,000 - $130,000',NULL,NULL,NULL,'')")
        c.commit()
        r = enrich_job(3, dict(c.execute("SELECT * FROM jobs WHERE job_id=3").fetchone()), c)
        assert "salary_min" in r.changes and float(r.changes["salary_min"][1]) == 90000.0

    def test_no_overwrite_existing_country(self):
        from src.enrichment.auto_enrich import enrich_job
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (4,'P','C','Berlin','Germany','unknown','Berlin role',NULL,NULL,NULL,'')")
        c.commit()
        r = enrich_job(4, dict(c.execute("SELECT * FROM jobs WHERE job_id=4").fetchone()), c)
        assert "country" not in r.changes

    def test_no_change_when_full(self):
        from src.enrichment.auto_enrich import enrich_job
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (5,'D','C','London','United Kingdom','remote','Good job',80000,120000,'GBP','')")
        c.commit()
        r = enrich_job(5, dict(c.execute("SELECT * FROM jobs WHERE job_id=5").fetchone()), c)
        assert not r.changed

    def test_null_conn_no_write(self):
        from src.enrichment.auto_enrich import enrich_job, _NullConn
        c = self._conn()
        c.execute("INSERT INTO jobs VALUES (6,'D','C','','','unknown','Location: Tokyo, Japan',NULL,NULL,NULL,'')")
        c.commit()
        job = dict(c.execute("SELECT * FROM jobs WHERE job_id=6").fetchone())
        r = enrich_job(6, job, _NullConn())
        assert r.changed
        assert c.execute("SELECT country FROM jobs WHERE job_id=6").fetchone()["country"] == ""


# ===========================================================================
# 6. GITHUB PARSER
# ===========================================================================
class TestGitHubParser:
    def _c(self):
        import sys; sys.path.insert(0, str(ROOT))
        from src.collectors.github_repo_collector import GitHubRepoCollector, _Cache
        obj = object.__new__(GitHubRepoCollector)
        obj.source_id = "github_simplify_2026"
        obj._cache = _Cache()
        obj._rate_limit_delay = 0.0
        obj._last_request_at = 0.0
        return obj

    def test_pipe_table(self):
        c = self._c()
        md = (
            "| Company | Role | Location | Application/Link |\n"
            "|---|---|---|---|\n"
            "| Google | SWE | Mountain View, CA | [Apply](https://g.co/1) |\n"
            "| Microsoft | PM | Seattle, WA | [Apply](https://ms.com/2) |\n"
        )
        jobs = c._parse_markdown_pipe_tables(md, "t/r", "main:README.md")
        assert len(jobs) == 2
        assert {j.parsed_fields["company"] for j in jobs} == {"Google", "Microsoft"}

    def test_arrow_inheritance(self):
        c = self._c()
        md = (
            "| Company | Role | Location | Application/Link |\n"
            "|---|---|---|---|\n"
            "| Apple | iOS | Cupertino | [Apply](https://a.com/1) |\n"
            "| ↳ | macOS | Cupertino | [Apply](https://a.com/2) |\n"
        )
        jobs = c._parse_markdown_pipe_tables(md, "t/r", "main:README.md")
        assert len(jobs) == 2
        assert all(j.parsed_fields["company"] == "Apple" for j in jobs)

    def test_html_table(self):
        c = self._c()
        html = (
            "<table>\n"
            "<tr><th>Company</th><th>Role</th><th>Location</th><th>Application</th></tr>\n"
            "<tr><td>Stripe</td><td>Backend</td><td>SF</td>"
            "<td><a href='https://s.com'>Apply</a></td></tr>\n"
            "</table>"
        )
        assert len(c._parse_html_tables(html, "t/r", "main:README.md")) == 1

    def test_url_markdown(self):
        assert self._c()._extract_url("[Apply](https://x.com)") == "https://x.com"

    def test_url_href(self):
        assert self._c()._extract_url('<a href="https://x.com">Apply</a>') == "https://x.com"

    def test_infer_remote(self):
        c = self._c()
        assert c._infer_remote_type("Remote, US") == "Remote"
        assert c._infer_remote_type("Hybrid NYC") == "Hybrid"
        assert c._infer_remote_type("New York, NY") == "On-site"

    def test_infer_country(self):
        c = self._c()
        assert c._infer_country("Seattle, WA") == "United States"
        assert c._infer_country("London, UK") == "United Kingdom"
        assert c._infer_country("Remote") == "Global"

    def test_date_age(self):
        assert self._c()._parse_any_date("5d") is not None

    def test_date_iso(self):
        assert self._c()._parse_any_date("2026-03-15") == "2026-03-15"

    def test_pipe_in_link_not_split(self):
        cells = self._c()._split_markdown_table_row("| **[Co | Name](url)** | SWE | SF |")
        assert len(cells) == 3

    def test_strip_md_links(self):
        c = self._c()
        assert c._strip_markdown_links("[Google](https://g.com)") == "Google"
        assert c._strip_markdown_links("**[Apple](https://a.com)**") == "Apple"

    def test_empty_content(self):
        assert self._c()._parse_content("", "auto", "t/r", "main:README.md") == []


# ===========================================================================
# 7. NORMALIZER
# ===========================================================================
class TestNormalizer:
    def _raw(self, url="https://x.com/1", pf=None):
        from src.storage.models import JobRaw
        base = {
            "title": "Senior ML Engineer", "company": "ACME",
            "location": "London", "country": "United Kingdom",
            "remote_type": "remote", "posted_date": "2026-03-01",
            "description": "Python, TensorFlow, PyTorch.",
        }
        if pf:
            base.update(pf)
        return JobRaw(
            source_id="remotive", source_name="Remotive", url=url,
            fetched_at=datetime.now(timezone.utc), parsed_fields=base,
        )

    def test_basic(self):
        from src.normalizer import normalize
        j = normalize(self._raw(), "m")
        assert j and j.title == "Senior ML Engineer" and j.market_id == "m"

    def test_url_hash_len(self):
        from src.normalizer import normalize
        assert len(normalize(self._raw(), "m").url_hash) == 64

    def test_canonical_location_independent(self):
        from src.normalizer import normalize
        j1 = normalize(self._raw(pf={"location": "NY"}), "m")
        j2 = normalize(self._raw(pf={"location": "London"}), "m")
        assert j1.canonical_hash == j2.canonical_hash

    def test_empty_url_none(self):
        from src.normalizer import normalize
        assert normalize(self._raw(url=""), "m") is None

    def test_empty_desc_none(self):
        from src.normalizer import normalize
        assert normalize(self._raw(pf={"description": ""}), "m") is None

    def test_remote_inferred(self):
        from src.normalizer import normalize
        j = normalize(self._raw(pf={"remote_type": "", "description": "Work from home role"}), "m")
        assert j.remote_type == "remote"

    def test_canonical_map_covers_variants(self):
        from src.normalizer import _REMOTE_TYPE_CANONICAL
        for v in ["fully_remote", "full_remote", "on_site", "office", "partially_remote"]:
            assert v in _REMOTE_TYPE_CANONICAL, f"Missing: {v!r}"


# ===========================================================================
# 8. LIVE DB (skipped if DB absent)
# ===========================================================================
@pytest.mark.skipif(not JOBS_DB_EXISTS, reason="data/jobs.sqlite absent")
class TestLiveDatabase:
    def setup_method(self):
        self.db = sqlite3.connect(str(DB_PATH), timeout=10)
        self.db.row_factory = sqlite3.Row

    def teardown_method(self):
        self.db.close()

    def q1(self, sql, *p):
        return self.db.execute(sql, p).fetchone()[0]

    def test_jobs_exist(self):
        assert self.q1("SELECT COUNT(*) FROM jobs") > 0

    def test_skills_exist(self):
        assert self.q1("SELECT COUNT(*) FROM skills") > 0

    def test_url_hash_unique(self):
        assert self.q1("SELECT COUNT(*) FROM jobs") == self.q1("SELECT COUNT(DISTINCT url_hash) FROM jobs")

    def test_no_null_url_hash(self):
        assert self.q1("SELECT COUNT(*) FROM jobs WHERE url_hash IS NULL") == 0

    def test_at_least_5_sources(self):
        assert self.q1("SELECT COUNT(DISTINCT source_name) FROM jobs") >= 5

    def test_country_coverage_65pct(self):
        """At least 65% of jobs should have a non-unknown country."""
        total = self.q1("SELECT COUNT(*) FROM jobs")
        if not total:
            pytest.skip("No jobs in DB")
        missing = self.q1("SELECT COUNT(*) FROM jobs WHERE country='' OR country='Unknown' OR country IS NULL")
        pct = (total - missing) / total * 100
        assert pct >= 65, f"Country coverage only {pct:.1f}% — run src/enrichment/auto_enrich.py to improve"

    def test_remote_type_all_canonical(self):
        from src.normalizer import _REMOTE_TYPE_CANONICAL
        CANON = {"remote", "hybrid", "on-site", "unknown", ""}
        rows = self.db.execute("SELECT DISTINCT lower(remote_type) as rt FROM jobs WHERE remote_type != ''").fetchall()
        unmapped = [r["rt"] for r in rows if r["rt"] not in CANON and r["rt"] not in _REMOTE_TYPE_CANONICAL]
        assert unmapped == [], f"Unmapped remote_type values (add to _REMOTE_TYPE_CANONICAL): {unmapped}"

    def test_date_format(self):
        bad = [
            r[0] for r in self.db.execute(
                "SELECT posted_date FROM jobs WHERE posted_date IS NOT NULL AND posted_date != ''"
            ).fetchall()
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", r[0])
        ]
        assert bad == [], f"Malformed dates: {bad[:5]}"

    def test_github_company_coverage(self):
        total = self.q1("SELECT COUNT(*) FROM jobs WHERE source_name LIKE 'GitHub:%'")
        if not total:
            pytest.skip("No GitHub jobs in DB")
        missing = self.q1("SELECT COUNT(*) FROM jobs WHERE source_name LIKE 'GitHub:%' AND (company='' OR company IS NULL)")
        assert missing / total * 100 < 5

    def test_enrichment_dry_run_improves(self):
        from src.enrichment.auto_enrich import enrich_job, _NullConn
        rows = self.db.execute(
            "SELECT job_id, title, company, location, country, remote_type, "
            "raw_description, salary_min, salary_max, currency, source_name "
            "FROM jobs WHERE country='' OR country='Unknown' LIMIT 100"
        ).fetchall()
        if not rows:
            pytest.skip("No missing-country rows in DB")
        from src.enrichment.auto_enrich import enrich_job, _NullConn
        improved = sum(1 for row in rows if enrich_job(row["job_id"], dict(row), _NullConn()).changed)
        pct = improved / len(rows) * 100
        assert pct >= 5, f"Only {improved}/{len(rows)} rows would improve ({pct:.0f}%)"