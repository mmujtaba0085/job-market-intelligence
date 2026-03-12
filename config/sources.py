"""
config/sources.py
─────────────────
ALLOWED_SOURCES: the explicit allowlist of permitted data sources.

Compliance rules (enforced by base_collector.py):
  - Only sources in this list may be used.
  - rate_limit_per_minute is enforced via time.sleep in every collector.
  - If robots_txt_allowed is False, the collector will refuse to run.
  - No login-gated sources. No CAPTCHA bypass. No headless browser automation.
  - tos_note must be populated before a source is registered.

Adding a new source = add a dict here. No other code change needed.
"""

ALLOWED_SOURCES: list[dict] = [
    {
        "source_id": "remotive",
        "display_name": "Remotive",
        "source_type": "API",                   # API | HTML | RSS
        "base_url": "https://remotive.com/api/remote-jobs",
        "rate_limit_per_minute": 20,
        "robots_txt_allowed": True,              # verified: no restriction on /api/*
        "requires_auth": False,
        "tos_note": (
            "Public REST API. No ToS restriction on reasonable automated access. "
            "Remotive encourages developer use of their API."
        ),
        "enabled": True,
    },
    {
        "source_id": "jsearch",
        "display_name": "JSearch (RapidAPI)",
        "source_type": "API",
        "base_url": "https://jsearch.p.rapidapi.com",
        "rate_limit_per_minute": 30,
        "robots_txt_allowed": True,
        "requires_auth": True,                   # JSEARCH_API_KEY required in .env
        "tos_note": (
            "RapidAPI subscriber terms apply. JSearch aggregates publicly "
            "listed jobs from Indeed, LinkedIn, Glassdoor, and others. "
            "No direct scraping — API access only."
        ),
        "enabled": False,
    },
    {
        "source_id": "arbeitnow",
        "display_name": "Arbeitnow",
        "source_type": "API",
        "base_url": "https://www.arbeitnow.com/api/job-board-api",
        "rate_limit_per_minute": 30,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public job board API, primarily EU jobs. No authentication required. "
            "Free to use for research and aggregation purposes."
        ),
        "enabled": True,  # Enable manually when ready
    },
    {
        "source_id": "usajobs",
        "display_name": "USA Jobs",
        "source_type": "API",
        "base_url": "https://data.usajobs.gov/api/Search",
        "rate_limit_per_minute": 20,
        "robots_txt_allowed": True,
        "requires_auth": True,  # USAJOBS_API_KEY + USAJOBS_USER_AGENT required
        "tos_note": (
            "Official US government jobs API. Free API key required from "
            "developer.usajobs.gov. Public data, explicitly designed for "
            "third-party applications."
        ),
        "enabled": False,  # Enable after adding API key to .env
    },
    {
        "source_id": "themuse",
        "display_name": "The Muse",
        "source_type": "API",
        "base_url": "https://www.themuse.com/api/public/jobs",
        "rate_limit_per_minute": 50,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public API for curated job listings. No authentication required. "
            "The Muse provides structured job data for aggregators."
        ),
        "enabled": True,
    },
    {
        "source_id": "graphqljobs",
        "display_name": "GraphQL Jobs",
        "source_type": "API",
        "base_url": "https://api.graphql.jobs/",
        "rate_limit_per_minute": 0,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "GraphQL-specific job board with public API. No authentication required. "
            "Free to use for job aggregation and research."
        ),
        "enabled": False,  # DNS error - api.graphql.jobs doesn't resolve
    },
    {
        "source_id": "himalayas",
        "display_name": "Himalayas",
        "source_type": "API",
        "base_url": "https://himalayas.app/jobs/api",
        "rate_limit_per_minute": 30,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public JSON API for remote job listings. No authentication required. "
            "Supports pagination with limit/offset parameters."
        ),
        "enabled": False,  # API slow/unreliable, disabled
    },
    {
        "source_id": "himalayas_rss",
        "display_name": "Himalayas RSS",
        "source_type": "RSS",
        "base_url": "https://himalayas.app/jobs/feed",
        "rate_limit_per_minute": 10,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public RSS feed for remote job listings. No authentication required. "
            "Backup source to Himalayas JSON API."
        ),
        "enabled": False,
    },
    {
        "source_id": "jobicy",
        "display_name": "Jobicy",
        "source_type": "API",
        "base_url": "GET https://jobicy.com/api/v2/remote-jobs",
        "rate_limit_per_minute": 30,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public API for remote job listings. No authentication required. "
            "Supports keyword/tag and geographic filtering."
        ),
        "enabled": False, 
    },
    {
        "source_id": "hireweb3",
        "display_name": "HireWeb3",
        "source_type": "RSS",
        "base_url": "https://hireweb3.io/job/rss",
        "rate_limit_per_minute": 20,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public RSS feed for Web3/blockchain job listings. No authentication required. "
            "Includes custom hireweb3Jobs namespace fields."
        ),
        "enabled": True,
    },
    {
        "source_id": "adzuna",
        "display_name": "Adzuna API",
        "source_type": "API",
        "base_url": "https://api.adzuna.com/v1/api/jobs",
        "rate_limit_per_minute": 20,
        "robots_txt_allowed": True,
        "requires_auth": True,  # ADZUNA_APP_ID + ADZUNA_APP_KEY required in .env
        "tos_note": (
            "Official public API. Free tier available. "
            "Aggregates jobs from multiple sources across multiple countries."
        ),
        "enabled": False,  # Enable after adding API credentials to .env
    },
    {
        "source_id": "findwork",
        "display_name": "Findwork API",
        "source_type": "API",
        "base_url": "https://findwork.dev/api/jobs/",
        "rate_limit_per_minute": 40,
        "robots_txt_allowed": True,
        "requires_auth": True,  # FINDWORK_API_KEY required in .env
        "tos_note": (
            "Official public API. Free tier available with 60 req/min limit. "
            "Curated remote and on-site job listings with quality focus."
        ),
        "enabled": True,  # Enable after adding API key to .env
    },
    {
        "source_id": "findwork_crawl",
        "display_name": "Findwork Crawler",
        "source_type": "HTML",
        "base_url": "https://findwork.dev/",
        "rate_limit_per_minute": 30,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": (
            "Public HTML crawling mode for Findwork full catalogue. "
            "Respectful crawling with rate limiting. No authentication required."
        ),
        "enabled": True,
    },
    {
        "source_id": "jooble",
        "display_name": "Jooble API",
        "source_type": "API",
        "base_url": "https://jooble.org/api/",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": True,  # JOOBLE_API_KEY required in .env
        "tos_note": (
            "Official public API. Free tier available. "
            "Job aggregator with global coverage and location-based search."
        ),
        "enabled": True,  # Disabled - very slow response times
    },

    # ── GitHub live-updated internship lists (raw markdown) ─────────────────────

    {
        "source_id": "github_simplify_2026",
        "display_name": "GitHub: Simplify Summer 2026 Internships",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repo README lists internships. Pulled via raw.githubusercontent.com for research/analytics.",
        "enabled": True,
        "repos": [
            {
                "repo_owner": "SimplifyJobs",
                "repo_name": "Summer2026-Internships",
                "branch_candidates": ["dev", "main", "master"],
                "paths": ["README.md"],
                "parser_mode": "auto",
            }
        ],
    },

    {
        "source_id": "github_vansh_2026",
        "display_name": "GitHub: Vansh Summer 2026 Internships",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repo table of internships. Pulled via raw.githubusercontent.com for research/analytics.",
        "enabled": True,
        "repos": [
            {
                "repo_owner": "vanshb03",
                "repo_name": "Summer2026-Internships",
                "branch_candidates": ["main", "master"],
                "paths": ["README.md"],
                "parser_mode": "markdown_pipe_table",
            }
        ],
    },

    {
        "source_id": "github_speedyapply_2026",
        "display_name": "GitHub: SpeedyApply 2026 (AI + SWE)",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repos with job/intern tables across multiple markdown files. Pulled via raw.githubusercontent.com.",
        "enabled": True,
        "repos": [
            {
                "repo_owner": "speedyapply",
                "repo_name": "2026-AI-College-Jobs",
                "branch_candidates": ["main", "master"],
                "paths": ["README.md", "NEW_GRAD_USA.md", "NEW_GRAD_INTL.md", "INTERN_INTL.md"],
                "parser_mode": "markdown_pipe_table",
            },
            {
                "repo_owner": "speedyapply",
                "repo_name": "2026-SWE-College-Jobs",
                "branch_candidates": ["main", "master"],
                "paths": ["README.md"],
                "follow_md_links": True,          # follow internal .md links found in README
                "max_linked_files": 12,
                "parser_mode": "markdown_pipe_table",
            },
        ],
    },

    {
        "source_id": "github_jobright_2026",
        "display_name": "GitHub: Jobright 2026 Internships (All Tracks)",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repos with internship tables by track. Pulled via raw.githubusercontent.com.",
        "enabled": True,
        "repos": [
            {"repo_owner": "jobright-ai", "repo_name": "2026-Software-Engineer-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
            {"repo_owner": "jobright-ai", "repo_name": "2026-Engineer-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
            {"repo_owner": "jobright-ai", "repo_name": "2026-Product-Management-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
            {"repo_owner": "jobright-ai", "repo_name": "2026-Data-Analysis-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
            {"repo_owner": "jobright-ai", "repo_name": "2026-Business-Analyst-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
            {"repo_owner": "jobright-ai", "repo_name": "2026-Consultant-Internship", "branch_candidates": ["main","master"], "paths": ["README.md"], "parser_mode": "jobright_daily_table"},
        ],
    },

    {
        "source_id": "github_nuft_quant_2026",
        "display_name": "GitHub: NorthwesternFinTech Quant Internships 2026",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repo listing quant internships. Pulled via raw.githubusercontent.com.",
        "enabled": True,
        "repos": [
            {
                "repo_owner": "northwesternfintech",
                "repo_name": "2026QuantInternships",
                "branch_candidates": ["main","master"],
                "paths": ["README.md"],
                "parser_mode": "nuft_quant",
            }
        ],
    },

    {
        "source_id": "github_offseason_internships",
        "display_name": "GitHub: Off-Season Internships (SharunKumar)",
        "source_type": "GITHUB_LIST",
        "base_url": "https://raw.githubusercontent.com",
        "rate_limit_per_minute": 60,
        "robots_txt_allowed": True,
        "requires_auth": False,
        "tos_note": "Public GitHub repo with off-season internship list. Pulled via raw.githubusercontent.com.",
        "enabled": True,
        "repos": [
            {
                "repo_owner": "sharunkumar",
                "repo_name": "Summer-Internships",
                "branch_candidates": ["dev","main","master"],
                "paths": ["README-Off-Season.md"],
                "parser_mode": "html_table",
            }
        ],
    },

    # ── Future sources (add below, keep disabled until vetted) ────────────────
]

# ── Quick lookup helpers ──────────────────────────────────────────────────────
SOURCES_BY_ID: dict[str, dict] = {s["source_id"]: s for s in ALLOWED_SOURCES}
ENABLED_SOURCES: list[dict] = [s for s in ALLOWED_SOURCES if s.get("enabled", True)]
