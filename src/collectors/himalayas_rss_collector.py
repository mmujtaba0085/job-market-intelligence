"""
src/collectors/himalayas_rss_collector.py
──────────────────────────────────────────
Collector for Himalayas RSS feed (backup source, no auth required).

Endpoint: GET https://himalayas.app/jobs/feed
Format: RSS/XML

RSS fields used:
  - item/title
  - item/link
  - item/description
  - item/pubDate

Uses stdlib xml.etree.ElementTree for parsing.
All jobs from Himalayas RSS are remote positions.
"""

from __future__ import annotations

import hashlib
import logging
import xml.etree.ElementTree as ET
from datetime import datetime

import requests

from src.collectors.base_collector import BaseCollector
from src.storage.models import JobRaw

logger = logging.getLogger(__name__)

_RSS_URL = "https://himalayas.app/jobs/feed"
_TIMEOUT = 15


class HimalayasRSSCollector(BaseCollector):
    source_id = "himalayas_rss"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from Himalayas RSS feed."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        
        self._wait()
        
        try:
            logger.debug("[himalayas_rss] Fetching RSS feed")
            resp = requests.get(_RSS_URL, timeout=_TIMEOUT)
            
            if resp.status_code != 200:
                logger.warning("[himalayas_rss] HTTP %d", resp.status_code)
                return []
            
            # Parse RSS
            root = ET.fromstring(resp.content)
            
            # RSS items are under channel/item
            items = root.findall(".//item")
            logger.debug("[himalayas_rss] Found %d items in feed", len(items))
            
            for item in items:
                if len(results) >= max_jobs:
                    break
                
                # Extract basic fields
                title = self._get_text(item, "title")
                link = self._get_text(item, "link")
                description = self._get_text(item, "description")
                pub_date = self._get_text(item, "pubDate")
                
                # Filter by keywords if provided
                if keywords and not self._matches_keywords(title, description, keywords):
                    continue
                
                # URL fallback
                url = link
                if not url:
                    hash_input = f"{title}|{pub_date}"
                    url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                    url = f"himalayas_rss://{url_hash}"
                
                # Try to extract company from description or title
                company = self._extract_company(title, description)
                
                # Parse date
                posted_date = self._parse_rss_date(pub_date)
                
                results.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name="Himalayas RSS",
                        url=url,
                        fetched_at=self._now(),
                        raw_json={
                            "title": title,
                            "link": link,
                            "description": description,
                            "pubDate": pub_date,
                        },
                        parsed_fields={
                            "title": title,
                            "company": company,
                            "location": "Global",
                            "country": "Global",
                            "remote_type": "Remote",  # Himalayas = remote only
                            "posted_date": posted_date,
                            "description": description,
                        },
                    )
                )
            
            logger.info("[himalayas_rss] Collected %d raw jobs for market %s", len(results), market.get("market_id"))
            
        except requests.Timeout:
            logger.warning("[himalayas_rss] Timeout fetching RSS feed")
        except ET.ParseError as e:
            logger.error("[himalayas_rss] XML parse error: %s", e)
        except Exception as e:
            logger.error("[himalayas_rss] Error fetching feed: %s", e)
        
        return results[:max_jobs]

    def _get_text(self, element: ET.Element, tag: str) -> str:
        """Safely get text from XML element."""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""

    def _matches_keywords(self, title: str, description: str, keywords: list[str]) -> bool:
        """Check if job matches any keyword."""
        search_text = f"{title} {description}".lower()
        return any(kw.lower() in search_text for kw in keywords)

    def _extract_company(self, title: str, description: str) -> str:
        """Try to extract company name from title or description."""
        # Titles often follow pattern: "Job Title at Company Name"
        if " at " in title:
            parts = title.split(" at ")
            if len(parts) == 2:
                return parts[1].strip()
        
        # Could try regex on description, but keep it simple for now
        return ""

    def _parse_rss_date(self, date_str: str) -> str:
        """Parse RFC 2822 date to YYYY-MM-DD."""
        if not date_str:
            return ""
        
        try:
            # RFC 2822 format: "Wed, 02 Oct 2024 12:00:00 +0000"
            dt = datetime.strptime(date_str, "%a, %d %b %Y %H:%M:%S %z")
            return dt.strftime("%Y-%m-%d")
        except Exception:
            try:
                # Try without timezone
                dt = datetime.strptime(date_str[:25], "%a, %d %b %Y %H:%M:%S")
                return dt.strftime("%Y-%m-%d")
            except Exception:
                return ""
