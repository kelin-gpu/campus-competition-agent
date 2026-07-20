"""
黑客松来源适配器接口。

每个适配器负责一个来源（平台或搜索渠道），实现：
- discover: 发现候选 URL
- parse_listing: 把列表页展开为事件级候选
- parse_detail: 解析详情页
- normalize: 统一候选格式
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class HackathonCandidate:
    """标准化的黑客松候选数据结构（入库前使用）。"""

    __slots__ = (
        "title", "source_name", "source_url", "canonical_url",
        "organizer", "registration_status", "registration_open_time",
        "signup_deadline", "event_start", "event_end", "timezone_str",
        "location", "mode", "tags", "summary",
        "evidence", "discovered_from", "source_authority",
        "raw_date_text", "extraction_method", "platform_id",
    )

    def __init__(self, **kwargs):
        for k in self.__slots__:
            setattr(self, k, kwargs.get(k, None) if k != "tags" else (kwargs.get("tags") or []))

    def to_dict(self) -> dict:
        return {k: getattr(self, k) for k in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "HackathonCandidate":
        return cls(**{k: d.get(k) for k in cls.__slots__})


class BaseAdapter(ABC):
    """来源适配器基类。"""

    name: str = "base"

    @abstractmethod
    def discover(self, ctx, limit: int, now: Optional[datetime] = None) -> List[HackathonCandidate]:
        """发现候选：返回事件级 HackathonCandidate 列表，不是页面级 URL。"""

    def parse_listing(self, html: str, url: str, now: Optional[datetime] = None) -> List[HackathonCandidate]:
        """解析列表页，展开为事件级候选。子类必须重写。"""
        return []

    def parse_detail(self, html: str, url: str, now: Optional[datetime] = None) -> Optional[HackathonCandidate]:
        """解析详情页，返回单个候选。"""
        return None

    def get_platform_id(self, candidate: HackathonCandidate) -> Optional[str]:
        """提取平台级事件 ID，用于跨来源去重。"""
        return candidate.platform_id
