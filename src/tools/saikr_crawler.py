#!/usr/bin/env python3
"""赛氪热门竞赛爬虫（线上版）"""
from __future__ import annotations

import html as html_lib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from lxml import html

from tools.saikr_rules import (
    canonicalize_saikr_url,
    is_likely_saikr_promotion,
    normalize_saikr_title,
    saikr_title_identity,
)

DESKTOP_URL = "https://www.saikr.com/index/hot/contest"
MOBILE_URL = "https://m.saikr.com/index/hot/contest"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

FIELD_LABELS = {
    "organizer": ["主办方", "主办单位", "组织单位", "主办机构", "主办"],
    "category": ["竞赛类别", "赛事类别", "类别"],
    "registration_time": ["报名时间", "报名截止", "报名日期", "参赛报名"],
    "contest_time": ["竞赛时间", "比赛时间", "活动时间", "比赛日期", "作品提交时间", "参赛作品提交时间"],
    "participant_scope": ["参赛对象", "参赛资格", "面向对象", "参赛人群", "参与对象"],
    "fee_or_status": ["报名费", "参赛费用", "费用", "报名状态", "状态"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_text(value: str) -> str:
    if not value:
        return ""
    value = html_lib.unescape(value).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def sanitize_html(page_html: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", page_html)


def parse_doc(page_html: str):
    return html.fromstring(sanitize_html(page_html))


def visible_text_lines(doc):
    doc = html.fromstring(html.tostring(doc, encoding="unicode"))
    for bad in doc.xpath(".//script|.//style|.//noscript"):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    lines = [clean_text(line) for line in doc.text_content().splitlines()]
    return [line for line in lines if line]


def full_visible_text(doc) -> str:
    return "\n".join(visible_text_lines(doc))


def meta_content(doc, name: str) -> str:
    values = doc.xpath(
        f"//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{name.lower()}']/@content"
    )
    return clean_text(values[0]) if values else ""


def page_title(doc) -> str:
    title = clean_text(doc.xpath("string(//title)"))
    title = re.sub(r"[-_]?大学生竞赛[-_]?赛氪$", "", title).strip()
    if title.startswith("赛氪 - 全国大学生竞赛活动平台"):
        return ""
    return title


def is_saikr_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return parsed.scheme in {"http", "https"} and (
        host == "saikr.com" or host.endswith(".saikr.com")
    )


def is_contest_detail_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return is_saikr_url(url) and bool(
        re.search(r"/(vse|vs|contest|races)/", parsed.path.lower())
    )


def detail_url_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    candidates = [url]
    path = parsed.path.rstrip("/")
    if parsed.netloc.endswith("saikr.com") and path.startswith("/vse/"):
        candidates.append(
            urllib.parse.urlunparse(("https", "m.saikr.com", path, "", "", ""))
        )
        slug = path[len("/vse/"):]
        if slug:
            candidates.append(
                urllib.parse.urlunparse(("https", "m.saikr.com", f"/{slug}", "", "", ""))
            )
    deduped = []
    for c in candidates:
        if c not in deduped:
            deduped.append(c)
    return deduped


def is_generic_detail_page(doc, lines: list[str], expected_title: str) -> bool:
    title = clean_text(doc.xpath("string(//title)"))
    text = "\n".join(lines)
    if title.startswith("赛氪 - 全国大学生竞赛活动平台"):
        return True
    if len(text) < 200 and "全国大学生竞赛活动平台" in text:
        return True
    if (
        expected_title
        and expected_title[:8] not in text
        and "竞赛详情" not in text
        and len(text) < 600
    ):
        return True
    return False


def fetch_html(url: str, timeout: int = 20) -> str:
    if not is_saikr_url(url):
        raise ValueError(f"Refusing to fetch non-Saikr URL: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
        return body.decode(encoding, errors="replace")


def extract_hot_contest_links(doc) -> list[dict]:
    """从热门竞赛列表页提取竞赛链接"""
    links = []
    seen_urls = {}  # url -> title (only store when we have a valid title)
    seen_titles = set()

    # 查找所有指向竞赛详情页的链接
    for a_tag in doc.xpath("//a[@href]"):
        href = a_tag.get("href", "")
        if not href:
            continue

        # 补全相对URL
        if href.startswith("/"):
            href = "https://www.saikr.com" + href
        elif not href.startswith("http"):
            continue

        if not is_contest_detail_url(href):
            continue

        # 标准化URL（统一用www）
        normalized = canonicalize_saikr_url(href)

        # 提取标题（先于去重检查，避免空标题占用URL）
        title = clean_text(a_tag.text_content())
        if not title or len(title) < 4:
            # 尝试从 title 属性获取
            title = clean_text(a_tag.get("title", ""))
        if not title or len(title) < 4:
            continue

        title = normalize_saikr_title(title)
        title_key = saikr_title_identity(title)
        if is_likely_saikr_promotion(title):
            continue

        # 同一赛事可能由多个别名 URL 暴露，同时按 URL 和标题去重。
        if normalized in seen_urls or (title_key and title_key in seen_titles):
            continue
        seen_urls[normalized] = title
        if title_key:
            seen_titles.add(title_key)

        links.append({
            "title": title,
            "detail_url": normalized,
            "url": normalized,
        })

    return links


def extract_detail_fields(doc, lines: list[str]) -> dict:
    """从详情页提取结构化字段"""
    fields = {}
    text = "\n".join(lines)

    for field_name, labels in FIELD_LABELS.items():
        for label in labels:
            pattern = rf"{label}[：:]\s*(.+?)(?:\n|$)"
            match = re.search(pattern, text)
            if match:
                value = clean_text(match.group(1))
                if value and len(value) < 200:
                    fields[field_name] = value
                    break

    # 提取报名时间（特殊处理）
    if "registration_time" not in fields:
        for pattern in [
            r"报名[时日]间[：:]\s*(\d{4}[./\-]\d{1,2}[./\-]\d{1,2}[^。\n]{0,50})",
            r"报名截止[：:]\s*(\d{4}[./\-]\d{1,2}[./\-]\d{1,2}[^。\n]{0,30})",
        ]:
            match = re.search(pattern, text)
            if match:
                fields["registration_time"] = clean_text(match.group(1))
                break

    return fields


def fetch_contest_detail(url: str) -> dict:
    """获取单个竞赛详情页的信息"""
    try:
        page_html = fetch_html(url)
        doc = parse_doc(page_html)
        lines = visible_text_lines(doc)

        title = page_title(doc)
        if not title:
            return {}

        if is_generic_detail_page(doc, lines, title):
            return {}

        fields = extract_detail_fields(doc, lines)
        detail_text = full_visible_text(doc)

        return {
            "title": title,
            "detail_url": url,
            "detail_text": detail_text[:3000],
            **fields,
        }
    except Exception:
        return {}


def crawl_saikr_hot_contests(
    limit: int = 50,
    sleep_seconds: float = 0.6,
    fetch_details: bool = False,
) -> dict:
    """
    爬取赛氪热门竞赛列表

    Args:
        limit: 最多爬取多少条
        sleep_seconds: 每次请求间隔（秒）
        fetch_details: 是否同时爬取每条竞赛的详情页（耗时较长）

    Returns:
        {
            "records": [{"title": ..., "detail_url": ..., ...}, ...],
            "crawled_at": "2026-01-01T00:00:00+08:00",
            "source": "saikr_hot_contest",
            "total_found": int,
            "errors": []
        }
    """
    result = {
        "records": [],
        "crawled_at": now_iso(),
        "source": "saikr_hot_contest",
        "total_found": 0,
        "errors": [],
    }

    # Step 1: 抓取热门竞赛列表页
    try:
        page_html = fetch_html(DESKTOP_URL)
        doc = parse_doc(page_html)
    except Exception as e:
        result["errors"].append(f"Failed to fetch hot contest list: {e}")
        # 尝试移动端
        try:
            page_html = fetch_html(MOBILE_URL)
            doc = parse_doc(page_html)
        except Exception as e2:
            result["errors"].append(f"Failed to fetch mobile list: {e2}")
            return result

    # Step 2: 提取竞赛链接
    contest_links = extract_hot_contest_links(doc)
    result["total_found"] = len(contest_links)

    if not contest_links:
        result["errors"].append("No contest links found on hot page")
        return result

    # Step 3: 限制数量
    contest_links = contest_links[:limit]

    # Step 4: 如果需要详情，逐个爬取
    records = []
    for i, link_info in enumerate(contest_links):
        if fetch_details:
            detail = fetch_contest_detail(link_info["detail_url"])
            if detail:
                record = {**link_info, **detail}
            else:
                record = link_info
        else:
            record = link_info

        # 确保必要字段
        record.setdefault("source", "赛氪")
        record.setdefault("source_url", "https://www.saikr.com/index/hot/contest")
        records.append(record)

        if sleep_seconds > 0 and i < len(contest_links) - 1:
            time.sleep(sleep_seconds)

    result["records"] = records
    return result


if __name__ == "__main__":
    # 本地测试
    print("Crawling saikr hot contests...")
    res = crawl_saikr_hot_contests(limit=5, sleep_seconds=0.5, fetch_details=False)
    print(f"Found {res['total_found']} contests, got {len(res['records'])} records")
    if res["errors"]:
        print(f"Errors: {res['errors']}")
    for r in res["records"][:5]:
        print(f"  {r['title'][:50]} -> {r['detail_url']}")
