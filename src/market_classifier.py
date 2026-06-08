"""Deterministic local job-market classifier with auditable evidence."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

from config.job_markets import LEAF_MARKETS

_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


@dataclass(frozen=True)
class MarketMatch:
    market_id: str | None
    confidence: float
    tags: tuple[str, ...]
    method: str
    evidence: tuple[str, ...]


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall((text or "").lower()))


def classify_job(title: str, description: str = "", source_tags: list[str] | None = None) -> MarketMatch:
    """Return a primary leaf and related leaf tags; low-confidence jobs stay unclassified."""
    title_lower = (title or "").lower()
    desc_lower = (description or "")[:4000].lower()
    source_text = " ".join(source_tags or []).lower()
    title_tokens = _tokens(title_lower)
    scores: list[tuple[float, str, list[str]]] = []

    for market in LEAF_MARKETS:
        score = 0.0
        evidence: list[str] = []
        for keyword in market["keywords"]:
            phrase = keyword.lower()
            phrase_tokens = _tokens(phrase)
            if phrase in title_lower:
                score += 5.0
                evidence.append(f"title:{keyword}")
            elif phrase_tokens and phrase_tokens <= title_tokens:
                score += 3.5
                evidence.append(f"title_tokens:{keyword}")
            elif phrase in source_text:
                score += 2.5
                evidence.append(f"source_tag:{keyword}")
            elif phrase in desc_lower:
                score += 1.0
                evidence.append(f"description:{keyword}")
            else:
                similarity = SequenceMatcher(None, title_lower, phrase).ratio()
                if similarity >= 0.78:
                    score += similarity * 2.0
                    evidence.append(f"title_similarity:{keyword}")
        if score:
            scores.append((score, market["market_id"], evidence))

    if not scores:
        return MarketMatch(None, 0.0, (), "local_hybrid_v1", ())

    scores.sort(reverse=True)
    top_score, top_id, top_evidence = scores[0]
    runner_up = scores[1][0] if len(scores) > 1 else 0.0
    confidence = min(0.99, 0.42 + 0.10 * math.log1p(top_score) + 0.04 * max(0.0, top_score - runner_up))
    if top_score < 2.0 or confidence < 0.62:
        return MarketMatch(None, round(confidence, 3), (), "local_hybrid_v1", tuple(top_evidence[:5]))

    tags = tuple(market_id for score, market_id, _ in scores[1:5] if score >= max(1.0, top_score * 0.35))
    return MarketMatch(top_id, round(confidence, 3), tags, "local_hybrid_v1", tuple(top_evidence[:8]))


def summarize_unknown_titles(titles: list[str], limit: int = 50) -> list[tuple[str, int]]:
    """Aggregate recurring normalized unknown titles for one-time admin mappings."""
    normalized = []
    for title in titles:
        clean = " ".join(_TOKEN_RE.findall((title or "").lower()))
        clean = re.sub(r"\b(senior|sr|junior|jr|lead|principal|staff|intern|internship)\b", "", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        if clean:
            normalized.append(clean)
    return Counter(normalized).most_common(limit)

