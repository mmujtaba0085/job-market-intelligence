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
        "market_id": "tenpearls_jobs",
        "display_name": "10Pearls Careers",

        # No keyword filter — 10Pearls' own careers board is a small,
        # finite, single-company catalogue that mixes tech and non-tech
        # roles (Solutions Architect alongside Account Manager, Talent
        # Acquisition, ...). Capturing everything is the point of tracking
        # this specific company, not just its roles that happen to match
        # generic global tech keywords.
        "keywords": [],
        "crawl_keywords": [],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["Pakistan", "United Kingdom"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        # Effectively unbounded — the whole board is one page, currently ~70
        # jobs; TenPearlsCollector always fetches everything currently listed.
        "max_jobs_per_source": 1_000,

        # ── Restrict this market to just this source ─────────────────────────
        "source_allowlist": ["tenpearls"],
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
