"""Service layer for catalog/edition/evidence data model.

Provides deterministic merge/upsert logic for:
- CompetitionCatalog (stable metadata)
- EventEdition (per-edition dynamic info)
- FieldEvidence (field-level provenance)
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from sqlalchemy import select
from sqlalchemy.orm import Session

from storage.database.catalog_models import CompetitionCatalog, EventEdition, FieldEvidence
from storage.database.db import get_engine


def _normalize_title(title: Optional[str]) -> str:
    """Extract stable competition name from a specific edition title."""
    if not title:
        return ""
    t = title.strip()
    # Remove year prefixes
    t = re.sub(r"^\d{4}年", "", t)
    # Remove edition markers
    t = re.sub(r"第[一二三四五六七八九十\d]+届", "", t)
    # Remove year suffixes in parentheses
    t = re.sub(r"[\(\)（）]\d{4}[\(\)（）]", "", t)
    # Remove stage/region markers
    t = re.sub(r"初赛|决赛|复赛|分区赛|赛区赛|赛区|第一轮报名|第二轮报名|报名通知", "", t)
    # Remove extra spaces
    t = re.sub(r"\s+", "", t)
    return t.strip()


def _extract_edition_year(title: Optional[str]) -> Optional[int]:
    """Extract edition year from title if present."""
    if not title:
        return None
    m = re.search(r"(\d{4})", title)
    if m:
        year = int(m.group(1))
        if 2020 <= year <= 2030:
            return year
    return None


def _compute_status(signup_deadline: Optional[datetime]) -> str:
    """Determine edition status from deadline."""
    if signup_deadline is None:
        return "暂无本届信息"
    now = datetime.now(timezone.utc)
    if signup_deadline.tzinfo is None:
        signup_deadline = signup_deadline.replace(tzinfo=timezone.utc)
    days = (signup_deadline - now).days
    if days < 0:
        return "已截止"
    if days <= 3:
        return "即将截止"
    return "报名中"


def _coerce_datetime(value: Any) -> Optional[datetime]:
    """Coerce ISO string or datetime to timezone-aware datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except ValueError:
            return None
    return None


def get_or_create_catalog(
    session: Session,
    title: str,
    **catalog_fields: Any,
) -> Tuple[CompetitionCatalog, bool]:
    """Get existing catalog by normalized title or create a new one.

    Returns (catalog, created).
    """
    normalized = _normalize_title(title)
    if not normalized:
        normalized = title.strip() if title else str(uuid.uuid4())

    catalog = session.execute(
        select(CompetitionCatalog).where(CompetitionCatalog.normalized_title == normalized)
    ).scalar_one_or_none()

    if catalog is not None:
        # Optionally merge stable fields if missing
        for key, value in catalog_fields.items():
            if getattr(catalog, key) in (None, "", "中") and value:
                setattr(catalog, key, value)
        return catalog, False

    catalog = CompetitionCatalog(
        normalized_title=normalized,
        original_title=title,
        **catalog_fields,
    )
    session.add(catalog)
    session.flush()
    return catalog, True


def upsert_edition(
    session: Session,
    catalog: CompetitionCatalog,
    event_id: str,
    title: str,
    **edition_fields: Any,
) -> Tuple[EventEdition, bool]:
    """Upsert an event edition and attach it to a catalog.

    Returns (edition, created).
    """
    edition = session.execute(
        select(EventEdition).where(EventEdition.event_id == event_id)
    ).scalar_one_or_none()

    deadline = _coerce_datetime(edition_fields.get("signup_deadline"))
    event_time = _coerce_datetime(edition_fields.get("event_time"))

    # Timeline validation: event_time must not precede signup_deadline
    if deadline and event_time and event_time < deadline:
        event_time = None

    status = edition_fields.get("status")
    if not status or status == "报名中":
        # Recompute from deadline unless explicitly provided
        status = _compute_status(deadline)

    if edition is None:
        edition = EventEdition(
            catalog_id=catalog.catalog_id,
            event_id=event_id,
            title=title,
            edition_year=_extract_edition_year(title),
            signup_deadline=deadline,
            event_time=event_time,
            status=status,
            **{k: v for k, v in edition_fields.items() if k not in ("signup_deadline", "event_time", "status")},
        )
        session.add(edition)
        session.flush()
        return edition, True

    # Update dynamic fields if new data is more complete
    edition.title = title
    edition.edition_year = _extract_edition_year(title)
    if deadline is not None:
        edition.signup_deadline = deadline
    if event_time is not None:
        edition.event_time = event_time
    edition.status = status

    for key, value in edition_fields.items():
        if key in ("signup_deadline", "event_time", "status"):
            continue
        if value not in (None, "") and getattr(edition, key) in (None, ""):
            setattr(edition, key, value)

    session.flush()
    return edition, False


def record_evidence(
    session: Session,
    edition: EventEdition,
    field_name: str,
    field_value: Any,
    source_url: str,
    extraction_method: str,
    confidence: str,
    verification_status: str = "pending_review",
) -> Optional[FieldEvidence]:
    """Record provenance for a field value."""
    if field_value in (None, ""):
        return None
    evidence = FieldEvidence(
        edition_id=edition.edition_id,
        field_name=field_name,
        field_value=str(field_value)[:2000],
        source_url=source_url or "",
        fetched_at=datetime.now(timezone.utc),
        extraction_method=extraction_method,
        confidence=confidence,
        verification_status=verification_status,
    )
    session.add(evidence)
    return evidence


def merge_event(
    session: Session,
    event_data: Dict[str, Any],
    extraction_method: str = "manual",
    confidence: str = "medium",
) -> EventEdition:
    """High-level helper: merge raw event data into catalog + edition + evidence."""
    title = event_data.get("title", "")
    event_id = event_data.get("event_id") or f"EVT-{uuid.uuid4().hex[:8].upper()}"

    # Stable fields -> catalog
    catalog_fields = {
        "organizer": event_data.get("organizer") or "",
        "category": event_data.get("category") or "其他",
        "contest_level": event_data.get("contest_level") or "",
        "authority_level": event_data.get("authority_level") or "中",
        "policy_tags": event_data.get("policy_tags") or "",
        "scope_type": event_data.get("scope_type") or "校外竞赛",
        "source_name": event_data.get("source_name") or "",
        "source_url": event_data.get("source_url") or "",
        "is_ministry_approved": event_data.get("source_name") in (
            "教育部竞赛目录",
            "教育部竞赛目录（2015-2018）",
        ),
    }

    catalog, _ = get_or_create_catalog(session, title, **catalog_fields)

    # Dynamic fields -> edition
    edition_fields = {
        "summary": event_data.get("summary") or "",
        "source_name": event_data.get("source_name") or "",
        "source_url": event_data.get("source_url") or "",
        "signup_deadline": event_data.get("signup_deadline"),
        "event_time": event_data.get("event_time"),
        "target_major": event_data.get("target_major") or "全校各专业",
        "target_grade": event_data.get("target_grade") or "",
        "tags": event_data.get("tags") or "",
        "policy_tags": event_data.get("policy_tags") or "",
        "extraction_method": extraction_method,
        "confidence": confidence,
        "verification_status": event_data.get("verification_status") or "pending_review",
    }

    edition, _ = upsert_edition(session, catalog, event_id, title, **edition_fields)

    # Record evidence for each non-empty field
    evidence_fields = [
        ("signup_deadline", edition.signup_deadline),
        ("event_time", edition.event_time),
        ("summary", edition.summary),
        ("source_url", edition.source_url),
        ("organizer", catalog.organizer),
        ("contest_level", catalog.contest_level),
    ]
    for field_name, field_value in evidence_fields:
        record_evidence(
            session,
            edition,
            field_name,
            field_value,
            source_url=edition.source_url or catalog.source_url,
            extraction_method=extraction_method,
            confidence=confidence,
            verification_status=edition.verification_status,
        )

    session.commit()
    return edition


def get_edition_by_event_id(session: Session, event_id: str) -> Optional[EventEdition]:
    return session.execute(
        select(EventEdition).where(EventEdition.event_id == event_id)
    ).scalar_one_or_none()


def merge_catalog(session: Session, catalog_data: Dict[str, Any]) -> CompetitionCatalog:
    """Merge Ministry or stable catalog data into competition_catalog only."""
    title = catalog_data.get("title", "")
    catalog_fields = {
        "organizer": catalog_data.get("organizer") or "",
        "category": catalog_data.get("category") or "其他",
        "contest_level": catalog_data.get("contest_level") or "",
        "authority_level": catalog_data.get("authority_level") or "中",
        "policy_tags": catalog_data.get("policy_tags") or "",
        "scope_type": catalog_data.get("scope_type") or "校外竞赛",
        "source_name": catalog_data.get("source_name") or "",
        "source_url": catalog_data.get("source_url") or "",
        "is_ministry_approved": catalog_data.get("source_name") in (
            "教育部竞赛目录",
            "教育部竞赛目录（2015-2018）",
        ),
    }
    catalog, _ = get_or_create_catalog(session, title, **catalog_fields)
    session.commit()
    return catalog


def list_catalogs(session: Session) -> List[CompetitionCatalog]:
    return list(session.execute(select(CompetitionCatalog)).scalars().all())
