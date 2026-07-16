"""Catalog + Edition + Field Evidence models for campus competition data.

This module splits the original monolithic event_info table into:
- competition_catalog: stable competition metadata
- event_edition: per-edition dynamic information
- field_evidence: provenance for every extracted field
"""

import uuid
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from storage.database.shared.model import Base


class CompetitionCatalog(Base):
    """Stable competition metadata (catalog-level)."""

    __tablename__ = "competition_catalog"

    catalog_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    normalized_title: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    original_title: Mapped[Optional[str]] = mapped_column(Text)
    organizer: Mapped[Optional[str]] = mapped_column(Text)
    category: Mapped[Optional[str]] = mapped_column(Text)
    contest_level: Mapped[Optional[str]] = mapped_column(Text)
    authority_level: Mapped[str] = mapped_column(Text, default="中")
    policy_tags: Mapped[Optional[str]] = mapped_column(Text)
    scope_type: Mapped[str] = mapped_column(Text, default="校外竞赛")
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    is_ministry_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(Text, default="active")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    editions: Mapped[List["EventEdition"]] = relationship("EventEdition", back_populates="catalog", cascade="all, delete-orphan")


class EventEdition(Base):
    """Dynamic per-edition information."""

    __tablename__ = "event_edition"

    edition_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    catalog_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("competition_catalog.catalog_id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    edition_year: Mapped[Optional[int]] = mapped_column(Integer)
    signup_deadline: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    event_time: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, default="待确认")
    source_name: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    target_major: Mapped[str] = mapped_column(Text, default="全校各专业")
    target_grade: Mapped[Optional[str]] = mapped_column(Text)
    tags: Mapped[Optional[str]] = mapped_column(Text)
    policy_tags: Mapped[Optional[str]] = mapped_column(Text)
    extraction_method: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[str]] = mapped_column(Text)
    verification_status: Mapped[Optional[str]] = mapped_column(Text, default="pending_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    catalog: Mapped["CompetitionCatalog"] = relationship("CompetitionCatalog", back_populates="editions")
    evidences: Mapped[List["FieldEvidence"]] = relationship("FieldEvidence", back_populates="edition", cascade="all, delete-orphan")


class FieldEvidence(Base):
    """Provenance record for each extracted field value."""

    __tablename__ = "field_evidence"

    evidence_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    edition_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("event_edition.edition_id", ondelete="CASCADE"),
        nullable=False,
    )
    field_name: Mapped[str] = mapped_column(Text, nullable=False)
    field_value: Mapped[Optional[str]] = mapped_column(Text)
    source_url: Mapped[Optional[str]] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    extraction_method: Mapped[Optional[str]] = mapped_column(Text)
    confidence: Mapped[Optional[str]] = mapped_column(Text)
    verification_status: Mapped[Optional[str]] = mapped_column(Text, default="pending_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    edition: Mapped["EventEdition"] = relationship("EventEdition", back_populates="evidences")
