"""HackClub Adapter v3.

hackathons.hackclub.com is a Next.js page — almost entirely JS-rendered.
Strategy:
1. Extract event names + dates from og:description meta tag
2. Extract external hackathon website URLs (the actual hackathon sites)
3. Match them as best we can, but primarily use the external URLs as candidates
4. The detail fetch will visit the actual hackathon website for real data
"""

import re
import logging
import time
from typing import List, Optional
from datetime import datetime, timezone

from .base import BaseAdapter, HackathonCandidate

logger = logging.getLogger(__name__)


class HackClubAdapter(BaseAdapter):
    """Adapter for hackathons.hackclub.com (high school hackathon directory)."""

    name = "hackclub"
    listing_url = "https://hackathons.hackclub.com/"

    # Known UI text to exclude from event names
    EXCLUDE_TITLES = {
        "Add Your Event", "Submit", "Login", "Sign up", "Dashboard",
        "About", "Contact", "Privacy", "Terms", "Events", "Hackathons",
        "High School Hackathons", "Upcoming High School Hackathons",
        "Online", "In-Person", "Virtual", "Hybrid",
        "Brilliant Move",  # This is a real event but we'll handle via og:desc
    }

    def discover(self, ctx, limit: int = 50) -> List[HackathonCandidate]:
        """Fetch HackClub directory, extract event names and external URLs."""
        html = self._fetch_listing_page(self.listing_url)
        if not html:
            return []

        # Parse og:description for event names + dates
        og_events = self._parse_og_description(html)

        # Get external hackathon URLs
        external_urls = self._extract_external_urls(html)

        # Build candidates: prefer og_events if available, else use external URLs
        candidates = []

        # First, create candidates from og:description events
        for ev in og_events:
            name = ev.get("name", "")
            if not name or name in self.EXCLUDE_TITLES:
                continue
            # Try to match with an external URL
            matched_url = self._match_url(name, external_urls)
            candidates.append(HackathonCandidate(
                title=name,
                source_name="HackClub",
                source_url=matched_url or self.listing_url,
                canonical_url=matched_url or "",
                organizer="",
                registration_status="unknown",
                event_start=ev.get("event_start"),
                event_end=ev.get("event_end"),
                signup_deadline=None,
                timezone="UTC",
                mode=ev.get("mode"),
                tags=["黑客松", "高中生"],
                summary=f"HackClub hackathon: {name}",
                evidence={"og_description": True, "date_text": ev.get("raw_date", "")},
                discovered_from="hackclub_listing",
                source_authority="high",
                raw_date_text=ev.get("raw_date", ""),
                extraction_method="og_description",
            ))

        # Then add remaining external URLs as lower-priority candidates
        used_urls = {c.source_url for c in candidates}
        for ext_url in external_urls:
            if ext_url in used_urls:
                continue
            if len(candidates) >= limit:
                break
            candidates.append(HackathonCandidate(
                title="",  # Will be filled by detail fetch
                source_name="HackClub",
                source_url=ext_url,
                canonical_url=ext_url,
                organizer="",
                registration_status="unknown",
                tags=["黑客松", "高中生"],
                summary="HackClub listed hackathon",
                evidence={"hackclub_listed": True},
                discovered_from="hackclub_listing",
                source_authority="medium",
                extraction_method="external_link",
            ))

        logger.info("HackClub: %d candidates (%d og + %d external)",
                    len(candidates), len(og_events), len(external_urls))
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
        session = requests.Session()
        for attempt in range(3):
            try:
                r = session.get(url, headers=headers, timeout=15, allow_redirects=True)
                if r.status_code == 200 and len(r.text) > 5000:
                    return r.text
                logger.warning("HackClub listing status=%s len=%d", r.status_code, len(r.text))
            except requests.RequestException as e:
                logger.warning("HackClub fetch attempt %d/3 failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
        return None

    def _parse_og_description(self, html: str) -> List[dict]:
        """Parse og:description meta tag for event names and dates.

        Format: 'Event Name (Date Range) … Event Name (Date Range) …'
        Dates can be: 'Jun 29–Aug 3', 'July 14–19', 'Jul 1–Aug 1'
        """
        m = re.search(
            r'<meta[^>]*property="og:description"[^>]*content="([^"]+)"',
            html, re.IGNORECASE,
        )
        if not m:
            return []

        desc = m.group(1)
        # Split on ' … ' or similar separators
        # Pattern: Event Name (Date) or Event Name (Month Day–Day)
        event_pattern = re.compile(
            r'([A-Z][A-Za-z0-9 &.\'#:\-–]{3,60}?)\s*\(([^)]+)\)',
        )
        events = []
        for em in event_pattern.finditer(desc):
            name = em.group(1).strip()
            date_str = em.group(2).strip()

            # Skip non-event text
            skip = {
                "High School Hackathons in 2026", "A curated list",
                "Maintained by the Hack Club staff",
            }
            if any(s in name for s in skip):
                continue

            # Parse the date
            start, end = self._parse_og_date(date_str)

            mode = None
            # Check if mode is mentioned nearby
            if "online" in desc.lower():
                pass  # Can't determine per-event mode from og:description

            events.append({
                "name": name,
                "raw_date": date_str,
                "event_start": start,
                "event_end": end,
                "mode": mode,
            })

        return events

    def _parse_og_date(self, date_str: str):
        """Parse date from og:description format like 'Jun 29–Aug 3' or 'July 14–19'."""
        now = datetime.now(timezone.utc)
        year = now.year

        month_map = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
            "january": 1, "february": 2, "march": 3, "april": 4,
            "june": 6, "july": 7, "august": 8, "september": 9,
            "october": 10, "november": 11, "december": 12,
        }

        # "Jun 29–Aug 3"
        m = re.match(
            r'(' + '|'.join(month_map.keys()) + r')\s+(\d{1,2})\s*[–\-]\s*(' +
            '|'.join(month_map.keys()) + r')?\s*(\d{1,2})',
            date_str, re.IGNORECASE,
        )
        if m:
            month1 = month_map[m.group(1).lower()]
            day1 = int(m.group(2))
            month2_str = m.group(3)
            day2 = int(m.group(4))
            month2 = month_map[month2_str.lower()] if month2_str else month1

            start = datetime(year, month1, day1, tzinfo=timezone.utc)
            end = datetime(year, month2, day2, 23, 59, 59, tzinfo=timezone.utc)
            return start.isoformat(), end.isoformat()

        # "July 14–19"
        m2 = re.match(
            r'(' + '|'.join(month_map.keys()) + r')\s+(\d{1,2})\s*[–\-]\s*(\d{1,2})',
            date_str, re.IGNORECASE,
        )
        if m2:
            month = month_map[m2.group(1).lower()]
            day1 = int(m2.group(2))
            day2 = int(m2.group(3))
            start = datetime(year, month, day1, tzinfo=timezone.utc)
            end = datetime(year, month, day2, 23, 59, 59, tzinfo=timezone.utc)
            return start.isoformat(), end.isoformat()

        return None, None

    def _extract_external_urls(self, html: str) -> List[str]:
        """Extract external hackathon website URLs from the page."""
        links = re.findall(
            r'href="(https?://(?!hackclub\.com|github\.com/hackclub|assets\.hackclub|hackathons\.hackclub\.com|dash\.hackathons)[^"]+)"',
            html, re.IGNORECASE,
        )
        # Deduplicate, preserve order
        seen = set()
        unique = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique.append(link)
        logger.info("HackClub: %d unique external URLs", len(unique))
        return unique

    def _match_url(self, name: str, urls: List[str]) -> Optional[str]:
        """Try to match an event name to its external URL."""
        name_lower = name.lower().replace(" ", "")
        for url in urls:
            domain = re.sub(r"https?://(www\.)?", "", url).split("/")[0].lower()
            domain_clean = re.sub(r"[^a-z0-9]", "", domain)
            name_clean = re.sub(r"[^a-z0-9]", "", name_lower)
            if name_clean and domain_clean and (
                name_clean in domain_clean or domain_clean in name_clean
            ):
                return url
        return None

    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Not used: HackClub listing is JS-rendered."""
        return []

    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """Parse a HackClub-listed external hackathon website."""
        return None

    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Already normalized in discover."""
        return None
