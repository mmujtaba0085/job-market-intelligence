"""
src/collectors/hireweb3_collector.py
─────────────────────────────────────
Collector for HireWeb3 RSS feed (no auth required).

Endpoint: GET https://hireweb3.io/job/rss
Format: RSS/XML with custom hireweb3Jobs namespace

RSS fields used:
  - item/title
  - item/link
  - item/description
  - item/pubDate
  - hireweb3Jobs:companyName (if present)
  - hireweb3Jobs:location (if present)
  - hireweb3Jobs:locationType (if present)
  - hireweb3Jobs:minSalary, maxSalary (if present)

Uses stdlib xml.etree.ElementTree for parsing.
Infers remote_type from locationType or description.
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

_RSS_URL = "https://hireweb3.io/job/rss"
_TIMEOUT = 15


class HireWeb3Collector(BaseCollector):
    source_id = "hireweb3"

    def _fetch_raw(self, market: dict) -> list[JobRaw]:
        """Fetch jobs from HireWeb3 RSS feed."""
        results: list[JobRaw] = []
        max_jobs = market.get("max_jobs_per_source", 200)
        keywords = market.get("keywords", [])
        
        self._wait()
        
        try:
            logger.debug("[hireweb3] Fetching RSS feed")
            resp = requests.get(_RSS_URL, timeout=_TIMEOUT)
            
            if resp.status_code != 200:
                logger.warning("[hireweb3] HTTP %d", resp.status_code)
                return []
            
            # Parse RSS
            root = ET.fromstring(resp.content)
            
            # Register namespaces (hireweb3Jobs custom fields)
            # Namespace might be in the XML, try to detect it
            namespaces = dict([node for _, node in ET.iterparse(
                requests.get(_RSS_URL, timeout=_TIMEOUT).content,
                events=['start-ns']
            )]) if False else {}  # Skip for now, use direct tag names
            
            # RSS items under channel/item
            items = root.findall(".//item")
            logger.debug("[hireweb3] Found %d items in feed", len(items))
            
            for item in items:
                if len(results) >= max_jobs:
                    break
                
                # Extract standard RSS fields
                title = self._get_text(item, "title")
                link = self._get_text(item, "link")
                description = self._get_text(item, "description")
                pub_date = self._get_text(item, "pubDate")
                
                # Try to extract custom hireweb3Jobs fields (might have namespace prefix)
                company = self._get_custom_field(item, "companyName")
                location = self._get_custom_field(item, "location")
                location_type = self._get_custom_field(item, "locationType")
                min_salary = self._get_custom_field(item, "minSalary")
                max_salary = self._get_custom_field(item, "maxSalary")
                
                # Filter by keywords if provided
                if keywords and not self._matches_keywords(title, description, keywords):
                    continue
                
                # URL fallback
                url = link
                if not url:
                    hash_input = f"{title}|{company}|{pub_date}"
                    url_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]
                    url = f"hireweb3://{url_hash}"
                
                # Infer remote type
                remote_type = self._infer_remote_type(location_type, description)
                
                # Extract country from location
                country = self._extract_country(location)
                
                # Parse date
                posted_date = self._parse_rss_date(pub_date)
                
                results.append(
                    JobRaw(
                        source_id=self.source_id,
                        source_name="HireWeb3",
                        url=url,
                        fetched_at=self._now(),
                        raw_json={
                            "title": title,
                            "link": link,
                            "description": description,
                            "pubDate": pub_date,
                            "companyName": company,
                            "location": location,
                            "locationType": location_type,
                            "minSalary": min_salary,
                            "maxSalary": max_salary,
                        },
                        parsed_fields={
                            "title": title,
                            "company": company or self._extract_company_from_title(title),
                            "location": location or "",
                            "country": country,
                            "remote_type": remote_type,
                            "posted_date": posted_date,
                            "description": description,
                        },
                    )
                )
            
            logger.info("[hireweb3] Collected %d raw jobs for market %s", len(results), market.get("market_id"))
            
        except requests.Timeout:
            logger.warning("[hireweb3] Timeout fetching RSS feed")
        except ET.ParseError as e:
            logger.error("[hireweb3] XML parse error: %s", e)
        except Exception as e:
            logger.error("[hireweb3] Error fetching feed: %s", e)
        
        return results[:max_jobs]

    def _get_text(self, element: ET.Element, tag: str) -> str:
        """Safely get text from XML element."""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""

    def _get_custom_field(self, element: ET.Element, field_name: str) -> str:
        """Try to extract custom hireweb3Jobs field (with or without namespace)."""
        # Try without namespace first
        child = element.find(field_name)
        if child is not None and child.text:
            return child.text.strip()
        
        # Try with common namespace prefixes
        for prefix in ["hireweb3Jobs:", "{http://hireweb3.io/rss/}"]:
            child = element.find(f"{prefix}{field_name}")
            if child is not None and child.text:
                return child.text.strip()
        
        return ""

    def _matches_keywords(self, title: str, description: str, keywords: list[str]) -> bool:
        """Check if job matches any keyword."""
        search_text = f"{title} {description}".lower()
        return any(kw.lower() in search_text for kw in keywords)

    def _infer_remote_type(self, location_type: str, description: str) -> str:
        """Infer remote type from locationType field or description."""
        # Check locationType field first
        if location_type:
            loc_type_lower = location_type.lower()
            if "remote" in loc_type_lower:
                return "Remote"
            elif "hybrid" in loc_type_lower:
                return "Hybrid"
            elif "onsite" in loc_type_lower or "on-site" in loc_type_lower:
                return "On-site"
        
        # Fall back to description analysis
        desc_lower = description.lower()
        if "remote" in desc_lower or "work from home" in desc_lower:
            if "hybrid" in desc_lower:
                return "Hybrid"
            return "Remote"
        
        return "On-site"  # Default

    def _extract_country(self, location: str) -> str:
        """Extract country from location string."""
        if not location:
            return ""
        
        loc_lower = location.lower()
        
        # Common country detection
        if "us" in loc_lower or "usa" in loc_lower or "united states" in loc_lower:
            return "United States"
        elif "uk" in loc_lower or "united kingdom" in loc_lower:
            return "United Kingdom"
        elif "canada" in loc_lower:
            return "Canada"
        elif "germany" in loc_lower:
            return "Germany"
        elif "france" in loc_lower:
            return "France"
        elif "global" in loc_lower or "worldwide" in loc_lower or "remote" in loc_lower:
            return "Global"
        
        return ""

    def _extract_company_from_title(self, title: str) -> str:
        """Try to extract company name from title."""
        # Titles often follow pattern: "Job Title at Company Name"
        if " at " in title:
            parts = title.split(" at ")
            if len(parts) == 2:
                return parts[1].strip()
        
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
