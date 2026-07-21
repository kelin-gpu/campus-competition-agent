"""MLH Adapter v2 — parse mlh.io/seasons/{year}/events listing page.

Uses requests to fetch the HTML, then regex to extract event cards.
No JS rendering required — MLH serves full HTML.
"""

import re
import json
import logging
import time
import html as html_lib
from typing import List, Optional
from datetime import datetime, timezone

from .base import BaseAdapter, HackathonCandidate

logger = logging.getLogger(__name__)


class MLHAdapter(BaseAdapter):
    """Adapter for mlh.io (Major League Hacking)."""

    name = "mlh"
    listing_url_template = "https://mlh.io/seasons/{year}/events"

    # Common month abbreviations
    MONTH_MAP = {
        "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
        "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
    }

    def discover(self, ctx, limit: int = 50) -> List[HackathonCandidate]:
        """Fetch MLH season listing pages, extract event cards."""
        candidates = []
        now = datetime.now(timezone.utc)
        current_year = now.year

        # Check current season and next season
        for year in [current_year, current_year + 1]:
            url = self.listing_url_template.format(year=year)
            html = self._fetch_listing_page(url)
            if not html:
                continue

            events = self.parse_listing(html, url)
            for event in events:
                event.setdefault("season_year", year)
            logger.info("MLH season %s: %d events found", year, len(events))

            for ev in events:
                c = self.normalize(ev)
                if c is not None and self._is_current_or_future(c, now):
                    candidates.append(c)

            if len(candidates) >= limit:
                break

        return candidates[:limit]

    @staticmethod
    def _is_current_or_future(candidate: HackathonCandidate, now: datetime) -> bool:
        """Drop completed events before they consume the discovery limit."""
        boundary = candidate.event_end or candidate.event_start
        if not boundary:
            return True
        try:
            event_time = datetime.fromisoformat(boundary.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        return event_time >= now

    def _fetch_listing_page(self, url: str) -> Optional[str]:
        """Fetch MLH listing page HTML."""
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
                logger.warning("MLH listing page returned status=%s len=%d", r.status_code, len(r.text))
            except requests.RequestException as e:
                logger.warning("MLH listing fetch attempt %d/3 failed: %s", attempt + 1, e)
            if attempt < 2:
                time.sleep(0.5 * (2 ** attempt))
        return None

    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Parse MLH listing HTML into event-level candidates with raw data."""
        structured_events = self._parse_schema_events(html, url)
        if structured_events:
            return structured_events

        events = []

        # Extract the events section (after "Upcoming Events")
        idx = html.find("Upcoming Events")
        if idx < 0:
            idx = html.find("upcoming events")
        if idx < 0:
            # Try to find events anywhere
            idx = 0
        chunk = html[idx:idx + 50000] if idx >= 0 else html

        # Strip SVG and script tags
        chunk = re.sub(r"<svg[^>]*>.*?</svg>", "", chunk, flags=re.DOTALL)
        chunk = re.sub(r"<script[^>]*>.*?</script>", "", chunk, flags=re.DOTALL)
        chunk = re.sub(r"<path[^>]*/>", "", chunk)
        chunk = re.sub(r"<circle[^>]*/>", "", chunk)

        # MLH event blocks: event name is in an h3/h4, followed by date, location, mode
        # Pattern: event name (in heading), then date line (MON DD - DD), then location
        lines = re.split(r"<[^>]+>", chunk)
        lines = [l.strip() for l in lines if l.strip() and len(l.strip()) > 1]

        # Skip noise
        skip_words = {
            "Menu", "MLH", "Major League Hacking", "Upcoming Events", "Apply",
            "Login", "Sign up", "Register", "About", "Contact", "Privacy",
            "Terms", "Cookie", "Season", "Events", "Sponsors", "Community",
            "Fellowship", "Top 50", "Hackathon", "Season", "©", "All rights",
        }

        # Scan for event blocks
        event_blocks = []
        current_block = None

        for line in lines:
            # Year markers - start new section
            if re.match(r"^\d{4}$", line):
                continue

            # Skip noise
            if line in skip_words or len(line) < 3:
                continue

            # Date pattern: "JUL 17 - 19" or "AUG 22 - 24"
            date_match = re.match(
                r"^(" + "|".join(self.MONTH_MAP.keys()) + r")\s+\d{1,2}\s*[-–]\s*\d{1,2}$",
                line, re.IGNORECASE,
            )
            # Also: "JUL 17 - 19" with year
            date_match2 = re.match(
                r"^(" + "|".join(self.MONTH_MAP.keys()) + r")\s+\d{1,2}\s*[-–]\s*\d{1,2},\s*\d{4}$",
                line, re.IGNORECASE,
            )

            if date_match or date_match2:
                if current_block is not None:
                    current_block["date_text"] = line
                continue

            # Location: comma-separated city/state/country
            if re.match(r"^[A-Z][a-z]+.*,\s*[A-Z]{2}", line) or re.match(
                r"^[A-Z][a-z]+.*,\s*[A-Z][a-z]+", line
            ):
                if current_block is not None:
                    if not current_block.get("location"):
                        current_block["location"] = line
                continue

            # Mode: Digital / In-Person / Hybrid
            if line in ("Digital", "In-Person", "Hybrid", "Online", "Virtual"):
                if current_block is not None:
                    current_block["mode"] = line.lower()
                continue

            # If we see a short-ish line (potential event name)
            # Event names are usually 3-40 chars, not all caps, not a single word
            if 3 <= len(line) <= 80 and not line.isupper() and " " in line:
                # Save previous block
                if current_block is not None and current_block.get("title"):
                    event_blocks.append(current_block)

                current_block = {
                    "title": line,
                    "date_text": None,
                    "location": None,
                    "mode": None,
                    "source_url": url,
                    "source_name": "MLH",
                }

        # Don't forget last block
        if current_block is not None and current_block.get("title"):
            event_blocks.append(current_block)

        return event_blocks

    def _parse_schema_events(self, page_html: str, listing_url: str) -> List[dict]:
        """Parse MLH's server-rendered schema.org Event cards.

        Current MLH pages expose the actual event URL and ISO start/end dates
        in each card, so this is both more complete and less fragile than
        scanning flattened page text.
        """
        anchor_pattern = re.compile(
            r'<a\b(?=[^>]*itemType=["\']https://schema\.org/Event["\'])'
            r'(?P<attrs>[^>]*)>(?P<body>.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )

        def attr(fragment: str, name: str) -> str:
            match = re.search(
                rf'\b{re.escape(name)}=["\']([^"\']*)["\']',
                fragment,
                re.IGNORECASE,
            )
            return html_lib.unescape(match.group(1)).strip() if match else ""

        events: List[dict] = []
        for match in anchor_pattern.finditer(page_html):
            attrs = match.group("attrs")
            body = match.group("body")
            href = attr(attrs, "href")
            metadata = {}
            for meta_tag in re.findall(r"<meta\b[^>]*>", body, re.IGNORECASE):
                prop = attr(meta_tag, "itemProp")
                content = attr(meta_tag, "content")
                if prop and content:
                    metadata[prop] = content

            title_match = re.search(
                r"<h[1-6][^>]*>(.*?)</h[1-6]>", body, re.IGNORECASE | re.DOTALL
            )
            title = ""
            if title_match:
                title = re.sub(r"<[^>]+>", "", title_match.group(1))
                title = html_lib.unescape(title).strip()
            if not title:
                continue

            location_match = re.search(
                r'<span[^>]*itemProp=["\']name["\'][^>]*>(.*?)</span>',
                body,
                re.IGNORECASE | re.DOTALL,
            )
            location = ""
            if location_match:
                location = re.sub(r"<[^>]+>", "", location_match.group(1))
                location = html_lib.unescape(location).strip()

            attendance = metadata.get("eventAttendanceMode", "").lower()
            if "online" in attendance:
                mode = "online"
            elif "mixed" in attendance or "hybrid" in attendance:
                mode = "hybrid"
            elif "offline" in attendance:
                mode = "offline"
            else:
                mode = None

            canonical_url = metadata.get("url") or href or listing_url
            events.append({
                "title": title,
                "source_url": canonical_url,
                "source_name": "MLH",
                "event_start": metadata.get("startDate"),
                "event_end": metadata.get("endDate"),
                "location": location,
                "mode": mode,
                "platform_id": canonical_url,
            })

        return events

    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Normalize MLH event block to HackathonCandidate."""
        title = raw.get("title", "").strip()
        if not title:
            return None

        # Skip non-hackathon headers
        skip_titles = {
            "Upcoming Events", "Past Events", "Events", "Sponsors",
            "Community", "Fellowship", "Top 50", "Season",
        }
        if title in skip_titles:
            return None

        mode = raw.get("mode", "unknown")
        location = raw.get("location", "")
        date_text = raw.get("date_text", "")

        # Parse date
        event_start = raw.get("event_start")
        event_end = raw.get("event_end")
        if date_text:
            parsed_start, parsed_end = self._parse_mlh_date(
                date_text, year=raw.get("season_year")
            )
            event_start = event_start or parsed_start
            event_end = event_end or parsed_end

        tags = ["黑客松"]
        if mode and mode != "unknown":
            tags.append(mode)

        return HackathonCandidate(
            title=title,
            source_name="MLH",
            source_url=raw.get("source_url", ""),
            canonical_url="",  # Will be enriched by detail fetch
            organizer="MLH",
            registration_status="unknown",
            event_start=event_start,
            event_end=event_end,
            signup_deadline=None,
            timezone="UTC",
            location=location,
            mode=mode if mode != "unknown" else None,
            tags=tags,
            summary=f"MLH hackathon: {title}. {date_text}. {location}",
            evidence={"date_text": date_text, "location": location, "mode": mode},
            discovered_from="mlh_listing",
            source_authority="high",
            raw_date_text=date_text,
            extraction_method="html_parse",
            platform_id=raw.get("platform_id"),
        )

    def _parse_mlh_date(self, date_text: str, year: Optional[int] = None):
        """Parse MLH date format like 'JUL 17 - 19' into (start_dt, end_dt).

        Uses current year since MLH season page is for a specific year.
        """
        now = datetime.now(timezone.utc)
        year = int(year or now.year)

        # JUL 17 - 19
        m = re.match(
            r"(" + "|".join(self.MONTH_MAP.keys()) + r")\s+(\d{1,2})\s*[-–]\s*(\d{1,2})$",
            date_text, re.IGNORECASE,
        )
        if m:
            month = self.MONTH_MAP[m.group(1).lower()]
            day_start = int(m.group(2))
            day_end = int(m.group(3))
            start = datetime(year, month, day_start, tzinfo=timezone.utc)
            end = datetime(year, month, day_end, 23, 59, 59, tzinfo=timezone.utc)
            return start.isoformat(), end.isoformat()

        # JUL 17 - 19, 2027 (with explicit year)
        m2 = re.match(
            r"(" + "|".join(self.MONTH_MAP.keys()) + r")\s+(\d{1,2})\s*[-–]\s*(\d{1,2}),\s*(\d{4})$",
            date_text, re.IGNORECASE,
        )
        if m2:
            month = self.MONTH_MAP[m2.group(1).lower()]
            year = int(m2.group(4))
            day_start = int(m2.group(2))
            day_end = int(m2.group(3))
            start = datetime(year, month, day_start, tzinfo=timezone.utc)
            end = datetime(year, month, day_end, 23, 59, 59, tzinfo=timezone.utc)
            return start.isoformat(), end.isoformat()

        return None, None

    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """MLH detail pages - currently minimal support."""
        return None
