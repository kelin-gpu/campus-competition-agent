"""Pure parsing and consensus rules for internal cross verification."""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse


def source_domain(url: str) -> str:
    """Return a registrable-domain approximation for source independence."""
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) < 2:
        return ""
    multipart_suffixes = {
        "ac.cn",
        "com.cn",
        "edu.cn",
        "gov.cn",
        "net.cn",
        "org.cn",
        "co.uk",
        "org.uk",
    }
    suffix = ".".join(parts[-2:])
    width = 3 if suffix in multipart_suffixes and len(parts) >= 3 else 2
    return ".".join(parts[-width:])


def extract_deadline(text: str) -> Optional[str]:
    patterns = [
        r"报名截止[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"截止日期[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"截止时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2}).*截止",
        r"报名时间[：:].*?[至~-]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
    ]
    return _extract_date(text, patterns, "23:59:59")


def extract_event_time(text: str) -> Optional[str]:
    patterns = [
        r"比赛时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"活动时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"竞赛时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"举办时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2}).*[举行举办开赛开始]",
    ]
    return _extract_date(text, patterns, "08:00:00")


def _extract_date(text: str, patterns: list[str], clock: str) -> Optional[str]:
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        year, month, day = (int(match.group(index)) for index in range(1, 4))
        try:
            datetime(year, month, day)
        except ValueError:
            continue
        return f"{year:04d}-{month:02d}-{day:02d}T{clock}+08:00"
    return None


def extract_level(text: str) -> Optional[str]:
    level_map = [
        ("国家级", r"国家[级际]|全国"),
        ("省级", r"省[级际]|全省"),
        ("校级", r"校[级际]|全校|南京大学.*主办"),
        ("院级", r"院[级际]|书院.*主办|学院.*主办"),
    ]
    for level, pattern in level_map:
        if re.search(pattern, text):
            return level
    return None


def extract_organizer(text: str) -> Optional[str]:
    patterns = [
        r"主办(?:方|单位)[：:]\s*([^\n。，,]{4,40})",
        r"承办(?:方|单位)[：:]\s*([^\n。，,]{4,40})",
        r"由\s*([^\n。，,]{3,30})\s*主办",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1).strip()
    return None


def cross_check(candidates_list: list[dict]) -> tuple[dict, dict]:
    """Accept a value only when two different domains independently agree."""
    independent: dict[str, dict] = {}
    for candidate in candidates_list:
        domain = candidate.get("source_domain") or source_domain(
            str(candidate.get("source_url", ""))
        )
        if domain and domain not in independent:
            independent[domain] = candidate

    verified: dict = {}
    trace: dict = {}
    all_fields = {
        field
        for candidate in independent.values()
        for field in candidate.get("candidates", {})
    }

    for field in all_fields:
        observations = []
        for domain, candidate in independent.items():
            value = candidate.get("candidates", {}).get(field)
            if value:
                observations.append(
                    {
                        "source_name": candidate.get("source_name", domain),
                        "source_url": candidate.get("source_url", ""),
                        "source_domain": domain,
                        "candidate_value": value,
                    }
                )

        values = [item["candidate_value"] for item in observations]
        counter = Counter(values)
        agree_value, agree_count = counter.most_common(1)[0]
        trace[field] = {
            "sources": observations,
            "values_found": values,
            "agree_count": agree_count,
            "total_sources": len(independent),
        }
        if len(independent) >= 2 and agree_count >= 2:
            verified[field] = agree_value

    return verified, trace
