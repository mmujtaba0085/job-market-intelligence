"""
config/markets.py
─────────────────
TARGET_MARKETS: the single source of truth for which markets the engine tracks.

Adding a new market = add a dict here. Zero code changes elsewhere.
"""

TARGET_MARKETS: list[dict] = [
    {
        # ── Identity ─────────────────────────────────────────────────────────
        "market_id": "ai_ml_global",
        "display_name": "AI & Machine Learning (Global)",

        # ── Search keywords ───────────────────────────────────────────────────
        "keywords": [
            # Core ML/AI roles
            "machine learning", "deep learning", "computer vision",
            "natural language processing", "nlp",
            "large language model", "llm", "mlops",
            "data scientist", "data science",
            "ai", "ml",
            "artificial intelligence", "neural network",
            # Frameworks
            "pytorch", "tensorflow", "scikit-learn",
            # Role variants
            "python machine", "ml engineer",
            # New: role titles
            "ai engineer", "research engineer", "research scientist",
            "applied scientist", "applied ml",
            "llm engineer", "computer vision engineer", "nlp engineer",
            "data engineer", "ml platform", "ml infrastructure",
            # New: trending topics
            "generative ai", "gen ai",
            "hugging face", "transformers", "langchain", "llamaindex",
            "diffusion", "rag", "retrieval augmented",
            "fine-tuning", "reinforcement learning", "multimodal",
        ],

        # ── Crawler keywords (for full-catalogue crawler relevance filtering) ─
        "crawl_keywords": [
            "machine learning", "deep learning", "data scientist",
            "nlp", "natural language processing", "computer vision",
            "llm", "large language model", "mlops",
            "ai engineer", "ml engineer", "artificial intelligence",
            "neural network", "data science", "analytics",
            # New additions
            "research engineer", "generative ai", "data engineer",
            "llm engineer", "applied scientist", "multimodal",
        ],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["United States", "United Kingdom", "Germany", "Canada"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,          # False = include all, True = remote-only
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        "max_jobs_per_source": 500,

        # ── Per-source overrides (only lower, never raise, the global cap) ──────
        "source_overrides": {
            "adzuna": {"max_jobs": 150},
            # Himalayas was re-enabled at some point after being disabled and,
            # uncapped, grew to 46.9% of all active jobs (82.8% of the last-
            # month Active window) across just this market + swe_backend_global
            # - confirmed against production on 2026-07-16. 50/market caps
            # future growth to roughly a tenth of its former per-cycle yield.
            "himalayas": {"max_jobs": 50},
        },
    },

    {
        # ── Identity ─────────────────────────────────────────────────────────
        "market_id": "swe_backend_global",
        "display_name": "Software Engineering & Backend (Global)",

        # ── Search keywords ───────────────────────────────────────────────────
        "keywords": [
            # Core SWE roles
            "software engineer", "software developer", "backend engineer",
            "backend developer", "full stack engineer", "full stack developer",
            "fullstack engineer", "frontend engineer", "frontend developer",
            # Systems & infrastructure
            "platform engineer", "infrastructure engineer", "site reliability",
            "sre", "devops engineer", "cloud engineer",
            "systems engineer", "distributed systems",
            # DevOps & cloud tools
            "kubernetes", "docker", "terraform", "aws", "gcp", "azure",
            "ci/cd", "devsecops",
            # Languages / stacks
            "python developer", "golang", "rust engineer",
            "java engineer", "scala engineer", "typescript",
            "node.js", "react engineer",
            # Data engineering adjacent
            "data platform", "data infrastructure", "streaming engineer",
            "kafka", "spark engineer", "dbt",
            # API & services
            "api engineer", "microservices", "grpc",
            # Security
            "security engineer", "appsec",
        ],

        # ── Crawler keywords ──────────────────────────────────────────────────
        "crawl_keywords": [
            "software engineer", "backend engineer", "full stack",
            "platform engineer", "devops", "cloud engineer",
            "infrastructure engineer", "site reliability", "sre",
            "kubernetes", "docker", "terraform",
            "python developer", "golang", "rust",
            "data platform", "data infrastructure",
        ],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["United States", "United Kingdom", "Germany", "Canada"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        "max_jobs_per_source": 500,

        # ── Per-source overrides (only lower, never raise, the global cap) ──────
        "source_overrides": {
            "himalayas": {"max_jobs": 50},
        },
    },

    {
        # ── Identity ─────────────────────────────────────────────────────────
        "market_id": "pakistan_jobs_all",
        "display_name": "Pakistan Jobs (All Categories)",

        # No keyword filter — this market intentionally captures every job
        # category from the source (government, banking, medical, teaching,
        # etc.), not just tech roles.
        "keywords": [],
        "crawl_keywords": [],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["Pakistan"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        # Effectively unbounded — PakistanJobsBankCollector paces itself via its
        # own per-run date-page budget (see _MAX_DATES_PER_RUN), not this cap.
        "max_jobs_per_source": 1_000_000,

        # ── Restrict this market to just this source ─────────────────────────
        # Without this, every other registered collector (Remotive, Arbeitnow,
        # JSearch, ...) would also run against this market, which makes no
        # sense for a Pakistan-specific, all-category archive.
        "source_allowlist": ["pakistanjobsbank"],
    },

    {
        # ── Identity ─────────────────────────────────────────────────────────
        "market_id": "pakistan_company_boards",
        "display_name": "Pakistan Company Career Boards",

        # No keyword filter — these are curated single-company catalogues
        # (PMCL/Jazz alone spans sales, finance, and marketing roles
        # alongside tech; 10Pearls mixes Solutions Architect with Account
        # Manager and Talent Acquisition), not a generic keyword-matched
        # search. A few of these companies (Motive, S&P
        # Global, Veeam, Software Finder) are large global employers whose
        # collectors already filter down to Pakistan-relevant postings
        # before returning results, so no additional filtering belongs here.
        "keywords": [],
        "crawl_keywords": [],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["Pakistan"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        # Contour is the largest single board at ~108 jobs; comfortably
        # under this per-source cap with room to grow.
        "max_jobs_per_source": 500,

        # ── Restrict this market to just these sources ────────────────────────
        "source_allowlist": [
            "devsinc", "pmcl", "vyro", "kodifly", "veeam", "motive",
            "spglobal", "contour", "venturedive", "carbonteq",
            "softwarefinder", "dpl", "xgrid", "tenpearls",
        ],
    },

    # ── Future market template (commented out) ────────────────────────────────
    # {
    #     "market_id": "devops_remote",
    #     "display_name": "DevOps & Cloud (Remote Global)",
    #     "keywords": ["docker", "kubernetes", "aws", "terraform", "devops", "sre"],
    #     "countries": ["Global"],
    #     "remote_filter": True,
    #     "experience_levels": ["mid", "senior"],
    #     "salary_required": True,
    #     "max_jobs_per_source": 200,
    # },
]
