"""Devfolio Adapter v2 — parse devfolio.co/hackathons listing page.

Extracts hackathon entries from the HTML (62+ hackathon mentions).
Also tries to extract from __NEXT_DATA__ JSON if available.
"""

import re
import json
import logging
from typing import List, Optional
from datetime import datetime, timezone

from .base import BaseAdapter, HackathonCandidate

logger = logging.getLogger(__name__)


class DevfolioAdapter(BaseAdapter):
    """Adapter for devfolio.co hackathon platform."""

    name = "devfolio"
    listing_url = "https://devfolio.co/hackathons"

    def discover(self, ctx, limit: int = 50) -> List[HackathonCandidate]:
        """Fetch Devfolio listing and extract hackathon entries."""
        html = self._fetch_listing_page(self.listing_url)
        if not html:
            return []

        events = self.parse_listing(html, self.listing_url)
        logger.info("Devfolio: %d events found", len(events))

        candidates = []
        for ev in events:
            c = self.normalize(ev)
            if c is not None:
                candidates.append(c)

        return candidates[:limit]

    def _fetch_listing_page(self, url: str) -> Optional[str]:
        import requests
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        try:
            r = requests.get(url, headers=headers, timeout=15, allow_redirects=True)
            if r.status_code == 200 and len(r.text) > 5000:
                return r.text
            logger.warning("Devfolio listing page status=%s len=%d", r.status_code, len(r.text))
        except Exception as e:
            logger.warning("Devfolio fetch error: %s", e)
        return None

    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Parse Devfolio listing page.

        Devfolio uses React/Next.js. The HTML contains hackathon names in text content.
        We try:
        1. __NEXT_DATA__ JSON for structured data
        2. Hackathon names from visible text near "Open"/"Upcoming" status
        3. Link extraction for /hackathons/{slug} patterns
        """
        events = []

        # Try __NEXT_DATA__ first
        m = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*type="application/json"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if m:
            try:
                data = json.loads(m.group(1))
                page_props = data.get("props", {}).get("pageProps", {})
                dehydrated = page_props.get("dehydratedState", {})

                # React Query cache has queries array
                if isinstance(dehydrated, dict):
                    queries = dehydrated.get("queries", [])
                    for q in queries:
                        state_data = q.get("state", {}).get("data", {})
                        if isinstance(state_data, dict):
                            # Look for hackathon data
                            for key in ["data", "hackathons", "results", "items"]:
                                items = state_data.get(key)
                                if isinstance(items, list):
                                    for item in items:
                                        if isinstance(item, dict):
                                            ev = self._extract_from_json(item)
                                            if ev:
                                                events.append(ev)
            except Exception as e:
                logger.debug("Devfolio NEXT_DATA parse error: %s", e)

        # Fallback: extract from visible text
        if not events:
            # Strip scripts/styles
            text_html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
            text_html = re.sub(r"<style[^>]*>.*?</style>", "", text_html, flags=re.DOTALL)

            # Find hackathon name + status patterns
            # Devfolio shows hackathon title followed by Open/Upcoming/Closed badge
            status_patterns = re.finditer(
                r'(?:Open|Upcoming|Live|Closed|Registration)',
                text_html, re.IGNORECASE,
            )
            seen_titles = set()

            for sp in status_patterns:
                # Look backwards for a hackathon title
                before = text_html[max(0, sp.start() - 500):sp.start()]
                # Extract the last heading-like text before the status
                titles = re.findall(r'<h[1-6][^>]*>(.*?)</h[1-6]>', before)
                if not titles:
                    titles = re.findall(r'class="[^"]*title[^"]*"[^>]*>(.*?)<', before, re.IGNORECASE)
                if not titles:
                    # Try plain text extraction
                    text_parts = re.split(r'<[^>]+>', before)
                    text_parts = [t.strip() for t in text_parts if 5 <= len(t.strip()) <= 120]
                    if text_parts:
                        title = text_parts[-1]
                        if title not in seen_titles and "hackathon" in title.lower():
                            seen_titles.add(title)
                            events.append({
                                "title": title,
                                "source_url": url,
                                "source_name": "Devfolio",
                                "status": sp.group(0),
                            })
                elif titles:
                    title = re.sub(r'<[^>]+>', '', titles[-1]).strip()
                    if title not in seen_titles and len(title) >= 5:
                        seen_titles.add(title)
                        events.append({
                            "title": title,
                            "source_url": url,
                            "source_name": "Devfolio",
                            "status": sp.group(0),
                        })

        return events

    def _extract_from_json(self, item: dict) -> Optional[dict]:
        """Extract hackathon info from JSON item."""
        name = item.get("name") or item.get("title") or item.get("hackathon_name", "")
        if not name or not isinstance(name, str):
            return None
        if len(name) < 3:
            return None

        status = item.get("status") or item.get("registration_status", "")
        url_slug = item.get("slug") or item.get("id", "")
        detail_url = f"https://devfolio.co/hackathons/{url_slug}" if url_slug else ""

        start_date = item.get("starts_at") or item.get("start_date") or item.get("event_start", "")
        end_date = item.get("ends_at") or item.get("end_date") or item.get("event_end", "")
        deadline = item.get("registration_end") or item.get("application_deadline", "")

        return {
            "title": name,
            "source_url": detail_url or self.listing_url,
            "source_name": "Devfolio",
            "status": str(status) if status else "unknown",
            "event_start": str(start_date) if start_date else None,
            "event_end": str(end_date) if end_date else None,
            "signup_deadline": str(deadline) if deadline else None,
            "mode": str(item.get("mode", "")).lower() if item.get("mode") else None,
            "location": item.get("location") or item.get("city", ""),
            "organizer": item.get("organizer") or item.get("company_name", ""),
        }

    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Normalize Devfolio event to HackathonCandidate."""
        title = raw.get("title", "").strip()
        if not title:
            return None

        status = raw.get("status", "unknown")
        mode = raw.get("mode")

        tags = ["黑客松"]
        if mode:
            tags.append(mode)

        event_start = raw.get("event_start")
        event_end = raw.get("event_end")
        deadline = raw.get("signup_deadline")

        return HackathonCandidate(
            title=title,
            source_name="Devfolio",
            source_url=raw.get("source_url", self.listing_url),
            canonical_url=raw.get("source_url", ""),
            organizer=raw.get("organizer", "Devfolio"),
            registration_status=status,
            event_start=event_start,
            event_end=event_end,
            signup_deadline=deadline,
            timezone="UTC",
            mode=mode,
            location=raw.get("location", ""),
            tags=tags,
            summary=f"Devfolio hackathon: {title}. Status: {status}",
            evidence={"status": status, "source": "devfolio_listing"},
            discovered_from="devfolio_listing",
            source_authority="high",
            raw_date_text=f"start={event_start}, end={event_end}, deadline={deadline}",
            extraction_method="json" if raw.get("source_url", "").startswith("https://devfolio.co/hackathons/") else "html_parse",
        )

    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """Devfolio detail page parsing - not implemented."""
        return None
