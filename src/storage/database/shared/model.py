from coze_coding_dev_sdk.database import Base

from typing import Optional
import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Double, Integer, Numeric, PrimaryKeyConstraint, String, Text, Index, text
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column

class HealthCheck(Base):
    __tablename__ = 'health_check'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='health_check_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text('now()'))


class EventInfo(Base):
    __tablename__ = 'event_info'
    __table_args__ = (
        PrimaryKeyConstraint('event_id', name='event_info_pkey'),
        Index('event_info_scope_type_idx', 'scope_type'),
        Index('event_info_status_idx', 'status'),
        Index('event_info_signup_deadline_idx', 'signup_deadline'),
        Index('event_info_contest_level_idx', 'contest_level'),
        Index('event_info_category_idx', 'category'),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="唯一编号")
    title: Mapped[str] = mapped_column(String(256), nullable=False, comment="名称")
    scope_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="校外竞赛/校内竞赛/校内活动")
    category: Mapped[str] = mapped_column(String(64), nullable=True, comment="细分类型")
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="50-100字简介")
    signup_deadline: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), nullable=True, comment="报名截止时间")
    event_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), nullable=True, comment="活动/比赛时间")
    target_major: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, comment="适合专业")
    target_grade: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="适合年级")
    contest_level: Mapped[Optional[str]] = mapped_column(String(32), nullable=True, comment="国家级/省级/校级/院级")
    tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="标签，JSON数组")
    policy_tags: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="政策相关标签，JSON数组")
    source_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, comment="来源名称")
    source_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="来源链接")
    authority_level: Mapped[Optional[str]] = mapped_column(String(16), nullable=True, comment="可信度：高/中/低")
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="报名中", comment="报名中/即将截止/已截止/已结束")
    organizer: Mapped[Optional[str]] = mapped_column(String(256), nullable=True, comment="主办方")
    update_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text('now()'), nullable=True, comment="更新时间")
    original_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="原始通知正文")
