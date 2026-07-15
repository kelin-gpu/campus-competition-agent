"""三方交叉验证数据补充工具

当数据库记录缺失关键字段时，通过搜索 3 个独立来源交叉验证后补充。
原则：至少 2/3 来源一致才接受，单源信息不入库。
"""

import json
import logging
import re
from collections import Counter
from datetime import datetime
from typing import Optional

import requests
from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

from storage.database.supabase_client import get_supabase_client

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


def _search_web(query: str, timeout: int = 15) -> str:
    """通过 DuckDuckGo 搜索并返回页面文本摘要"""
    try:
        resp = requests.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": USER_AGENTS[0]},
            timeout=timeout,
        )
        if resp.status_code != 200:
            return ""
        # 提取搜索结果摘要
        snippets = re.findall(
            r'class="result__snippet"[^>]*>(.*?)</a>',
            resp.text,
            re.DOTALL,
        )
        return "\n".join(
            re.sub(r"<[^>]+>", "", s).strip() for s in snippets[:5]
        )
    except Exception as e:
        logger.warning(f"搜索失败 ({query[:30]}...): {e}")
        return ""


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
    patterns = [
        r"报名截止[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"截止日期[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"截止时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2}).*截止",
        r"报名时间[：:].*?[至~-]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return f"{y:04d}-{mo:02d}-{d:02d}T23:59:59+08:00"
            except ValueError:
                continue
    return None


def _extract_event_time(text: str) -> Optional[str]:
    """从文本中提取比赛/活动时间"""
    patterns = [
        r"比赛时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"活动时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"竞赛时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"举办时间[：:]\s*(\d{4})[年/-](\d{1,2})[月/-](\d{1,2})",
        r"(\d{4})[年/-](\d{1,2})[月/-](\d{1,2}).*[举行举办开赛开始]",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            try:
                return f"{y:04d}-{mo:02d}-{d:02d}T08:00:00+08:00"
            except ValueError:
                continue
    return None


def _extract_level(text: str) -> Optional[str]:
    """从文本中提取竞赛级别"""
    level_map = [
        ("国家级", r"国家[级际]|全国"),
        ("省级", r"省[级际]|全省"),
        ("校级", r"校[级际]|全校|南京大学.*主办"),
        ("院级", r"院[级际]|书院.*主办|学院.*主办"),
    ]
    for level, pat in level_map:
        if re.search(pat, text):
            return level
    return None


def _extract_organizer(text: str) -> Optional[str]:
    """从文本中提取主办方"""
    patterns = [
        r"主办(方|单位)[：:]\s*([^\n。，,]{4,40})",
        r"承办(方|单位)[：:]\s*([^\n。，,]{4,40})",
        r"由\s*([^\n。，,]{3,30})\s*主办",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(1).strip()
    return None


def _collect_candidates(title: str, missing_fields: list[str]) -> list[dict]:
    """从 3 个独立来源收集候选值"""
    sources = []

    # 来源 1：直接搜索竞赛名
    text1 = _search_web(f"{title} 报名截止 比赛时间")
    sources.append({"name": "DuckDuckGo搜索", "text": text1})

    # 来源 2：搜索竞赛名 + 赛事官网
    text2 = _search_web(f"{title} 竞赛 官网")
    sources.append({"name": "DuckDuckGo搜索(官网)", "text": text2})

    # 来源 3：搜索竞赛名 + 2026
    text3 = _search_web(f"{title} 2026")
    sources.append({"name": "DuckDuckGo搜索(2026)", "text": text3})

    # 对每个来源提取候选值
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
                "source_name": src["name"],
                "candidates": candidates,
                "text_preview": src["text"][:200],
            }
        )

    return results


def _cross_check(candidates_list: list[dict]) -> dict:
    """交叉验证：2/3 以上来源一致才接受"""
    verified = {}
    trace = {}

    # 收集所有字段
    all_fields = set()
    for c in candidates_list:
        all_fields.update(c["candidates"].keys())

    for field in all_fields:
        values = []
        for c in candidates_list:
            val = c["candidates"].get(field)
            if val:
                values.append(val)

        if not values:
            continue

        # 统计一致次数
        from collections import Counter

        counter = Counter(values)
        most_common = counter.most_common(1)[0]
        agree_count = most_common[1]
        total = len(candidates_list)  # 总来源数

        trace[field] = {
            "values_found": values,
            "agree_count": agree_count,
            "total_sources": total,
        }

        # 至少 2/3 一致
        if agree_count >= 2:
            verified[field] = most_common[0]

    return verified, trace


@tool
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
    ctx = request_context.get() or new_context(method="cross_verify_and_enrich")
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
        detail = "\n".join(
            f"  - {f}: {t['values_found']} (一致数: {t['agree_count']}/{t['total_sources']})"
            for f, t in trace.items()
        )
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
        }

    update_data["update_time"] = "now()"

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
        lines.append(f"| {field} | {info['value'][:40]} | {confidence} | {confidence} |")

    lines.append("")
    lines.append(f"✅ 共补充 {len(verified)} 个字段，未通过验证的字段已丢弃。")

    return "\n".join(lines)
