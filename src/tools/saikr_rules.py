"""Shared filtering and identity rules for Saikr crawl entry points."""

from __future__ import annotations

import re
import urllib.parse


_PROMOTION_KEYWORDS = (
    "培训", "课程", "辅导班", "保研规划", "保研咨询", "保研定位", "保送研究生",
    "留学", "雅思", "托福", "gre", "考研", "考公", "考编", "教师资格证",
    "注册会计师", "会员", "团购", "报名咨询", "扫码添加", "免费领取",
)


def normalize_saikr_title(title: str) -> str:
    """Remove platform SEO suffixes while preserving the event title."""
    cleaned = re.sub(r"\s+", " ", title or "").strip()
    cleaned = re.sub(r"-大学生竞赛-赛氪.*$", "", cleaned).strip()
    cleaned = re.sub(r"-赛氪竞赛网.*$", "", cleaned).strip()
    return cleaned


def saikr_title_identity(title: str) -> str:
    """Stable comparison key used to collapse alias URLs for one contest."""
    cleaned = normalize_saikr_title(title).lower()
    return re.sub(r"[\s（）()【】\[\]《》<>「」“”'\"·—_\-，,。.!！:：;；]", "", cleaned)


def is_likely_saikr_promotion(title: str) -> bool:
    """Reject course, consulting and planning promotions from contest feeds."""
    normalized = saikr_title_identity(title)
    return any(keyword in normalized for keyword in _PROMOTION_KEYWORDS)


def canonicalize_saikr_url(url: str) -> str:
    """Canonicalize desktop/mobile aliases and discard tracking fragments."""
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    if host == "m.saikr.com":
        host = "www.saikr.com"
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunparse((parsed.scheme.lower() or "https", host, path, "", "", ""))
