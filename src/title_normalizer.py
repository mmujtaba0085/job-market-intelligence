"""
src/title_normalizer.py
───────────────────────
Intelligent job title normalization with weighted voting system.

Combines multiple normalization methods:
1. Exact mapping rules (curated dictionary)
2. Pattern-based normalization (abbreviations, case fixes)
3. Abbreviation expansion (SWE → Software Engineer)
4. Similarity scoring (optional ML-based suggestions)

Following the architecture pattern from country_detector.py with weighted voting.
"""

from typing import Optional, Tuple
import re
from difflib import SequenceMatcher
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

# Method weights (higher = more trusted)
WEIGHTS = {
    "exact_mapping": 1.0,      # Curated title mappings (highest trust)
    "pattern_match": 0.9,      # Regex-based normalization
    "abbreviation": 0.8,       # Expand common abbreviations
    "similarity": 0.7,         # ML similarity scoring (optional)
}

MIN_CONFIDENCE = 0.6  # Auto-apply threshold (60%)

# ═══════════════════════════════════════════════════════════════════
# NORMALIZATION RULES
# ═══════════════════════════════════════════════════════════════════

# Exact title mappings (weight: 1.0)
# Lowercase keys for case-insensitive matching
TITLE_MAPPINGS = {
    # Software Engineering Interns - consolidate many variants
    "software engineering intern": "Software Engineer Intern",
    "swe intern": "Software Engineer Intern",
    "software development intern": "Software Engineer Intern",
    "software developer intern": "Software Engineer Intern",
    "software intern": "Software Engineer Intern",
    "software engineer internship": "Software Engineer Intern",
    "software development engineer intern": "Software Engineer Intern",
    "simulation software engineer intern": "Software Engineer Intern",
    "engineering intern": "Software Engineer Intern",
    "intern": "Software Engineer Intern",  # Generic intern defaults to SWE
    
    # Software Engineers (consolidate variants)
    "software engineers": "Software Engineer",
    "software developer": "Software Engineer",
    "software development engineer": "Software Engineer",
    "swe": "Software Engineer",
    
    # Full Stack variants
    "full stack engineer": "Full Stack Engineer",
    "full stack developer": "Full Stack Engineer",
    "fullstack engineer": "Full Stack Engineer",
    "fullstack developer": "Full Stack Engineer",
    "full-stack engineer": "Full Stack Engineer",
    "full-stack developer": "Full Stack Engineer",
    "full stack software engineer": "Full Stack Engineer",
    "senior full - stack engineer": "Senior Full Stack Engineer",
    
    # Backend/Frontend
    "backend engineer": "Backend Engineer",
    "backend developer": "Backend Engineer",
    "back end engineer": "Backend Engineer",
    "backend software engineer": "Backend Engineer",
    "senior backend engineer": "Senior Backend Engineer",
    "senior python backend developer": "Senior Backend Engineer",
    
    "frontend engineer": "Frontend Engineer",
    "frontend developer": "Frontend Engineer",
    "front end engineer": "Frontend Engineer",
    "front-end engineer": "Frontend Engineer",
    
    # Data Science variants
    "data science intern": "Data Scientist Intern",
    "data scientist": "Data Scientist",
    "senior data scientist": "Senior Data Scientist",
    
    # Data Analyst variants
    "data analyst intern": "Data Analyst Intern",
    "data analyst": "Data Analyst",
    "business analyst intern": "Data Analyst Intern",
    "business analyst": "Data Analyst",
    
    # Data Engineer variants
    "data engineer": "Data Engineer",
    "data engineer intern": "Data Engineer Intern",
    "senior data engineer": "Senior Data Engineer",
    "tech lead databricks data engineer": "Data Engineer",
    
    # ML/AI roles - consolidate into ML Engineer
    "ml engineer": "Machine Learning Engineer",
    "ml intern": "Machine Learning Engineer Intern",
    "ai engineer": "Machine Learning Engineer",
    "ai/ml engineer": "Machine Learning Engineer",
    "machine learning engineer intern": "Machine Learning Engineer Intern",
    "senior machine learning engineer": "Senior Machine Learning Engineer",
    "principal machine learning engineer": "Principal Machine Learning Engineer",
    "ai intern": "Machine Learning Engineer Intern",
    "ai research intern": "Machine Learning Engineer Intern",
    
    # Computer Vision (consolidate into ML Engineer)
    "computer vision engineer": "Machine Learning Engineer",
    "senior computer vision engineer": "Senior Machine Learning Engineer",
    
    # Quant roles (common abbreviations)
    "qt intern": "Quantitative Trading Intern",
    "qr intern": "Quantitative Research Intern",
    "quant intern": "Quantitative Research Intern",
    "quantitative trading intern": "Quantitative Trading Intern",
    "quantitative research intern": "Quantitative Research Intern",
    
    # DevOps/SRE
    "devops engineer": "DevOps Engineer",
    "senior devops engineer": "Senior DevOps Engineer",
    "site reliability engineer": "Site Reliability Engineer",
    "sre": "Site Reliability Engineer",
    
    # QA/Testing
    "qa engineer": "Quality Assurance Engineer",
    "qa tester": "Quality Assurance Engineer",
    "test engineer": "Quality Assurance Engineer",
    
    # Product roles
    "product manager": "Product Manager",
    "pm": "Product Manager",
    "senior product manager": "Senior Product Manager",
    "product management intern": "Product Manager Intern",
    "product manager intern": "Product Manager Intern",
    "product engineer": "Product Engineer",
    
    # Design roles
    "ux designer": "UX Designer",
    "ui designer": "UI Designer",
    "ui/ux designer": "UI/UX Designer",
    "product designer": "Product Designer",
    "senior product designer": "Senior Product Designer",
    
    # Content/Writing roles - consolidate
    "content reviewer": "Content Writer",
    "copywriter": "Content Writer",
    "content writer": "Content Writer",
    
    # Sales/Business Development - consolidate
    "business development manager": "Business Development Manager",
    "inside sales contractor": "Sales Representative",
    "sales representative": "Sales Representative",
    
    # Operations/Support - consolidate
    "client support specialist": "Customer Support Specialist",
    "customer support specialist": "Customer Support Specialist",
    "patient care technician float": "Healthcare Support",
    "patient care technician": "Healthcare Support",
    
    # Retail/Operations - consolidate
    "retail manager - tire and battery center": "Retail Manager",
    "retail manager": "Retail Manager",
    "retail team member - cart attendant": "Retail Associate",
    "retail team member": "Retail Associate",
    "store merchandising team member": "Retail Associate",
    "courtesy clerk/grocery bagger": "Retail Associate",
    "department supervisor": "Retail Manager",
    
    # Administrative - consolidate
    "office assistant": "Administrative Assistant",
    "administrative assistant": "Administrative Assistant",
    "order management and operations manager": "Operations Manager",
    "operations manager": "Operations Manager",
    "product operation specialist - comprehensive search ope": "Operations Specialist",
    "product operation specialist": "Operations Specialist",
    
    # Brand/Marketing - consolidate
    "senior amazon brand manager": "Brand Manager",
    "brand manager": "Brand Manager",
    
    # Specialized roles
    "ai architect": "AI Architect",
    "forward deployed engineer": "Solutions Engineer",
    "founding engineer": "Founding Engineer",
    "staff software engineer": "Staff Software Engineer",
    
    # Insurance/Finance
    "underwriting manager, farm": "Underwriting Manager",
    "underwriting manager": "Underwriting Manager",
    
    # Specialized interns (keep as-is but clean up)
    "ai data operations internship - cambridge": "Data Operations Intern",
    "2026 phd scientist intern (competitive intelligence)": "Research Intern",
    "sustainability data analysis program intern": "Data Analyst Intern",
    "statistics & data science intern": "Data Scientist Intern",
    "computer vision & ai intern in bali": "Machine Learning Engineer Intern",
    "computer vision and machine learning intern": "Machine Learning Engineer Intern",
    
    # Research roles - consolidate
    "applied researcher i": "Research Scientist",
    "applied researcher ii": "Research Scientist",
    "applied researcher ii (ai foundations)": "Research Scientist",
    "applied ai engineer": "Machine Learning Engineer",
    
    # Sales roles - consolidate
    "business development representative - italy": "Sales Representative",
    "business development representative": "Sales Representative",
    "remote sales representative": "Sales Representative",
    
    # Specialized ML/CV roles - consolidate into ML Engineer
    "senior machine learning platform engineer": "Senior Machine Learning Engineer",
    "senior computer vision engineer, space": "Senior Machine Learning Engineer",
    "computer vision engineer": "Machine Learning Engineer",
    "computer vision": "Machine Learning Engineer",
    
    # Engineering management
    "senior engineering manager, compute": "Engineering Manager",
    "engineering manager": "Engineering Manager",
    
    # Specialized engineers - keep but clean
    "mdm data engineer": "Data Engineer",
    "senior datacenter network infrastructure engineer": "Infrastructure Engineer",
    "microsoft dynamics crm developer": "Software Engineer",
    
    # Program/Project Management
    "technical program management intern - ai tools development": "Product Manager Intern",
    
    # Quality/Testing
    "quality engineer": "Quality Assurance Engineer",
    
    # HR/Operations
    "hr operations manager, hong kong": "HR Operations Manager",
    "hr operations manager": "HR Operations Manager",
    "payroll specialist, total rewards": "Payroll Specialist",
    "payroll specialist": "Payroll Specialist",
    
    # Finance
    "assistant controller - emea": "Assistant Controller",
    "assistant controller": "Assistant Controller",
    
    # Talent/Recruiting
    "senior contract recruiter": "Recruiter",
    "recruiter": "Recruiter",
    "talent team lead, product, design, & engineering": "Recruiting Manager",
    
    # Product roles
    "product strategy and funding specialist": "Product Manager",
    "enterprise client strategist - uk/ireland": "Account Manager",
    
    # Tech leads - consolidate
    "tech lead - aws connect ai": "Tech Lead",
    
    # Manufacturing/Operations
    "cnc operator - king of prussia, pa": "CNC Operator",
    "cnc operator": "CNC Operator",
    "maintenance technician": "Maintenance Technician",
    
    # Power/Energy
    "power engineering intern - alexander city, al": "Engineering Intern",
    
    # Software interns with odd suffixes
    "software intern engineer": "Software Engineer Intern",
    "sw engineer intern -": "Software Engineer Intern",
    "sw engineer intern": "Software Engineer Intern",
    
    # Generic catch-all roles
    "multiple roles": "Multiple Roles",
    "engineer iii": "Software Engineer",
    "engineer": "Software Engineer",
    
    # Case fixes (common typos or variants)
    "software engineer - intern": "Software Engineer Intern",
    "intern - software engineer": "Software Engineer Intern",
    
    # Rating/Review roles
    "ai internet rater": "Content Reviewer",
    "internet rater": "Content Reviewer",
}

# Abbreviation expansions (regex patterns)
# These are applied to expand common abbreviations in titles
ABBREVIATION_MAP = {
    r'\bswe\b': 'Software Engineer',
    r'\bml\b': 'Machine Learning',
    r'\bai\b': 'AI',
    r'\bqa\b': 'Quality Assurance',
    r'\bui\b': 'UI',
    r'\bux\b': 'UX',
    r'\bsre\b': 'Site Reliability Engineer',
    r'\bdevops\b': 'DevOps',
    r'\bfullstack\b': 'Full Stack',
    r'\bfull-stack\b': 'Full Stack',
    r'\bfrontend\b': 'Frontend',
    r'\bbackend\b': 'Backend',
    r'\bpm\b': 'Product Manager',
}

# Seniority level patterns (for future enhancements)
SENIORITY_LEVELS = [
    "intern", "junior", "mid-level", "senior", "staff", 
    "principal", "lead", "head", "director", "vp", "chief"
]

# Common role categories (for taxonomy)
ROLE_CATEGORIES = {
    "engineer": ["engineer", "developer", "programmer", "coder"],
    "data": ["data scientist", "data analyst", "data engineer"],
    "design": ["designer", "ux", "ui"],
    "product": ["product manager", "product owner"],
    "qa": ["qa", "quality assurance", "tester", "test engineer"],
}

# ═══════════════════════════════════════════════════════════════════
# DETECTION METHODS
# ═══════════════════════════════════════════════════════════════════

def _method_exact_mapping(title: str) -> Optional[Tuple[str, float]]:
    """Check for exact curated mappings (case-insensitive)."""
    title_lower = title.lower().strip()
    
    if title_lower in TITLE_MAPPINGS:
        return (TITLE_MAPPINGS[title_lower], WEIGHTS["exact_mapping"])
    
    return None


def _method_pattern_normalization(title: str) -> Optional[Tuple[str, float]]:
    """
    Apply pattern-based normalization (case, spacing, punctuation).
    
    Fixes:
    - Remove country/location prefixes: "(USA) Manager" → "Manager"
    - Remove gender notations: "Engineer (M/W/D)" → "Engineer"
    - Remove year/degree prefixes: "(2027 Bachelor's Graduates) Analyst" → "Analyst"
    - Remove seniority prefixes: "(senior) Data Scientist" → "Senior Data Scientist"
    - Remove job reference numbers: "#ls359 - Engineer" → "Engineer"
    - Remove salary prefixes: "$150K - $250K Annual Total Comp (multiple)" → handled separately
    - Remove quotes: '"Business Intelligence Analyst "' → "Business Intelligence Analyst"
    - Remove location suffixes: "Engineer - New York, NY" → "Engineer"
    - Remove trailing hyphens/commas: "Intern -" → "Intern"
    - Collapse whitespace: "Software  Engineer" → "Software Engineer"
    - Standardize separators: "DevOps / SRE" → "DevOps/SRE"
    - Title case: "software ENGINEER" → "Software Engineer"
    """
    normalized = title.strip()
    
    # Remove leading/trailing quotes
    normalized = re.sub(r'^["\']|["\']$', '', normalized)
    
    # Remove country prefixes like "(USA)", "(UK)", etc.
    normalized = re.sub(r'^\([A-Z]{2,3}\)\s*', '', normalized)
    
    # Remove year/degree prefixes like "(2027 Bachelor's/Master's Graduates)"
    normalized = re.sub(r'^\(20\d{2}\s+[^)]+\)\s*', '', normalized)
    
    # Remove job reference numbers like "#ls359 - ", "REF123 - " (but not common words)
    # Only match if it starts with # or $ or has numbers/mixed case
    normalized = re.sub(r'^#[a-z0-9]{2,10}\s*-\s*', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'^\$[a-z0-9]{2,10}\s*-\s*', '', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'^[a-z]{2,4}\d+\s*-\s*', '', normalized, flags=re.IGNORECASE)
    
    # Remove salary prefixes (if entire title is just salary info, skip this job)
    if re.match(r'^\$[\d,k\s\-/]+', normalized, flags=re.IGNORECASE):
        # This is just a salary, not a real title
        return None
    
    # Remove gender notations like "(M/W/D)", "(all Genders)", "(F/M/X)", "(gn)"
    normalized = re.sub(r'\s*\([mfw/dgx]+\)\s*', ' ', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s*\(all\s+genders\)\s*', ' ', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'\s*\(gn\)\s*', ' ', normalized, flags=re.IGNORECASE)
    
    # Convert seniority prefixes to proper format: "(senior) Data Scientist" → "Senior Data Scientist"
    seniority_match = re.match(r'^\((\w+)\)\s+(.+)', normalized, flags=re.IGNORECASE)
    if seniority_match:
        seniority, rest = seniority_match.groups()
        # Check if it's a valid seniority level
        if seniority.lower() in ['junior', 'senior', 'lead', 'staff', 'principal']:
            normalized = f"{seniority.capitalize()} {rest}"
        else:
            # Not a seniority, just remove the parentheses
            normalized = f"{seniority} {rest}"
    
    # Reverse "Intern - Role" / "Intern, Role" patterns BEFORE any stripping
    # e.g., "Intern - Data Science" → "Data Science Intern"
    # e.g., "Summer Intern - Engineering" → "Engineering Intern"
    _intern_role = re.match(
        r'^((?:(?:Summer|Fall|Spring|Winter)\s+)?Intern)\s*[-,]\s*(.{3,60})$',
        normalized, re.IGNORECASE,
    )
    if _intern_role:
        suffix = _intern_role.group(2).strip().rstrip('-').strip()
        # Only reverse if suffix doesn't look like a geographic location
        if suffix and not re.search(r',\s*[A-Z]{2}\s*$', suffix):
            normalized = f"{suffix} Intern"

    # Remove geographic location suffixes only (require state code OR known location word)
    # e.g., "Engineer - New York, NY" → "Engineer" (has state code)
    # e.g., "Analyst - Remote" → "Analyst"
    # Does NOT strip "- Data Science", "- Product Management" (no state code)
    normalized = re.sub(r'\s*[-,]\s*[A-Za-z][a-z]+(?:\s+[A-Za-z][a-z]+)?,\s*[A-Z]{2}\s*$', '', normalized)
    normalized = re.sub(
        r'\s*[-,]\s*(?:Remote|Hybrid|Onsite|On-site|Virtual|Worldwide|Global|Anywhere|WFH)\s*$',
        '', normalized, flags=re.IGNORECASE,
    )
    
    # Remove trailing hyphens and whitespace
    normalized = re.sub(r'\s*-\s*$', '', normalized)
    normalized = re.sub(r',\s*$', '', normalized)
    
    # Fix common spacing issues
    normalized = re.sub(r'\s+', ' ', normalized)  # Collapse whitespace
    normalized = re.sub(r'\s*[/|]\s*', '/', normalized)  # "A / B" → "A/B"
    normalized = re.sub(r'\s*-\s*', ' - ', normalized)  # Standardize hyphen spacing
    
    # Remove redundant punctuation
    normalized = re.sub(r'\s+-\s+intern', ' Intern', normalized, flags=re.IGNORECASE)
    normalized = re.sub(r'intern\s+-\s+', 'Intern - ', normalized, flags=re.IGNORECASE)
    
    # Clean up the hyphen spacing we just added if it's at the end
    normalized = re.sub(r'\s*-\s*$', '', normalized)
    
    # Final trim
    normalized = normalized.strip()
    
    # Standardize case for common words (title case)
    # Keep acronyms uppercase (ML, AI, UI, UX, SRE, etc.)
    words = normalized.split()
    title_cased = []
    
    # Words that should stay lowercase
    lowercase_words = {'and', 'or', 'of', 'in', 'at', 'for', 'the', 'a', 'an'}
    
    for word in words:
        # Strip special chars for checking
        word_clean = re.sub(r'[^\w]', '', word)
        
        if word.lower() in lowercase_words:
            title_cased.append(word.lower())
        elif word_clean.isupper() and len(word_clean) <= 4:  
            # Keep short acronyms: "ML", "AI", "UI/UX", "SRE"
            title_cased.append(word)
        elif '/' in word:
            # Handle "UI/UX" style
            parts = word.split('/')
            title_cased.append('/'.join(p.upper() if len(p) <= 3 else p.capitalize() for p in parts))
        else:
            title_cased.append(word.capitalize())
    
    normalized = ' '.join(title_cased)
    
    # If changed, return with pattern weight
    if normalized != title:
        return (normalized, WEIGHTS["pattern_match"])
    
    return None


def _method_expand_abbreviations(title: str) -> Optional[Tuple[str, float]]:
    """
    Expand common abbreviations using regex patterns.
    
    Examples:
    - "SWE Intern" → "Software Engineer Intern"
    - "ML Engineer" → "Machine Learning Engineer"
    - "QA Tester" → "Quality Assurance Tester"
    """
    expanded = title
    changed = False
    
    for pattern, replacement in ABBREVIATION_MAP.items():
        new_expanded = re.sub(pattern, replacement, expanded, flags=re.IGNORECASE)
        if new_expanded != expanded:
            changed = True
            expanded = new_expanded
    
    if changed:
        # Apply title case after expansion
        words = expanded.split()
        title_cased = []
        
        for word in words:
            word_clean = re.sub(r'[^\w]', '', word)
            # Keep acronyms that are still uppercase
            if word_clean.isupper() and len(word_clean) <= 4:
                title_cased.append(word)
            else:
                title_cased.append(word.capitalize())
        
        expanded = ' '.join(title_cased)
        return (expanded, WEIGHTS["abbreviation"])
    
    return None


def _method_similarity_match(title: str, candidate_titles: list[str]) -> Optional[Tuple[str, float]]:
    """
    Find most similar normalized title using Levenshtein distance.
    
    This is an optional ML-based method for suggesting corrections.
    Use with use_similarity=True in normalize_title().
    
    Useful for:
    - Typo corrections: "Sofware Engineer" → "Software Engineer"
    - Close variants: "Software Eng Intern" → "Software Engineer Intern"
    """
    title_lower = title.lower().strip()
    
    best_match = None
    best_score = 0.0
    
    for candidate in candidate_titles:
        # Use SequenceMatcher for similarity (0.0 to 1.0)
        similarity = SequenceMatcher(None, title_lower, candidate.lower()).ratio()
        
        if similarity > best_score and similarity >= 0.85:  # 85% similar minimum
            best_score = similarity
            best_match = candidate
    
    if best_match:
        # Scale confidence by similarity score
        confidence = best_score * WEIGHTS["similarity"]
        return (best_match, confidence)
    
    return None


# ═══════════════════════════════════════════════════════════════════
# MAIN NORMALIZATION FUNCTION
# ═══════════════════════════════════════════════════════════════════

def normalize_title(raw_title: str, use_similarity: bool = False, 
                   candidate_titles: Optional[list[str]] = None) -> Tuple[str, float]:
    """
    Normalize job title using weighted voting from multiple methods.
    
    Args:
        raw_title: Original job title from source
        use_similarity: Enable ML similarity matching (slower, optional)
        candidate_titles: List of existing normalized titles for similarity matching
    
    Returns:
        (normalized_title, confidence_score)
        
        confidence_score is between 0.0 and 1.0:
        - 1.0: Exact mapping (curated rule)
        - 0.8-0.9: Abbreviation expansion or pattern match
        - 0.6-0.8: Medium confidence (similarity match)
        - 0.0: No normalization applied (use original)
    
    Examples:
        >>> normalize_title("Software Engineering Intern")
        ("Software Engineer Intern", 1.0)
        
        >>> normalize_title("SWE Intern")
        ("Software Engineer Intern", 0.8)
        
        >>> normalize_title("Unicorn Wrangler")
        ("Unicorn Wrangler", 0.0)
    """
    if not raw_title or raw_title.strip() == "":
        return ("Unknown", 0.0)
    
    # Collect votes from all methods
    votes: dict[str, float] = {}  # {normalized_title: total_weight}
    
    # Method 1: Exact mapping (highest priority)
    result = _method_exact_mapping(raw_title)
    if result:
        normalized, weight = result
        votes[normalized] = votes.get(normalized, 0) + weight
    
    # Method 2: Pattern normalization (case, spacing, punctuation)
    result = _method_pattern_normalization(raw_title)
    if result:
        normalized, weight = result
        votes[normalized] = votes.get(normalized, 0) + weight
    
    # Method 3: Abbreviation expansion
    result = _method_expand_abbreviations(raw_title)
    if result:
        normalized, weight = result
        votes[normalized] = votes.get(normalized, 0) + weight
    
    # Method 4: Similarity matching (optional, slower)
    if use_similarity and candidate_titles:
        result = _method_similarity_match(raw_title, candidate_titles)
        if result:
            normalized, weight = result
            votes[normalized] = votes.get(normalized, 0) + weight
    
    # No votes = use original title (no normalization rule matched)
    if not votes:
        return (raw_title, 0.0)  # 0 confidence = needs manual review
    
    # Winner = title with highest total weight
    winner = max(votes, key=votes.get)
    total_weight = votes[winner]
    
    # Confidence = total weight (capped at 1.0)
    # Multiple methods can stack (e.g., exact + pattern = 1.9 → capped to 1.0)
    confidence = min(total_weight, 1.0)
    
    return (winner, confidence)


# ═══════════════════════════════════════════════════════════════════
# CONVENIENCE FUNCTIONS
# ═══════════════════════════════════════════════════════════════════

def should_auto_apply(confidence: float) -> bool:
    """
    Check if confidence is high enough for auto-application.
    
    Returns True if confidence >= 0.6 (60% threshold).
    Low-confidence normalizations should be flagged for manual review.
    """
    return confidence >= MIN_CONFIDENCE


def get_confidence_label(confidence: float) -> str:
    """
    Get human-readable confidence label for UI display.
    
    Returns:
        "high" (≥0.9), "medium" (≥0.6), or "low" (<0.6)
    """
    if confidence >= 0.9:
        return "high"
    elif confidence >= 0.6:
        return "medium"
    else:
        return "low"


def extract_seniority_level(title: str) -> Optional[str]:
    """
    Extract seniority level from title (for analytics).
    
    Examples:
        >>> extract_seniority_level("Software Engineer Intern")
        "Intern"
        
        >>> extract_seniority_level("Senior Backend Engineer")
        "Senior"
        
        >>> extract_seniority_level("Staff Machine Learning Engineer")
        "Staff"
    
    Returns None if no seniority level detected.
    """
    title_lower = title.lower()
    
    for level in SENIORITY_LEVELS:
        if level in title_lower:
            return level.capitalize()
    
    return None


def get_normalization_stats(titles: list[str]) -> dict:
    """
    Analyze normalization coverage for a list of titles.
    
    Useful for validation and monitoring.
    
    Returns:
        {
            "total": int,
            "normalized": int,  # changed from original
            "unchanged": int,   # no rule matched
            "high_confidence": int,
            "medium_confidence": int,
            "low_confidence": int,
            "coverage_pct": float,
        }
    """
    stats = {
        "total": len(titles),
        "normalized": 0,
        "unchanged": 0,
        "high_confidence": 0,
        "medium_confidence": 0,
        "low_confidence": 0,
    }
    
    for title in titles:
        normalized, confidence = normalize_title(title)
        
        if normalized != title:
            stats["normalized"] += 1
        else:
            stats["unchanged"] += 1
        
        label = get_confidence_label(confidence)
        if label == "high":
            stats["high_confidence"] += 1
        elif label == "medium":
            stats["medium_confidence"] += 1
        else:
            stats["low_confidence"] += 1
    
    stats["coverage_pct"] = (stats["normalized"] / stats["total"] * 100) if stats["total"] > 0 else 0.0
    
    return stats


# ═══════════════════════════════════════════════════════════════════
# MANUAL OVERRIDE SUPPORT
# ═══════════════════════════════════════════════════════════════════

def add_mapping(raw_title: str, normalized_title: str) -> None:
    """
    Add a new manual mapping to TITLE_MAPPINGS.
    
    This allows the admin panel to dynamically add new rules.
    Note: This only affects the current runtime. For persistence,
    mappings should be added to the source code or a config file.
    
    Args:
        raw_title: Original title variant
        normalized_title: Canonical normalized form
    """
    TITLE_MAPPINGS[raw_title.lower().strip()] = normalized_title
    logger.info(f"Added title mapping: '{raw_title}' → '{normalized_title}'")


def get_all_mappings() -> dict[str, str]:
    """
    Get all current title mappings (for export/admin UI).
    
    Returns:
        Dictionary of {raw_title_lower: normalized_title}
    """
    return TITLE_MAPPINGS.copy()


# ═══════════════════════════════════════════════════════════════════
# VALIDATION & TESTING
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    # Quick test cases for development
    test_cases = [
        ("Software Engineering Intern", "Software Engineer Intern", 1.0),
        ("SWE Intern", "Software Engineer Intern", 0.8),
        ("software engineer intern", "Software Engineer Intern", 1.0),
        ("Multiple Roles", "Multiple Roles", 1.0),
        ("multiple roles", "Multiple Roles", 1.0),
        ("ML Engineer", "Machine Learning Engineer", 0.8),
        ("QA Tester", "Quality Assurance Tester", 0.8),
        ("Full Stack Developer", "Full Stack Engineer", 1.0),
        ("fullstack engineer", "Full Stack Engineer", 1.0),
        ("DevOps Engineer", "DevOps Engineer", 1.0),
        ("Unicorn Wrangler", "Unicorn Wrangler", 0.0),  # Unknown title
    ]
    
    print("Testing title normalization:\n")
    for raw, expected, min_confidence in test_cases:
        normalized, confidence = normalize_title(raw)
        status = "✓" if normalized == expected and confidence >= min_confidence else "✗"
        print(f"{status} '{raw}' → '{normalized}' (confidence: {confidence:.2f})")
        if normalized != expected:
            print(f"  Expected: '{expected}'")
    
    # Test seniority extraction
    print("\nTesting seniority extraction:")
    seniority_tests = [
        "Software Engineer Intern",
        "Senior Backend Engineer",
        "Staff Machine Learning Engineer",
        "Backend Engineer",
    ]
    for title in seniority_tests:
        level = extract_seniority_level(title)
        print(f"  '{title}' → Seniority: {level or 'None'}")
