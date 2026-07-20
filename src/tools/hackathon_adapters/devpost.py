"""Devpost Adapter v2.

Devpost listing page (devpost.com/hackathons) returns HTTP 202 with minimal content
(JS-rendered). We cannot parse the listing directly. Instead we:

1. Use SearchClient with site:devpost.com queries to find individual hackathon pages
2. Filter results to actual hackathon pages (not submissions, software pages, updates)
3. Try to fetch individual hackathon pages (may also be JS-heavy)
"""

import re
import logging
from typing import List, Optional
from datetime import datetime, timezone

from .base import BaseAdapter, HackathonCandidate
from .general_search import GeneralSearchAdapter

logger = logging.getLogger(__name__)


class DevpostAdapter(BaseAdapter):
    """Adapter for devpost.com — uses search-based discovery."""

    name = "devpost"

    # Search queries targeting actual hackathon pages on Devpost
    SEARCH_QUERIES = [
        "site:devpost.com hackathon 2026",
        "site:devpost.com hackathon 2027",
    ]

    # URL patterns to EXCLUDE (submissions, projects, updates, etc.)
    EXCLUDE_PATTERNS = [
        r"/software/",           # Individual projects
        r"/updates/",            # Project updates
        r"/submit-to/",          # Submission pages
        r"/submissions",         # Submissions listing
        r"/manage/",             # Management pages
        r"/discussions/",        # Discussion boards
        r"/participants",        # Participant listing
        r"/judging",             # Judging pages
        r"/rules",               # Rules pages (only hackathon rules)
        r"/prizes",              # Prize pages
        r"/resources",           # Resource pages
        r"/gallery",             # Gallery pages
    ]

    # URL patterns to KEEP (actual hackathon pages)
    INCLUDE_PATTERNS = [
        r"\.devpost\.com/$",           # hackathon-name.devpost.com/
        r"\.devpost\.com/?$",          # hackathon-name.devpost.com
        r"\.devpost\.com/details",     # hackathon details
        r"\.devpost\.com/?\?[^/]*$",   # hackathon-name.devpost.com?...
    ]

    def __init__(self):
        self._search_adapter = GeneralSearchAdapter()

    def discover(self, ctx, limit: int = 30) -> List[HackathonCandidate]:
        """Use search to find Devpost hackathon pages."""
        all_urls = set()
        candidates = []

        for query in self.SEARCH_QUERIES:
            if len(all_urls) >= limit * 2:
                break

            try:
                results = self._search_adapter._do_search(ctx, query, num=10)
                for r in results:
                    url = r.get("url", "") or r.get("link", "")
                    if not url:
                        continue
                    # Must be devpost.com
                    if "devpost.com" not in url:
                        continue
                    # Filter out non-hackathon pages
                    if not self._is_hackathon_page(url):
                        continue
                    all_urls.add(url)
            except Exception as e:
                logger.warning("Devpost search error for '%s': %s", query, e)

        # Create candidates from URLs
        for url in list(all_urls)[:limit]:
            # Extract hackathon name from URL
            name = self._extract_name_from_url(url)
            candidates.append(HackathonCandidate(
                title=name or "Devpost Hackathon",
                source_name="Devpost",
                source_url=url,
                canonical_url=url,
                organizer="",
                registration_status="unknown",
                discovered_from="devpost_search",
                source_authority="high",
                tags=["黑客松"],
                summary=f"Devpost hackathon: {name or url}",
                evidence={"url": url},
                extraction_method="search_discovery",
            ))

        logger.info("Devpost: %d candidates from %d unique URLs", len(candidates), len(all_urls))
        return candidates

    def _is_hackathon_page(self, url: str) -> bool:
        """Check if a Devpost URL is a hackathon page (not a project/submission)."""
        for pat in self.EXCLUDE_PATTERNS:
            if re.search(pat, url):
                return False
        return True

    def _extract_name_from_url(self, url: str) -> Optional[str]:
        """Extract hackathon name from Devpost URL like hackathon-name.devpost.com."""
        # Match: {name}.devpost.com
        m = re.match(r"https?://([^.]+)\.devpost\.com", url)
        if m:
            name = m.group(1).replace("-", " ").title()
            if name.lower() not in ("www", "devpost", "info"):
                return name
        return None

    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Devpost listing parse — not applicable for search-based approach."""
        return []

    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """Devpost detail page parse — limited due to JS rendering."""
        # Most Devpost pages are JS-rendered. Try to extract what we can.
        if len(html) < 3000:
            return None

        title_match = re.search(r"<title>(.*?)</title>", html, re.IGNORECASE)
        title = title_match.group(1) if title_match else None

        # Try to find structured data
        ld_json = re.search(
            r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if ld_json:
            try:
                import json
                data = json.loads(ld_json.group(1))
                name = data.get("name", "") if isinstance(data, dict) else ""
            except Exception:
                pass

        return {
            "title": title or "",
            "source_url": url,
            "source_name": "Devpost",
        }

    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Devpost candidates are already HackathonCandidate from discover."""
        if isinstance(raw, HackathonCandidate):
            return raw
        return None
