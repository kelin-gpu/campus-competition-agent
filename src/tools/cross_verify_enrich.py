"""三方交叉验证数据补充工具

当数据库记录缺失关键字段时，通过搜索 3 个独立来源交叉验证后补充。
原则：至少 2/3 来源一致才接受，单源信息不入库。
"""

import html
import logging
import re
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import parse_qs, urlparse

import requests

from storage.database.supabase_client import get_supabase_client
from tools.cross_verify_rules import (
    cross_check,
    extract_deadline,
    extract_event_time,
    extract_level,
    extract_organizer,
    source_domain,
)

logger = logging.getLogger(__name__)

# 需要交叉验证的字段
VERIFIABLE_FIELDS = [
    "signup_deadline",
    "event_time",
    "contest_level",
    "organizer",
    "summary",
]

# 搜索 User-Agent（轮换使用）
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
]


def _decode_result_url(raw_url: str) -> str:
    """Resolve DuckDuckGo redirect links to the actual source URL."""
    url = html.unescape(raw_url)
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if parsed.hostname and parsed.hostname.endswith("duckduckgo.com"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            url = target
    parsed = urlparse(url)
    return url if parsed.scheme in {"http", "https"} and parsed.hostname else ""


def _search_web(query: str, timeout: int = 15) -> list[dict]:
    """Return linked search results; snippets alone never count as sources."""
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return []

        results = []
        anchor_pattern = re.compile(
            r'<a\b(?=[^>]*class=["\'][^"\']*\bresult__a\b[^"\']*["\'])'
            r'(?=[^>]*href=["\']([^"\']+)["\'])[^>]*>(.*?)</a>',
            re.DOTALL | re.IGNORECASE,
        )
        for raw_url, raw_title in anchor_pattern.findall(resp.text):
            url = _decode_result_url(raw_url)
            domain = source_domain(url)
            if not domain:
                continue
            title = html.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
            results.append(
                {
                    "source_name": title or domain,
                    "source_url": url,
                    "source_domain": domain,
                }
            )
        return results
    except Exception as e:
        logger.warning(f"搜索失败 ({query[:30]}...): {e}")
        return []


def _fetch_page_text(url: str, timeout: int = 15) -> str:
    """抓取单个页面的文本内容"""
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": USER_AGENTS[1]},
            timeout=timeout,
            allow_redirects=True,
        )
        if resp.status_code != 200:
            return ""
        # 简单去标签
        text = re.sub(r"<script[^>]*>.*?</script>", "", resp.text, flags=re.DOTALL)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:8000]  # 限制长度
    except Exception as e:
        logger.warning(f"抓取页面失败 ({url[:50]}...): {e}")
        return ""


def _extract_deadline(text: str) -> Optional[str]:
    """从文本中提取报名截止日期"""
    return extract_deadline(text)


def _extract_event_time(text: str) -> Optional[str]:
    """从文本中提取比赛/活动时间"""
    return extract_event_time(text)


def _extract_level(text: str) -> Optional[str]:
    """从文本中提取竞赛级别"""
    return extract_level(text)


def _extract_organizer(text: str) -> Optional[str]:
    """从文本中提取主办方"""
    return extract_organizer(text)


def _collect_candidates(title: str, missing_fields: list[str]) -> list[dict]:
    """Fetch up to three pages hosted on different domains."""
    search_results = _search_web(f"{title} 报名截止 比赛时间 竞赛 官网")
    sources = []
    seen_domains = set()
    for result in search_results:
        domain = result["source_domain"]
        if domain in seen_domains:
            continue
        text = _fetch_page_text(result["source_url"])
        if not text:
            continue
        seen_domains.add(domain)
        sources.append({**result, "text": text})
        if len(sources) == 3:
            break

    results = []
    for src in sources:
        candidates = {}
        for field in missing_fields:
            if field == "signup_deadline":
                val = _extract_deadline(src["text"])
            elif field == "event_time":
                val = _extract_event_time(src["text"])
            elif field == "contest_level":
                val = _extract_level(src["text"])
            elif field == "organizer":
                val = _extract_organizer(src["text"])
            else:
                val = None
            if val:
                candidates[field] = val
        results.append(
            {
                "source_name": src["source_name"],
                "source_url": src["source_url"],
                "source_domain": src["source_domain"],
                "candidates": candidates,
                "text_preview": src["text"][:200],
            }
        )

    return results


def _cross_check(candidates_list: list[dict]) -> tuple[dict, dict]:
    """交叉验证：至少两个独立域名一致才接受。"""
    return cross_check(candidates_list)


def cross_verify_and_enrich(event_id: str) -> str:
    """对指定竞赛记录进行三方交叉验证数据补充。

    当某条竞赛记录缺少关键字段（如报名截止时间、比赛时间、级别等）时，
    通过搜索 3 个独立信息源进行交叉验证：
    - 至少 2/3 来源信息一致才接受
    - 单源信息不入库
    - 补充后会记录验证来源和置信度

    Args:
        event_id: 竞赛记录ID（如 EVT-4744E272）

    Returns:
        补充结果摘要（哪些字段被补充、置信度、验证来源）
    """
    supabase = get_supabase_client()

    # 1. 获取当前记录
    resp = (
        supabase.table("event_info")
        .select("event_id,title,signup_deadline,event_time,contest_level,organizer,summary")
        .eq("event_id", event_id)
        .execute()
    )
    data: list = resp.data if isinstance(resp.data, list) else []
    if not data:
        return f"❌ 未找到记录: {event_id}"

    record: dict = data[0] if isinstance(data[0], dict) else {}
    if not record:
        return f"❌ 记录数据异常: {event_id}"

    title: str = str(record.get("title", ""))

    # 2. 找出缺失字段
    missing: list[str] = []
    for f in VERIFIABLE_FIELDS:
        val = record.get(f)
        if not val or str(val).strip() == "":
            missing.append(f)
    if not missing:
        return f"✅ 记录 {event_id} 所有关键字段已完整，无需补充。"

    logger.info(f"[交叉验证] {event_id} '{title[:40]}' 缺失字段: {missing}")

    # 3. 收集候选值
    candidates = _collect_candidates(title, missing)

    # 4. 交叉验证
    verified, trace = _cross_check(candidates)

    if not verified:
        detail_lines = []
        for field, field_trace in trace.items():
            detail_lines.append(
                f"  - {field}: 一致数 "
                f"{field_trace['agree_count']}/{field_trace['total_sources']}"
            )
            detail_lines.extend(
                "    "
                f"{source['source_name']} ({source['source_url']}): "
                f"{source['candidate_value']}"
                for source in field_trace["sources"]
            )
        detail = "\n".join(detail_lines) or "  - 未获得至少两个独立域名的候选值"
        return (
            f"⚠️ 交叉验证未通过，无字段达到 2/3 一致阈值，不补充任何数据。\n\n"
            f"记录: {title}\n"
            f"缺失字段: {missing}\n"
            f"验证详情:\n{detail}"
        )

    # 5. 补充通过验证的字段
    update_data = {}
    verify_log = {}
    for field, value in verified.items():
        update_data[field] = value
        verify_log[field] = {
            "value": value,
            "agree_count": trace[field]["agree_count"],
            "total_sources": trace[field]["total_sources"],
            "sources": [
                source
                for source in trace[field]["sources"]
                if source["candidate_value"] == value
            ],
        }

    update_data["update_time"] = datetime.now(timezone.utc).isoformat()

    supabase.table("event_info").update(update_data).eq("event_id", event_id).execute()

    # 6. 生成结果报告
    lines = [
        f"## 交叉验证补充结果: {title}",
        f"",
        f"| 字段 | 补充值 | 置信度 | 一致来源 |",
        f"|------|--------|--------|----------|",
    ]
    for field, info in verify_log.items():
        confidence = f"{info['agree_count']}/{info['total_sources']}"
        source_links = "<br>".join(
            f"[{source['source_name']}]({source['source_url']})"
            for source in info["sources"]
        )
        lines.append(
            f"| {field} | {str(info['value'])[:40]} | {confidence} | {source_links} |"
        )

    lines.append("")
    lines.append(f"✅ 共补充 {len(verified)} 个字段，未通过验证的字段已丢弃。")

    return "\n".join(lines)
