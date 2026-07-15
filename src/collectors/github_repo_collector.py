"""
src/collectors/github_repo_collector.py
────────────────────────────────────────
Collector for GitHub repository-based job/internship listings.

Key features:
- Pulls from raw.githubusercontent.com (no GitHub API key needed)
- ETag + Last-Modified caching (per repo+branch+path)
- Stores raw markdown on disk so HTTP 304 can still be parsed
- Supports multiple repos per source (use config key "repos": [...])
- Parser modes:
    - auto
    - markdown_pipe_table
    - html_table
    - flattened_table_blocks
    - jobright_daily_table
- Optional: follow_md_links for README index pages (SpeedyApply style)
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Optional

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_TIMEOUT = 20
_UA = "JobMarketIntelligence/1.0 (research; raw.githubusercontent.com)"

# Tech-relevance filter: titles must contain at least one of these keywords
# to be kept. Filters out civil/mechanical/HVAC/etc. from broad "Engineer" repos.
_TECH_TITLE_RE = re.compile(
    r'\b(?:software|data|machine[\s-]?learning|deep[\s-]?learning|'
    r'computer(?:\s+science)?|artificial[\s-]?intelligence|'
    r'\bml\b|\bai\b|web|mobile|\bios\b|android|'
    r'cloud|devops|dev[\s-]ops|platform|security|cyber|network|infrastructure|\bsre\b|'
    r'developer|programmer|scientist|'
    r'database|\bapi\b|backend|front[\s-]?end|full[\s-]?stack|'
    r'product[\s-](?:manager|management|design)|'
    r'robotics|automation|firmware|embedded|'
    r'quantitative|\bquant\b|fintech|blockchain|'
    r'information[\s-]technology|\bit[\s-]intern|'
    r'\bux\b|\bui\b|nlp\b|computer[\s-]?vision|'
    r'python|java(?:script)?|typescript|golang)\b',
    re.IGNORECASE,
)

_MD_LINK_RE = re.compile(r'\[([^\]]*)\]\(([^)]+)\)')

# Salary column parsing (e.g. SpeedyApply's "$62/hr" internship rates).
# Hourly is checked first since it's the specific case - a plain range/single
# check would otherwise misread "$62/hr" as a single annual figure of $62.
_SALARY_HOURLY_RE = re.compile(r"\$?\s*([\d,]+(?:\.\d+)?)\s*/\s*(?:hr|hour)", re.IGNORECASE)
_SALARY_RANGE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k)?\s*-\s*\$?\s*([\d,]+(?:\.\d+)?)\s*(k)?", re.IGNORECASE)
_SALARY_SINGLE_RE = re.compile(r"\$\s*([\d,]+(?:\.\d+)?)\s*(k)?", re.IGNORECASE)

_CACHE_ROOT = Path("data/cache/github")
_META_FILE = _CACHE_ROOT / "meta.json"
_RAW_DIR = _CACHE_ROOT / "raw"

# ------------------------------- Cache ----------------------------------


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def _safe_key(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8", errors="ignore")).hexdigest()[:24]


class _Cache:
    """
    Singleton cache for GitHub markdown files.
    All collector instances share the same cache to avoid overwriting meta.json.
    """
    _instance: Optional["_Cache"] = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self) -> None:
        # Only initialize once for the singleton
        if self._initialized:
            return
        
        _RAW_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        self.meta: dict[str, dict[str, Any]] = {}
        self._load()
        self._initialized = True

    def _load(self) -> None:
        if not _META_FILE.exists():
            self.meta = {}
            return
        try:
            self.meta = json.loads(_META_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            logger.warning("[github] Failed to load cache meta: %s", exc)
            self.meta = {}

    def save(self) -> None:
        try:
            _META_FILE.write_text(json.dumps(self.meta, indent=2), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.warning("[github] Failed to save cache meta: %s", exc)

    def raw_path_for(self, cache_key: str) -> Path:
        return _RAW_DIR / f"{_safe_key(cache_key)}.md"

    def get(self, cache_key: str) -> dict[str, Any]:
        return self.meta.get(cache_key, {})

    def set(self, cache_key: str, value: dict[str, Any]) -> None:
        self.meta[cache_key] = value


# ------------------------------ Config ----------------------------------


@dataclass(frozen=True)
class RepoSpec:
    repo_owner: str
    repo_name: str
    branch_candidates: list[str]
    paths: list[str]
    parser_mode: str = "auto"
    follow_md_links: bool = False
    max_linked_files: int = 10
    section_contains: Optional[list[str]] = None  # filter to a section whose heading contains any of these


# ------------------------------ Collector --------------------------------


class GitHubRepoCollector(BaseCollector):
    """
    Reads source config from config/sources.py.

    Required:
      - repos: list[dict] where each dict matches RepoSpec keys

    Notes:
      - source_id must exist in ALLOWED_SOURCES (BaseCollector enforces allowlist).
    """

    source_id: str = ""

    def __init__(self) -> None:
        super().__init__()
        self._cache = _Cache()

    @BaseCollector._retry_decorator()
    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        cfg = self._source_cfg
        repos_cfg = cfg.get("repos", [])
        if not repos_cfg:
            logger.warning("[%s] No repos configured (missing 'repos' list).", self.source_id)
            return []

        max_jobs = int(market.get("max_jobs_per_source", 5000))
        jobs: list[JobRaw] = []

        for rc in repos_cfg:
            spec = self._parse_repo_spec(rc)
            jobs.extend(self._collect_repo(spec, max_jobs=max_jobs - len(jobs)))
            if len(jobs) >= max_jobs:
                break

        return jobs[:max_jobs]

    def _parse_repo_spec(self, rc: dict) -> RepoSpec:
        return RepoSpec(
            repo_owner=rc["repo_owner"],
            repo_name=rc["repo_name"],
            branch_candidates=rc.get("branch_candidates") or [rc.get("branch", "main"), "main", "master", "dev"],
            paths=rc.get("paths") or ["README.md"],
            parser_mode=rc.get("parser_mode", "auto"),
            follow_md_links=bool(rc.get("follow_md_links", False)),
            max_linked_files=int(rc.get("max_linked_files", 10)),
            section_contains=rc.get("section_contains"),
        )

    def _collect_repo(self, spec: RepoSpec, max_jobs: int) -> list[JobRaw]:
        repo_tag = f"{spec.repo_owner}/{spec.repo_name}"
        collected: list[JobRaw] = []

        # BFS queue of paths to fetch (supports follow_md_links)
        seen_paths: set[str] = set()
        queue: list[str] = list(spec.paths)

        linked_used = 0

        while queue and len(collected) < max_jobs:
            path = queue.pop(0)
            path = self._normalize_path(path)
            if not path or path in seen_paths:
                continue
            seen_paths.add(path)

            branch_used, content = self._fetch_markdown(repo_tag, spec, path)
            if content is None:
                continue

            # Optional section filter
            if spec.section_contains:
                content = self._extract_section_by_heading_contains(content, spec.section_contains) or ""

            # Parse jobs
            parsed = self._parse_content(
                content=content,
                parser_mode=spec.parser_mode,
                repo_tag=repo_tag,
                file_path=f"{branch_used}:{path}",
            )
            # Drop non-tech titles (e.g. HVAC, Civil, Mechanical from broad Engineer repos)
            tech = [j for j in parsed if _TECH_TITLE_RE.search(j.parsed_fields.get("title", ""))]
            if len(tech) < len(parsed):
                logger.debug("Tech filter dropped %d/%d jobs from %s:%s",
                             len(parsed) - len(tech), len(parsed), repo_tag, path)
            collected.extend(tech)

            # Follow internal markdown links if configured
            if spec.follow_md_links and linked_used < spec.max_linked_files:
                new_paths = self._extract_internal_md_paths(
                    content, base_path=path, repo_owner=spec.repo_owner, repo_name=spec.repo_name
                )
                for p in new_paths:
                    if p not in seen_paths and p not in queue:
                        queue.append(p)
                        linked_used += 1
                        if linked_used >= spec.max_linked_files:
                            break

        return collected[:max_jobs]

    def _extract_internal_md_paths(
        self, content: str, base_path: str, repo_owner: str, repo_name: str
    ) -> list[str]:
        """
        Find markdown links to other .md files within this repo (e.g.
        SpeedyApply's README indexes separate category files like
        NEW_GRAD_USA.md, NEW_GRAD_INTL.md, INTERN_INTL.md via root-relative
        links such as "[New Graduate](/NEW_GRAD_USA.md)" rather than listing
        every category in one table) and resolves them to repo-relative
        paths so _collect_repo's queue can fetch and parse them too.
        External links, non-.md links, and in-file anchors are ignored.
        """
        base_dir = base_path.rsplit("/", 1)[0] if "/" in base_path else ""
        paths: list[str] = []

        for _text, link in _MD_LINK_RE.findall(content):
            link = link.strip()
            if link.startswith(("http://", "https://")):
                continue
            link = link.split("#", 1)[0]  # drop in-file anchor
            if not link.lower().endswith(".md"):
                continue

            if link.startswith("/"):
                resolved = link.lstrip("/")
            elif base_dir:
                resolved = f"{base_dir}/{link}"
            else:
                resolved = link

            resolved = self._normalize_path(resolved)
            if resolved and resolved not in paths:
                paths.append(resolved)

        return paths

    # ------------------------- Fetch with caching -------------------------

    def _fetch_markdown(self, repo_tag: str, spec: RepoSpec, path: str) -> tuple[str, Optional[str]]:
        """
        Try branches in order until we can fetch or load from cache.
        Returns (branch_used, content_or_none).
        """
        last_error: Optional[str] = None

        for branch in spec.branch_candidates:
            cache_key = f"{self.source_id}:{repo_tag}@{branch}:{path}"
            meta = self._cache.get(cache_key)
            raw_path = self._cache.raw_path_for(cache_key)

            headers = {"User-Agent": _UA, "Accept": "text/plain"}
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]

            url = f"https://raw.githubusercontent.com/{spec.repo_owner}/{spec.repo_name}/{branch}/{path}"

            self._wait()
            try:
                resp = requests.get(url, headers=headers, timeout=_TIMEOUT)

                # 404 on this branch -> try next branch
                if resp.status_code == 404:
                    last_error = f"404 {url}"
                    continue

                # Not modified -> load cached content (CRITICAL FIX vs your current `continue`)
                if resp.status_code == 304:
                    if raw_path.exists():
                        return branch, raw_path.read_text(encoding="utf-8", errors="ignore")
                    # If cache missing, force a fresh fetch without conditional headers
                    resp = requests.get(url, headers={"User-Agent": _UA, "Accept": "text/plain"}, timeout=_TIMEOUT)

                resp.raise_for_status()
                content = resp.text or ""

                # Save content and update meta
                raw_path.write_text(content, encoding="utf-8")
                meta_update = {
                    "etag": resp.headers.get("ETag"),
                    "last_modified": resp.headers.get("Last-Modified"),
                    "sha256": _sha256_text(content),
                    "saved_at": datetime.now(timezone.utc).isoformat(),
                    "url": url,
                }
                self._cache.set(cache_key, meta_update)
                self._cache.save()

                return branch, content

            except requests.RequestException as exc:
                last_error = str(exc)
                continue

        if last_error:
            logger.warning("[%s] Failed to fetch %s (%s)", self.source_id, f"{repo_tag}:{path}", last_error)
        return (spec.branch_candidates[0] if spec.branch_candidates else "main"), None

    @staticmethod
    def _normalize_path(path: str) -> str:
        path = (path or "").strip()
        if not path:
            return ""
        path = path.split("#", 1)[0]  # drop anchor
        path = path.replace("\\", "/")
        # Strip leading/current-directory markers
        if path.startswith("./"):
            path = path[2:]
        if path.startswith("/"):
            path = path[1:]
        return path

    # ----------------------------- Parsers --------------------------------

    def _parse_content(self, content: str, parser_mode: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        mode = (parser_mode or "auto").lower().strip()

        if mode == "auto":
            # Prefer HTML table if present
            if "<table" in content.lower():
                jobs = self._parse_html_tables(content, repo_tag, file_path)
                if jobs:
                    return jobs
            # Fall back to pipe tables
            jobs = self._parse_markdown_pipe_tables(content, repo_tag, file_path)
            if jobs:
                return jobs
            # Flattened blocks (Simplify-like)
            jobs = self._parse_flattened_blocks(content, repo_tag, file_path)
            if jobs:
                return jobs
            # Jobright daily list fallback
            return self._parse_jobright_daily(content, repo_tag, file_path)

        if mode == "markdown_pipe_table":
            return self._parse_markdown_pipe_tables(content, repo_tag, file_path)
        if mode == "html_table":
            return self._parse_html_tables(content, repo_tag, file_path)
        if mode == "flattened_table_blocks":
            return self._parse_flattened_blocks(content, repo_tag, file_path)
        if mode == "jobright_daily_table":
            return self._parse_jobright_daily(content, repo_tag, file_path)
        if mode == "nuft_quant":
            return self._parse_nuft_quant_format(content, repo_tag, file_path)

        logger.warning("[%s] Unknown parser_mode=%s; using auto.", self.source_id, parser_mode)
        return self._parse_content(content, "auto", repo_tag, file_path)

    # --- markdown pipe tables ---

    def _parse_markdown_pipe_tables(self, content: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        lines = content.splitlines()
        jobs: list[JobRaw] = []

        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not (line.startswith("|") and "|" in line):
                i += 1
                continue

            # Header line
            header_cells = [c.strip().lower() for c in self._split_markdown_table_row(line)]
            # Separator line must follow
            if i + 1 >= len(lines) or not re.match(r"^\s*\|?\s*[-: ]+\|", lines[i + 1]):
                i += 1
                continue

            idx_company = self._col_index(header_cells, ["company", "employer", "organization"])
            idx_title = self._col_index(header_cells, ["role", "position", "job title", "title"])
            idx_location = self._col_index(header_cells, ["location", "city", "region", "where"])
            idx_apply = self._col_index(header_cells, ["apply", "application", "link", "url"])
            idx_date = self._col_index(header_cells, ["date", "posted", "date posted", "age"])
            idx_salary = self._col_index(header_cells, ["salary", "pay", "compensation"])

            i += 2  # move to first data row
            prev_company = ""
            while i < len(lines):
                row = lines[i].strip()
                if not row.startswith("|"):
                    break
                cells = self._split_markdown_table_row(row)

                company = self._clean_cell(cells, idx_company)
                company = self._strip_html(company)
                company = self._strip_markdown_links(company)
                company = self._remove_emojis(company).strip()
                
                # Handle ↳ company inheritance
                if company == "↳" or company == "":
                    company = prev_company
                else:
                    prev_company = company
                
                title = self._clean_cell(cells, idx_title)
                title = self._strip_html(title)
                title = self._strip_markdown_links(title)
                title = self._remove_emojis(title).strip()
                
                location = self._clean_location(self._clean_cell(cells, idx_location))

                apply_cell = self._clean_cell(cells, idx_apply) if idx_apply != -1 else ""
                date_cell = self._clean_cell(cells, idx_date) if idx_date != -1 else ""
                salary_cell = self._clean_cell(cells, idx_salary) if idx_salary != -1 else ""

                url = self._extract_url(apply_cell) or self._extract_url(row) or ""
                if not url:
                    url = self._fallback_url(repo_tag, file_path, company, title, location)

                if company.lower() in {"company", "employer"} or not title or not company:
                    i += 1
                    continue

                posted_date = self._parse_any_date(date_cell)
                salary_min, salary_max, currency, salary_period = self._parse_salary_cell(salary_cell)
                remote_type = self._infer_remote_type(location)
                country = self._infer_country(location)

                jobs.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name=f"GitHub:{repo_tag}",
                        url=url,
                        fetched_at=self._now(),
                        raw_json=None,
                        parsed_fields={
                            "title": title,
                            "company": company,
                            "location": location,
                            "country": country,
                            "remote_type": remote_type,
                            "posted_date": posted_date,
                            "description": f"{title} at {company} ({location})",
                            "source_repo": repo_tag,
                            "source_file": file_path,
                            "salary_min": salary_min,
                            "salary_max": salary_max,
                            "currency": currency,
                            "salary_period": salary_period,
                        },
                    )
                )
                i += 1

            continue

        return jobs

    # --- HTML <table> parsing (Simplify style) ---

    def _parse_html_tables(self, content: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        jobs: list[JobRaw] = []

        tables = re.findall(r"<table.*?>(.*?)</table>", content, flags=re.IGNORECASE | re.DOTALL)
        for table_html in tables:
            # headers
            header_cells = re.findall(r"<th[^>]*>(.*?)</th>", table_html, flags=re.IGNORECASE | re.DOTALL)
            headers = [self._strip_html(h).lower() for h in header_cells]

            idx_company = self._col_index(headers, ["company", "employer"])
            idx_title = self._col_index(headers, ["role", "position", "job", "title"])
            idx_location = self._col_index(headers, ["location", "where"])
            idx_apply = self._col_index(headers, ["application", "apply", "link", "url"])
            idx_date = self._col_index(headers, ["age", "date", "posted"])

            rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table_html, flags=re.IGNORECASE | re.DOTALL)
            prev_company = ""
            for row_html in rows:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.IGNORECASE | re.DOTALL)
                if not cells:
                    continue

                company = self._strip_html(cells[idx_company]) if idx_company != -1 and idx_company < len(cells) else ""
                company = self._remove_emojis(company).strip()

                # handle ↳ inheritance
                if company == "↳" or company == "":
                    company = prev_company
                else:
                    prev_company = company

                title = self._strip_html(cells[idx_title]) if idx_title != -1 and idx_title < len(cells) else ""
                title = self._remove_emojis(title).strip()

                loc_html = cells[idx_location] if idx_location != -1 and idx_location < len(cells) else ""
                location = self._clean_location(self._extract_first_location_from_details(loc_html) or self._strip_html(loc_html))

                apply_html = cells[idx_apply] if idx_apply != -1 and idx_apply < len(cells) else ""
                url = self._extract_url(apply_html) or ""
                if not url:
                    url = self._fallback_url(repo_tag, file_path, company, title, location)

                date_cell = self._strip_html(cells[idx_date]) if idx_date != -1 and idx_date < len(cells) else ""
                posted_date = self._parse_any_date(date_cell)

                if not company or not title or company.lower() in {"company", "employer"}:
                    continue

                jobs.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name=f"GitHub:{repo_tag}",
                        url=url,
                        fetched_at=self._now(),
                        raw_json=None,
                        parsed_fields={
                            "title": title,
                            "company": company,
                            "location": location,
                            "country": self._infer_country(location),
                            "remote_type": self._infer_remote_type(location),
                            "posted_date": posted_date,
                            "description": f"{title} at {company} ({location})",
                            "source_repo": repo_tag,
                            "source_file": file_path,
                        },
                    )
                )

        return jobs

    # --- Simplify-like flattened lines fallback ---

    def _parse_flattened_blocks(self, content: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        jobs: list[JobRaw] = []
        lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
        prev_company = ""

        for ln in lines:
            if "company" in ln.lower() and "role" in ln.lower() and "location" in ln.lower():
                continue

            url = self._extract_url(ln) or ""
            if not url:
                continue

            # strip link part to isolate text-ish
            ln_clean = re.sub(r"\[!\[.*?\]\(.*?\)\]\(.*?\)", "", ln)  # remove image-link apply buttons
            ln_clean = re.sub(r"\[.*?\]\(https?://.*?\)", "", ln_clean)  # remove markdown links
            ln_clean = self._remove_emojis(self._strip_html(ln_clean)).strip()

            # try to pull age at end
            age = ""
            m_age = re.search(r"(\d+\s*(?:d|w|mo))\s*$", ln_clean.lower())
            if m_age:
                age = m_age.group(1)
                ln_clean = ln_clean[: m_age.start()].strip()

            # Handle ↳
            if ln_clean.startswith("↳"):
                ln_clean = ln_clean.lstrip("↳").strip()
                company = prev_company
            else:
                # naive split: first token chunk as company until we hit a likely role keyword
                parts = ln_clean.split()
                if len(parts) < 3:
                    continue
                company = parts[0]
                # better: if comma-separated segments exist, assume last segment is location
            # crude: location is after last comma if any
            location = ""
            if "," in ln_clean:
                location = ln_clean.split(",")[-1].strip()
            # title: everything except company and location (best effort)
            title = ln_clean
            if company and title.startswith(company):
                title = title[len(company) :].strip()
            if location and title.endswith(location):
                title = title[: -len(location)].strip(" ,")

            if company:
                prev_company = company

            posted_date = self._parse_any_date(age)
            if not posted_date:
                posted_date = None

            if not company or not title:
                continue

            jobs.append(
                JobRaw(
                    source_id=self.source_id,
                    source_name=f"GitHub:{repo_tag}",
                    url=url,
                    fetched_at=self._now(),
                    raw_json=None,
                    parsed_fields={
                        "title": title,
                        "company": company,
                        "location": location,
                        "country": self._infer_country(location),
                        "remote_type": self._infer_remote_type(location),
                        "posted_date": posted_date,
                        "description": f"{title} at {company} ({location})",
                        "source_repo": repo_tag,
                        "source_file": file_path,
                    },
                )
            )

        return jobs

    # --- Jobright daily list (use section + pipe-table fallback) ---

    def _parse_jobright_daily(self, content: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        section = self._extract_section_by_heading_contains(content, ["daily job list", "daily internship list"])
        if section:
            jobs = self._parse_markdown_pipe_tables(section, repo_tag, file_path)
            if jobs:
                return jobs
        # fallback: just parse any pipe tables in whole doc
        jobs = self._parse_markdown_pipe_tables(content, repo_tag, file_path)
        if jobs:
            return jobs
        return []

    # ----------------------------- Helpers --------------------------------

    @staticmethod
    def _split_markdown_table_row(row: str) -> list[str]:
        """
        Split a markdown table row by pipes, but ignore pipes inside markdown links.
        Handles cases like: | **[Company | Name](url)** | Title | 
        where the pipe inside the link should not split the cell.
        """
        row = row.strip()
        if row.startswith("|"):
            row = row[1:]
        if row.endswith("|"):
            row = row[:-1]
        
        cells = []
        current_cell = []
        bracket_depth = 0
        paren_depth = 0
        
        i = 0
        while i < len(row):
            char = row[i]
            
            if char == '[':
                bracket_depth += 1
                current_cell.append(char)
            elif char == ']':
                bracket_depth -= 1
                current_cell.append(char)
            elif char == '(':
                paren_depth += 1
                current_cell.append(char)
            elif char == ')':
                paren_depth -= 1
                current_cell.append(char)
            elif char == '|' and bracket_depth == 0 and paren_depth == 0:
                # Found a cell separator
                cells.append(''.join(current_cell).strip())
                current_cell = []
            else:
                current_cell.append(char)
            
            i += 1
        
        # Don't forget the last cell
        if current_cell:
            cells.append(''.join(current_cell).strip())

        return cells

    @staticmethod
    def _parse_salary_cell(cell: str) -> tuple[Optional[float], Optional[float], Optional[str], Optional[str]]:
        """
        Parse a markdown table Salary cell into (salary_min, salary_max,
        currency, salary_period). Handles hourly rates ("$62/hr" - common
        for internship listings) and simple annual ranges/single values
        ("$95K - $110K", "$95,000"). Returns all-None for anything else
        rather than guessing - an hourly rate stored without salary_period
        would look like a nonsensical annual figure everywhere else in the
        app that assumes salary_min/max are annual.
        """
        cell = (cell or "").strip()
        if not cell or "$" not in cell:
            return None, None, None, None

        m = _SALARY_HOURLY_RE.search(cell)
        if m:
            value = float(m.group(1).replace(",", ""))
            return value, value, "USD", "hourly"

        m = _SALARY_RANGE_RE.search(cell)
        if m:
            lo = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            hi = float(m.group(3).replace(",", "")) * (1000 if m.group(4) else 1)
            return lo, hi, "USD", "annual"

        m = _SALARY_SINGLE_RE.search(cell)
        if m:
            value = float(m.group(1).replace(",", "")) * (1000 if m.group(2) else 1)
            return value, value, "USD", "annual"

        return None, None, None, None

    @staticmethod
    def _col_index(headers: list[str], options: list[str]) -> int:
        for opt in options:
            opt_l = opt.lower()
            for idx, h in enumerate(headers):
                if opt_l in h:
                    return idx
        return -1

    @staticmethod
    def _clean_cell(cells: list[str], idx: int) -> str:
        if idx == -1 or idx >= len(cells):
            return ""
        return re.sub(r"\s+", " ", cells[idx]).strip()

    def _strip_markdown_links(self, text: str) -> str:
        """
        Strip markdown links and formatting from text, keeping only the link text.
        Examples:
        - [text](url) -> text
        - **[text](url)** -> text
        - **text** -> text
        - [**text**](url) -> text
        - [[Online Assessment] Title](url) -> [Online Assessment] Title
          (link text containing its own nested [tag] brackets, e.g.
          Jobright's "[Online Assessment]"/"[US]" prefixes - see
          _extract_markdown_link_text for why this needs a bracket-depth
          scan rather than a single regex)
        """
        if not text:
            return ""
        # First pass: remove bold/italic around links: **[text](url)** -> [text](url)
        text = re.sub(r'(\*+)(\[.+?\]\(.+?\))(\*+)', r'\2', text)
        # Second pass: extract text from markdown links: [text](url) -> text
        text = self._extract_markdown_link_text(text)
        # Third pass: remove any remaining markdown bold/italic markers
        text = re.sub(r'[*_]+', '', text)
        return text.strip()

    @staticmethod
    def _extract_markdown_link_text(text: str) -> str:
        """[text](url) -> text, where text may itself contain [nested]
        brackets (e.g. "[[Online Assessment] Foo](url)"). A regex like
        \\[([^\\]]+)\\]\\(...\\) can't handle this: [^\\]]+ stops at the
        FIRST ']', which is the inner tag's closing bracket, not the outer
        link's - the match then fails (no '(' right after) and the whole
        [text](url) is left completely unstripped in the output, verbatim.
        Same bracket-depth-counting approach _split_markdown_table_row
        already uses for pipe-splitting, applied here instead."""
        result = []
        i = 0
        n = len(text)
        while i < n:
            if text[i] == '[':
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if text[j] == '[':
                        depth += 1
                    elif text[j] == ']':
                        depth -= 1
                    j += 1
                if depth == 0 and j < n and text[j] == '(':
                    close_paren = text.find(')', j)
                    if close_paren != -1:
                        result.append(text[i + 1:j - 1])
                        i = close_paren + 1
                        continue
            result.append(text[i])
            i += 1
        return ''.join(result)

    def _clean_location(self, s: str) -> str:
        s = self._remove_emojis(self._strip_html(s))
        s = re.sub(r"\s+", " ", s).strip()
        return s

    def _extract_url(self, text: str) -> Optional[str]:
        if not text:
            return None
        # HTML href attribute: href="https://..."
        m_href = re.search(r'href=["\']([^"\'>]+)["\']', text, re.IGNORECASE)
        if m_href:
            url = m_href.group(1).strip()
            if url.startswith("http"):
                return url
        # markdown image-link: [![Apply](img)](https://...)
        m = re.search(r"\]\((https?://[^)]+)\)", text)
        if m:
            return m.group(1).strip()
        # plain url
        m2 = re.search(r"(https?://\S+)", text)
        if m2:
            return m2.group(1).rstrip("),.").strip()
        return None

    def _fallback_url(self, repo_tag: str, file_path: str, company: str, title: str, location: str) -> str:
        base = f"{repo_tag}|{file_path}|{company}|{title}|{location}"
        return f"github://{hashlib.sha256(base.encode('utf-8', errors='ignore')).hexdigest()}"

    def _parse_any_date(self, s: str) -> Optional[str]:
        s = (s or "").strip()
        if not s:
            return None

        # age formats: 5d, 2w, 1mo
        m_age = re.match(r"^\s*(\d+)\s*(d|w|mo)\s*$", s.lower())
        if m_age:
            n = int(m_age.group(1))
            unit = m_age.group(2)
            days = n if unit == "d" else (n * 7 if unit == "w" else n * 30)
            return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()

        # ISO date
        for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass

        # ISO datetime prefix
        m_iso = re.match(r"^(\d{4}-\d{2}-\d{2})", s)
        if m_iso:
            return m_iso.group(1)

        # Month Day Year formats (with optional comma)
        # Examples: "Dec 13, 2025", "December 13 2025", "Jan 5, 2026"
        for fmt in ["%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y"]:
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass

        # Day Month Year formats
        # Examples: "13 Dec 2025", "13 December 2025"
        for fmt in ["%d %b %Y", "%d %B %Y"]:
            try:
                return datetime.strptime(s, fmt).date().isoformat()
            except ValueError:
                pass

        # Month Day (no year) - assume current year, or previous if date would be in future
        # Examples: "Dec 13", "January 5"
        for fmt in ["%b %d", "%B %d"]:
            try:
                now = datetime.now(timezone.utc).date()
                parsed = datetime.strptime(s, fmt)
                # Set to current year first
                date_obj = parsed.replace(year=datetime.now().year).date()
                # If date is more than 30 days in the future, assume it's from last year
                if (date_obj - now).days > 30:
                    date_obj = date_obj.replace(year=date_obj.year - 1)
                return date_obj.isoformat()
            except ValueError:
                pass

        # Day Month (no year)
        # Examples: "13 Dec", "5 Jan"
        for fmt in ["%d %b", "%d %B"]:
            try:
                now = datetime.now(timezone.utc).date()
                parsed = datetime.strptime(s, fmt)
                # Set to current year first
                date_obj = parsed.replace(year=datetime.now().year).date()
                # If date is more than 30 days in the future, assume it's from last year
                if (date_obj - now).days > 30:
                    date_obj = date_obj.replace(year=date_obj.year - 1)
                return date_obj.isoformat()
            except ValueError:
                pass

        return None

    def _infer_remote_type(self, location: str) -> str:
        loc = (location or "").lower()
        if any(k in loc for k in ["remote", "anywhere", "worldwide", "global"]):
            return "Remote"
        if "hybrid" in loc:
            return "Hybrid"
        if not loc:
            return "Unknown"
        return "On-site"

    def _infer_country(self, location: str) -> str:
        from src.utils.country_inference import infer_country
        return infer_country(location)

    def _strip_html(self, html: str) -> str:
        if not html:
            return ""
        text = re.sub(r"<[^>]+>", "", html)
        text = (
            text.replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
            .replace("&nbsp;", " ")
        )
        return " ".join(text.split()).strip()

    def _remove_emojis(self, text: str) -> str:
        if not text:
            return ""
        emoji_pattern = re.compile(
            "["
            "\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F900-\U0001F9FF"
            "\U00002702-\U000027B0"
            "]+",
            flags=re.UNICODE,
        )
        return emoji_pattern.sub("", text).strip()

    def _extract_first_location_from_details(self, html: str) -> str:
        if not html:
            return ""
        m = re.search(r"<details>.*?<summary[^>]*>.*?</summary>(.*?)</details>", html, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            return ""
        body = m.group(1)
        parts = re.split(r"<br\s*/?>", body, flags=re.IGNORECASE)
        for p in parts:
            p_clean = self._strip_html(p).strip()
            if p_clean:
                return p_clean
        return ""

    def _parse_nuft_quant_format(self, content: str, repo_tag: str, file_path: str) -> list[JobRaw]:
        """
        Parse NUFT Quant format which uses company sections with mini Role/Links tables.
        
        Format:
        ## Company Name
        **Website**: [link](url)
        **Locations**: Chicago
        **Notes**: Some notes
        
        |Role|Links|
        |---|---|
        |SWE|[✅ C++](url1) [✅ Python](url2)|
        |QR|[✅](url3)|
        """
        jobs: list[JobRaw] = []
        lines = content.splitlines()
        i = 0
        
        while i < len(lines):
            line = lines[i].strip()
            
            # Find company section (## heading)
            if not line.startswith("##"):
                i += 1
                continue
            
            # Extract company name
            company = line.lstrip("#").strip()
            i += 1
            
            # Extract metadata (Website, Locations, Notes) until we hit the table
            metadata = {}
            while i < len(lines):
                ln = lines[i].strip()
                if ln.startswith("|"):  # Table header found
                    break
                if ln.startswith("**") and "**:" in ln:
                    # Parse metadata line like "**Locations**: Chicago"
                    key_match = re.match(r"\*\*([^*]+)\*\*:\s*(.+)", ln)
                    if key_match:
                        key = key_match.group(1).strip().lower()
                        value = key_match.group(2).strip()
                        # Strip markdown link if present [text](url) -> text
                        value = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', value)
                        metadata[key] = value
                i += 1
            
            # Skip table header and separator
            if i < len(lines) and lines[i].strip().startswith("|"):
                i += 1  # Skip header row
            if i < len(lines) and lines[i].strip().startswith("|"):
                i += 1  # Skip separator row
            
            # Parse table rows
            while i < len(lines):
                ln = lines[i].strip()
                if not ln.startswith("|"):
                    break  # End of table
                
                # Split by | and clean
                cells = [c.strip() for c in ln.split("|")]
                cells = [c for c in cells if c]  # Remove empty cells
                
                if len(cells) >= 2:
                    role = cells[0].strip()
                    links_cell = cells[1].strip()
                    
                    # Extract all markdown links from Links cell
                    # Pattern: [text](url) or [✅ text](url)
                    link_pattern = r'\[([^\]]+)\]\(([^)]+)\)'
                    matches = re.findall(link_pattern, links_cell)
                    
                    for link_text, url in matches:
                        # Clean link text (remove checkmarks, emojis)
                        link_text_clean = self._remove_emojis(link_text).strip()
                        
                        # Build job title
                        if link_text_clean and link_text_clean != "✅":
                            job_title = f"{role} Intern ({link_text_clean})"
                        else:
                            job_title = f"{role} Intern"
                        
                        # Extract location from metadata
                        location = metadata.get("locations", "Unknown")
                        remote_type = self._infer_remote_type(location)
                        country = self._infer_country(location)

                        description = f"{job_title} at {company} ({location})"
                        notes = metadata.get("notes", "")
                        if notes:
                            description = f"{description}. {notes}"

                        # Create job
                        jobs.append(
                            JobRaw(
                                source_id=self.source_id,
                                source_name=f"GitHub:{repo_tag}",
                                url=url.strip(),
                                fetched_at=self._now(),
                                raw_json=None,
                                parsed_fields={
                                    "title": job_title,
                                    "company": company,
                                    "location": location,
                                    "country": country,
                                    "remote_type": remote_type,
                                    "posted_date": "",
                                    "description": description,
                                    "source_repo": repo_tag,
                                    "source_file": file_path,
                                    "website": metadata.get("website", ""),
                                    "notes": metadata.get("notes", ""),
                                    "role": role,
                                    "link_text": link_text,
                                }
                            )
                        )
                
                i += 1
        
        logger.info("[%s] Parsed NUFT Quant format from %s: %d jobs", self.source_id, file_path, len(jobs))
        return jobs

    def _extract_section_by_heading_contains(self, content: str, needles: list[str]) -> Optional[str]:
        """
        Return content from the first heading line that contains any needle
        until the next heading of same or higher level.
        """
        lines = content.splitlines()
        needles_l = [n.lower() for n in needles]
        start = -1
        level = None

        for i, ln in enumerate(lines):
            s = ln.strip()
            if s.startswith("#"):
                s_l = s.lower()
                if any(n in s_l for n in needles_l):
                    start = i
                    level = len(s) - len(s.lstrip("#"))
                    break

        if start == -1:
            return None

        end = len(lines)
        for j in range(start + 1, len(lines)):
            s = lines[j].strip()
            if s.startswith("#"):
                lvl = len(s) - len(s.lstrip("#"))
                if level is not None and lvl <= level:
                    end = j
                    break

        return "\n".join(lines[start:end]).strip()



# ── Concrete collector classes (just map to source_id) ─────────────────────

class GitHubSimplify2026Collector(GitHubRepoCollector):
    source_id = "github_simplify_2026"


class GitHubVansh2026Collector(GitHubRepoCollector):
    source_id = "github_vansh_2026"


class GitHubSpeedyApply2026Collector(GitHubRepoCollector):
    source_id = "github_speedyapply_2026"


class GitHubJobright2026Collector(GitHubRepoCollector):
    source_id = "github_jobright_2026"


class GitHubNUFTQuant2026Collector(GitHubRepoCollector):
    source_id = "github_nuft_quant_2026"


class GitHubOffSeasonInternshipsCollector(GitHubRepoCollector):
    source_id = "github_offseason_internships"