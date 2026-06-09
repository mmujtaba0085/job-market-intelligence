"""Friendly ISCO-inspired job-market taxonomy used after collection."""

from __future__ import annotations


JOB_MARKETS: list[dict] = [
    {"market_id": "it", "name": "IT & Software", "parent_id": None, "isco": "25", "keywords": []},
    {"market_id": "it.software", "name": "Software Engineering", "parent_id": "it", "isco": "2512",
     "keywords": ["software engineer", "software developer", "backend", "frontend", "full stack", "mobile developer", "ios", "android"]},
    {"market_id": "it.data", "name": "Data, AI & Machine Learning", "parent_id": "it", "isco": "2511",
     "keywords": ["data scientist", "data science", "data analyst", "data analytics", "data engineer",
                  "machine learning", "ml engineer", "ai engineer", "ai architect",
                  "artificial intelligence", "computer vision", "business intelligence", "llm",
                  "analytics engineer"]},
    {"market_id": "it.infrastructure", "name": "Cloud, DevOps & Security", "parent_id": "it", "isco": "252",
     "keywords": ["devops", "site reliability", "sre", "cloud engineer", "platform engineer", "cybersecurity", "security engineer", "network engineer"]},
    {"market_id": "it.product", "name": "Product & Technical Leadership", "parent_id": "it", "isco": "1330",
     "keywords": ["product manager", "technical product", "engineering manager", "cto", "chief technology", "vp engineering"]},

    {"market_id": "healthcare", "name": "Healthcare & Medical", "parent_id": None, "isco": "22", "keywords": []},
    {"market_id": "healthcare.clinical", "name": "Clinical Care", "parent_id": "healthcare", "isco": "22",
     "keywords": ["physician", "doctor", "nurse", "clinical", "pharmacist", "therapist", "dentist", "medical assistant"]},
    {"market_id": "healthcare.life_sciences", "name": "Life Sciences & Research", "parent_id": "healthcare", "isco": "2131",
     "keywords": ["biologist", "biotech", "life sciences", "laboratory", "research scientist", "bioinformatics"]},

    {"market_id": "engineering", "name": "Engineering & Manufacturing", "parent_id": None, "isco": "21,31", "keywords": []},
    {"market_id": "engineering.core", "name": "Core Engineering", "parent_id": "engineering", "isco": "214",
     "keywords": ["mechanical engineer", "electrical engineer", "civil engineer", "chemical engineer", "aerospace engineer", "structural engineer"]},
    {"market_id": "engineering.manufacturing", "name": "Manufacturing & Quality", "parent_id": "engineering", "isco": "31",
     "keywords": ["manufacturing", "production engineer", "quality engineer", "process engineer", "industrial engineer", "maintenance engineer"]},

    {"market_id": "business", "name": "Business & Finance", "parent_id": None, "isco": "24", "keywords": []},
    {"market_id": "business.finance", "name": "Finance, Accounting & Quant", "parent_id": "business", "isco": "241",
     "keywords": ["accountant", "finance", "financial analyst", "investment", "quantitative", "quant trader", "auditor", "controller"]},
    {"market_id": "business.sales", "name": "Sales & Marketing", "parent_id": "business", "isco": "243",
     "keywords": ["sales", "account executive", "business development", "marketing", "growth manager", "brand manager"]},
    {"market_id": "business.people", "name": "People, HR & Recruiting", "parent_id": "business", "isco": "2423",
     "keywords": ["human resources", "people operations", "recruiter", "talent acquisition", "hr manager"]},
    {"market_id": "business.consulting", "name": "Consulting & Analysis", "parent_id": "business", "isco": "2421",
     "keywords": ["consultant", "business analyst", "management consultant", "strategy analyst", "operations analyst"]},

    {"market_id": "operations", "name": "Operations", "parent_id": None, "isco": "13,33,43", "keywords": []},
    {"market_id": "operations.supply", "name": "Supply Chain & Logistics", "parent_id": "operations", "isco": "1324",
     "keywords": ["supply chain", "logistics", "procurement", "warehouse", "transportation", "inventory"]},
    {"market_id": "operations.service", "name": "Customer & Business Operations", "parent_id": "operations", "isco": "33,42",
     "keywords": ["customer success", "customer support", "operations manager", "program manager", "project manager", "office manager"]},

    {"market_id": "creative", "name": "Creative", "parent_id": None, "isco": "26", "keywords": []},
    {"market_id": "creative.design", "name": "Design & User Experience", "parent_id": "creative", "isco": "2166",
     "keywords": ["designer", "ux", "ui", "user experience", "product design", "graphic design"]},
    {"market_id": "creative.content", "name": "Content, Media & Communications", "parent_id": "creative", "isco": "264",
     "keywords": ["writer", "editor", "content", "communications", "journalist", "social media", "copywriter"]},

    {"market_id": "education", "name": "Education", "parent_id": None, "isco": "23", "keywords": []},
    {"market_id": "education.teaching", "name": "Teaching & Learning", "parent_id": "education", "isco": "23",
     "keywords": ["teacher", "professor", "instructor", "lecturer", "tutor", "curriculum", "learning designer"]},

    {"market_id": "legal", "name": "Legal", "parent_id": None, "isco": "261", "keywords": []},
    {"market_id": "legal.practice", "name": "Legal Practice & Compliance", "parent_id": "legal", "isco": "261",
     "keywords": ["lawyer", "attorney", "legal counsel", "paralegal", "compliance", "legal assistant", "privacy counsel"]},
]

MARKETS_BY_ID = {market["market_id"]: market for market in JOB_MARKETS}
LEAF_MARKETS = [market for market in JOB_MARKETS if market["keywords"]]
