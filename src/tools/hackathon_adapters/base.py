"""Base adapter interface and HackathonCandidate data class."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class HackathonCandidate:
    """Rich internal candidate structure for a hackathon event.

    More detailed than event_info fields — used for filtering and auditing
    before writing to the database.
    """

    title: str
    source_name: str
    source_url: str
    canonical_url: str = ""
    organizer: str = ""
    registration_status: str = "unknown"  # open / upcoming / closed / ended / unknown
    registration_open_time: Optional[str] = None
    signup_deadline: Optional[str] = None
    event_start: Optional[str] = None
    event_end: Optional[str] = None
    timezone: str = "UTC"
    location: str = ""
    mode: Optional[str] = None  # online / offline / hybrid
    tags: List[str] = field(default_factory=list)
    summary: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)
    discovered_from: str = ""
    source_authority: str = "low"  # high / medium / low
    raw_date_text: str = ""
    extraction_method: str = ""  # html_parse / json / search_discovery / api
    platform_id: Optional[str] = None

    def to_event_dict(self) -> dict:
        """Convert to dict suitable for sync_events_to_db."""
        return {
            "title": self.title,
            "scope_type": "校外竞赛",
            "category": "黑客松",
            "summary": self.summary,
            "signup_deadline": self.signup_deadline,
            "event_time": self.event_start,
            "source_name": self.source_name,
            "source_url": self.source_url,
            "tags": self.tags,
            "organizer": self.organizer,
            "authority_level": self.source_authority,
            "status": "暂无本届信息",
            "is_ministry_approved": False,
            "original_text": self.summary,
            "contest_level": "",  # Don't guess
            "target_major": "",
            "target_grade": "",
            "policy_tags": "",
        }

    def merge_from(self, other: "HackathonCandidate") -> "HackathonCandidate":
        """Merge another candidate's data into this one (higher authority wins).

        Lower authority never overwrites higher authority data.
        Empty values never overwrite existing values.
        """
        authority_order = {"high": 3, "medium": 2, "low": 1, "": 0}

        def _should_update(my_val, their_val, my_auth, their_auth) -> bool:
            if not their_val:
                return False
            if not my_val:
                return True
            their_rank = authority_order.get(their_auth, 0)
            my_rank = authority_order.get(my_auth, 0)
            return their_rank >= my_rank

        if _should_update(self.signup_deadline, other.signup_deadline, self.source_authority, other.source_authority):
            self.signup_deadline = other.signup_deadline
        if _should_update(self.event_start, other.event_start, self.source_authority, other.source_authority):
            self.event_start = other.event_start
        if _should_update(self.event_end, other.event_end, self.source_authority, other.source_authority):
            self.event_end = other.event_end
        if _should_update(self.registration_status, other.registration_status, self.source_authority, other.source_authority):
            self.registration_status = other.registration_status
        if other.organizer and not self.organizer:
            self.organizer = other.organizer
        if other.location and not self.location:
            self.location = other.location
        if other.mode and not self.mode:
            self.mode = other.mode

        # Tags: union
        for t in other.tags:
            if t not in self.tags:
                self.tags.append(t)

        # Evidence: merge
        self.evidence.update(other.evidence)

        return self


class BaseAdapter(ABC):
    """Base interface for hackathon source adapters."""

    name: str = "base"

    @abstractmethod
    def discover(self, ctx, limit: int = 50) -> List[HackathonCandidate]:
        """Discover hackathon candidates from this source.

        Returns event-level candidates, NOT platform list pages.
        """
        ...

    @abstractmethod
    def parse_listing(self, html: str, url: str) -> List[dict]:
        """Parse a listing page into event-level raw dicts."""
        ...

    @abstractmethod
    def parse_detail(self, html: str, url: str) -> Optional[dict]:
        """Parse a single hackathon detail page into raw dict."""
        ...

    @abstractmethod
    def normalize(self, raw: dict) -> Optional[HackathonCandidate]:
        """Normalize a raw event dict into a HackathonCandidate."""
        ...
