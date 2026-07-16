"""Event persistence contract used by tool-layer database writes.

The platform-owned SQLAlchemy model is intentionally not imported here.  This
module mirrors its public ``event_info`` columns so model/LLM metadata can never
leak into PostgREST payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping


EVENT_DB_FIELDS = frozenset(
    {
        "event_id",
        "title",
        "scope_type",
        "category",
        "summary",
        "signup_deadline",
        "event_time",
        "target_major",
        "target_grade",
        "contest_level",
        "tags",
        "policy_tags",
        "source_name",
        "source_url",
        "authority_level",
        "status",
        "organizer",
        "update_time",
        "original_text",
        "is_ministry_approved",
    }
)

IMMUTABLE_FIELDS = frozenset({"event_id"})
AUTHORITY_RANK = {"低": 1, "中": 2, "高": 3}


def is_meaningful(value: Any) -> bool:
    """Return whether a value is safe to use as an enrichment update."""
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "null", "none"}
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def event_db_payload(event: Mapping[str, Any], *, include_none: bool = False) -> dict[str, Any]:
    """Filter an event mapping to columns accepted by ``event_info``."""
    return {
        key: value
        for key, value in event.items()
        if key in EVENT_DB_FIELDS and (include_none or value is not None)
    }


def parse_event_datetime(value: Any) -> datetime | None:
    """Parse an event timestamp and reject invalid or timezone-naive values."""
    if value is None or value == "":
        return None
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value).strip().replace("Z", "+00:00")
        )
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def normalize_event_times(event: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize event timestamps and clear impossible timeline values."""
    normalized = dict(event)
    deadline = parse_event_datetime(event.get("signup_deadline"))
    event_time = parse_event_datetime(event.get("event_time"))

    if "signup_deadline" in event:
        normalized["signup_deadline"] = deadline.isoformat() if deadline else None
    if "event_time" in event:
        normalized["event_time"] = event_time.isoformat() if event_time else None
    if deadline and event_time and event_time < deadline:
        normalized["event_time"] = None
    return normalized


def _source_priority(event: Mapping[str, Any]) -> tuple[int, int]:
    return (
        1 if event.get("is_ministry_approved") is True else 0,
        AUTHORITY_RANK.get(str(event.get("authority_level", "")), 0),
    )


def merge_event_data(existing: Mapping[str, Any], incoming: Mapping[str, Any]) -> dict[str, Any]:
    """Merge an incoming event without allowing lower-quality data to erase facts.

    A higher-priority source may replace populated fields.  Equal or lower
    priority sources may only fill fields that are currently empty.
    """
    merged = event_db_payload(existing, include_none=True)
    candidate = event_db_payload(incoming, include_none=True)
    incoming_is_higher = _source_priority(candidate) > _source_priority(merged)

    for key, value in candidate.items():
        if key in IMMUTABLE_FIELDS or not is_meaningful(value):
            continue
        if not is_meaningful(merged.get(key)) or incoming_is_higher:
            merged[key] = value

    if existing.get("event_id") is not None:
        merged["event_id"] = existing["event_id"]
    merged["update_time"] = datetime.now(timezone.utc).isoformat()
    return merged
