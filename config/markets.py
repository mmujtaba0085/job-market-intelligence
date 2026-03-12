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
            "machine learning", "deep learning", "computer vision",
            "natural language processing", "nlp",
            "large language model", "llm", "mlops", 
            "data scientist", "data science",
            "ai", "ml",  # Add shorter variants
            "artificial intelligence", "neural network",
            "pytorch", "tensorflow", "scikit-learn",  # Add frameworks
            "python machine", "ml engineer",  # Add role variants
        ],
        
        # ── Crawler keywords (for full-catalogue crawler relevance filtering) ─
        "crawl_keywords": [
            "machine learning", "deep learning", "data scientist",
            "nlp", "natural language processing", "computer vision",
            "llm", "large language model", "mlops",
            "ai engineer", "ml engineer", "artificial intelligence",
            "neural network", "data science", "analytics",
        ],

        # ── Geography ─────────────────────────────────────────────────────────
        "countries": ["United States", "United Kingdom", "Germany", "Canada"],

        # ── Filters ───────────────────────────────────────────────────────────
        "remote_filter": False,          # False = include all, True = remote-only
        "experience_levels": ["entry", "mid", "senior"],
        "salary_required": False,

        # ── Collection limits (per source per run) ────────────────────────────
        "max_jobs_per_source": 500,
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
