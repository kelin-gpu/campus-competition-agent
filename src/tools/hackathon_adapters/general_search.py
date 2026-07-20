"""General Search Adapter v2.

Uses SearchClient for multi-language web search with current-year awareness.
Results are URL-level candidates; detail pages are fetched separately.
Search snippets are NOT used as date evidence.
"""

import re
import json
import logging
import os
from typing import List, Optional
from datetime import datetime, timezone

from .base import BaseAdapter, HackathonCandidate

logger = logging.getLogger(__name__)

# Load search queries from config
_QUERIES_CACHE = None


def _load_search_queries():
    """Load search queries from hackathon_sources.json."""
    global _QUERIES_CACHE
    if _QUERIES_CACHE is not None:
        return _QUERIES_CACHE

    now = datetime.now(timezone.utc)
    current_year = now.year
    next_year = current_year + 1

    # Default queries with year substitution
    template_queries = [
        # Chinese
        "黑客松 报名 {year}",
        "大学生 黑客松 {year}",
        "AI 黑客松 报名 {year}",
        "高校 黑客松 比赛 {year}",
        # English
        "hackathon registration open {year}",
        "student hackathon apply {year}",
        "AI hackathon application deadline {year}",
        "upcoming hackathon {year}",
        # Platform-specific
        "site:devpost.com hackathon {year}",
        "site:devfolio.co hackathon",
        "site:eventbrite.com hackathon {year}",
        "site:lu.ma hackathon {year}",
        "university hackathon register {year}",
    ]

    queries = []
    for q in template_queries:
        # Substitute both current and next year
        queries.append(q.format(year=current_year))
        queries.append(q.format(year=next_year))

    # Add queries without year
    for q in ["site:devfolio.co hackathon", "hackathon registration open now"]:
        if q not in queries:
            queries.append(q)

    _QUERIES_CACHE = queries
    return queries


class GeneralSearchAdapter(BaseAdapter):
    """General web search adapter for hackathon discovery."""

    name = "general_search"

    def __init__(self):
        self._queries = None
        self._ctx = None

    @property
    def queries(self):
        if self._queries is None:
            self._queries = _load_search_queries()
        return self._queries

    def discover(self, ctx, limit: int = 60) -> List[HackathonCandidate]:
        """Run search queries, collect unique candidate URLs."""
        self._ctx = ctx
        all_urls = {}  # url -> {title, snippet}

        # Per-query limit
        per_query_limit = max(3, limit // max(len(self.queries), 1))

        for query in self.queries:
            if len(all_urls) >= limit * 2:
                break

            try:
                results = self._do_search(ctx, query, num=per_query_limit)
                for r in results:
                    url = r.get("url", "") or r.get("link", "")
                    if not url:
                        continue
                    if url in all_urls:
                        continue
                    # Basic URL validation
                    if not url.startswith(("http://", "https://")):
                        continue
                    all_urls[url] = {
                        "title": r.get("title", ""),
                        "snippet": r.get("snippet", "") or r.get("description", ""),
                    }
            except Exception as e:
                logger.warning("Search error for '%s': %s", query, e)

        logger.info("GeneralSearch discovered %d unique candidate URLs", len(all_urls))

        # Convert to candidates (URLs only, no date claims from snippets)
        candidates = []
        for url, meta in list(all_urls.items())[:limit]:
            candidates.append(HackathonCandidate(
                title=meta.get("title", "")[:100],
                source_name="WebSearch",
                source_url=url,
                canonical_url=url,
                summary=meta.get("snippet", "")[:200],
                discovered_from="general_search",
                source_authority="low",  # Search results are low authority
                tags=[],
                evidence={"search_snippet": meta.get("snippet", "")[:200]},
                extraction_method="search_discovery",
            ))

        return candidates

    def _do_search(self, ctx, query: str, num: int = 6) -> List[dict]:
        """Execute a single search query using SearchClient."""
        try:
            from coze_coding_dev_sdk import SearchClient
            client = SearchClient(ctx=ctx)
            response = client.web_search(query=query, count=num)

            results = []
            if hasattr(response, "web_items") and response.web_items:
                for item in response.web_items:
                    results.append({
                        "title": item.title or "",
                        "url": item.url or "",
                        "snippet": item.snippet or "",
                        "source": item.site_name or "",
                    })
            return results
        except ImportError:
            logger.warning("SearchClient not available")
            return []
        except Exception as e:
            logger.warning("SearchClient error for '%s': %s", query, e)
            return []

    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Not used for search-based adapter."""
        return []

    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """Not used for search-based adapter."""
        return None

    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Candidates already normalized in discover."""
        return None
