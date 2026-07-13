from coze_coding_dev_sdk.database import Base

from typing import Optional
import datetime

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Double, Integer, Numeric, PrimaryKeyConstraint, String, Text, Index, text, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column

class HealthCheck(Base):
    __tablename__ = 'health_check'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='health_check_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    updated_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=text('now()'))


class UserProfile(Base):
    __tablename__ = "user_profile"
    __table_args__ = (
        PrimaryKeyConstraint('user_id', name='user_profile_pkey'),
        {'comment': '用户画像表'},
    )

    user_id: Mapped[str] = mapped_column(Text, primary_key=True, comment="用户唯一标识")
    nickname: Mapped[Optional[str]] = mapped_column(Text, comment="昵称")
    college: Mapped[Optional[str]] = mapped_column(Text, comment="学院")
    major: Mapped[Optional[str]] = mapped_column(Text, comment="专业")
    grade: Mapped[Optional[str]] = mapped_column(Text, comment="年级")
    interest_tags: Mapped[Optional[dict]] = mapped_column(JSON, comment="兴趣标签JSON数组")
    focus_contests: Mapped[Optional[dict]] = mapped_column(JSON, comment="关注竞赛ID列表")
    notify_preference: Mapped[Optional[str]] = mapped_column(Text, server_default=text("'daily'"), comment="推送偏好")
    last_active_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), comment="最后活跃时间")
    create_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=func.now(), comment="创建时间")
    update_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True), server_default=func.now(), comment="更新时间")


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
    is_ministry_approved: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, server_default=text('false'), comment="是否教育部目录竞赛")
