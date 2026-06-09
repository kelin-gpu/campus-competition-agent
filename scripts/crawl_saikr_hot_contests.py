#!/usr/bin/env python3
"""Crawl Saikr hot contests and export a structured Excel workbook."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import html


START_URL = "https://www.saikr.com/index/hot/contest"
DEFAULT_OUTPUT = Path("dataset") / "saikr_hot_contests_top50.xlsx"
MAX_DETAIL_TEXT = 1200

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

FIELD_LABELS = {
    "organizer": ["主办方", "主办单位", "组织单位", "主办机构", "主办"],
    "signup_deadline": ["报名截止", "报名时间", "参赛报名", "截止时间", "报名日期"],
    "contest_time": ["比赛时间", "竞赛时间", "活动时间", "比赛日期", "初赛时间", "决赛时间"],
    "participant_scope": ["参赛对象", "参赛资格", "面向对象", "参赛人群", "参与对象"],
    "fee_or_status": ["报名费", "参赛费用", "费用", "报名状态", "状态"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = re.sub(r"\s+", " ", value.replace("\u00a0", " ")).strip()
    return value


def visible_lines(node: html.HtmlElement) -> list[str]:
    for bad in node.xpath(".//script|.//style|.//noscript"):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    text = node.text_content()
    lines = [clean_text(line) for line in re.split(r"[\r\n]+", text)]
    return [line for line in lines if line]


def compact_excerpt(lines: list[str], limit: int = MAX_DETAIL_TEXT) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for line in lines:
        if line in seen:
            continue
        seen.add(line)
        if len(line) <= 2:
            continue
        kept.append(line)
        if len(" ".join(kept)) >= limit:
            break
    return " ".join(kept)[:limit]


def normalize_count(value: str | None) -> int | None:
    if not value:
        return None
    raw = value.replace(",", "").strip()
    match = re.search(r"(\d+(?:\.\d+)?)(\s*[万wWkK]?)", raw)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).strip().lower()
    if unit in {"万", "w"}:
        number *= 10000
    elif unit == "k":
        number *= 1000
    return int(number)


def extract_count(text: str, keywords: list[str]) -> int | None:
    patterns = [
        rf"(\d+(?:\.\d+)?\s*[万wWkK]?)\s*(?:{'|'.join(map(re.escape, keywords))})",
        rf"(?:{'|'.join(map(re.escape, keywords))})\s*[:：]?\s*(\d+(?:\.\d+)?\s*[万wWkK]?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return normalize_count(match.group(1))
    return None


def label_regex(labels: list[str]) -> re.Pattern[str]:
    return re.compile(rf"({'|'.join(map(re.escape, labels))})\s*[:：]?\s*(.+)?")


def extract_labeled_field(lines: list[str], labels: list[str], max_len: int = 180) -> str:
    pattern = label_regex(labels)
    for idx, line in enumerate(lines):
        match = pattern.search(line)
        if not match:
            continue
        value = clean_text(match.group(2))
        if value and value not in labels:
            return value[:max_len]
        for next_line in lines[idx + 1 : idx + 4]:
            next_value = clean_text(next_line)
            if next_value and not label_regex(sum(FIELD_LABELS.values(), [])).match(next_value):
                return next_value[:max_len]
    joined = " ".join(lines)
    match = re.search(rf"({'|'.join(map(re.escape, labels))})\s*[:：]\s*(.{{2,{max_len}}})", joined)
    return clean_text(match.group(2))[:max_len] if match else ""


def extract_date_like(lines: list[str]) -> str:
    joined = " ".join(lines)
    patterns = [
        r"20\d{2}[-/.年]\d{1,2}[-/.月]\d{1,2}日?(?:\s*[-至~—]\s*20?\d{0,2}[-/.年]?\d{1,2}[-/.月]\d{1,2}日?)?",
        r"\d{1,2}月\d{1,2}日(?:\s*[-至~—]\s*\d{1,2}月\d{1,2}日)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, joined)
        if match:
            return match.group(0)
    return ""


def is_saikr_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return parsed.scheme in {"http", "https"} and (host == "saikr.com" or host.endswith(".saikr.com"))


def is_contest_detail_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    path = parsed.path.lower()
    return is_saikr_url(url) and bool(re.search(r"/(vse|vs|contest|races)/", path))


def fetch_html(url: str, timeout: int = 20) -> str:
    if not is_saikr_url(url):
        raise ValueError(f"Refusing to fetch non-Saikr URL: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
    return body.decode(encoding, errors="replace")


def nearest_text_container(anchor: html.HtmlElement) -> html.HtmlElement:
    for xpath in [
        "ancestor::li[1]",
        "ancestor::div[contains(@class,'item')][1]",
        "ancestor::div[contains(@class,'contest')][1]",
        "ancestor::div[contains(@class,'list')][1]",
        "ancestor::div[1]",
    ]:
        found = anchor.xpath(xpath)
        if found:
            return found[0]
    return anchor


def extract_tags(node: html.HtmlElement) -> str:
    candidates: list[str] = []
    for item in node.xpath(".//*[contains(@class,'tag') or contains(@class,'label')]"):
        text = clean_text(item.text_content())
        if text and len(text) <= 30:
            candidates.append(text)
    deduped = list(dict.fromkeys(candidates))
    return "、".join(deduped[:8])


def extract_summary(title: str, lines: list[str]) -> str:
    ignored = {"查看详情", "立即报名", "报名", "收藏", "分享"}
    for line in lines:
        if line == title or line in ignored:
            continue
        if any(word in line for word in ["浏览", "关注", "主办", "报名时间", "比赛时间"]):
            continue
        if 12 <= len(line) <= 260:
            return line
    return ""


def parse_list_page(page_html: str, source_url: str, limit: int) -> list[dict[str, Any]]:
    doc = html.fromstring(page_html)
    seen: set[str] = set()
    records: list[dict[str, Any]] = []

    for anchor in doc.xpath("//a[@href]"):
        href = urllib.parse.urljoin(source_url, anchor.get("href") or "")
        if not is_contest_detail_url(href):
            continue
        title = clean_text(anchor.text_content())
        if len(title) < 4 or title in {"查看详情", "立即报名", "报名参赛"}:
            continue

        normalized = urllib.parse.urldefrag(href)[0]
        if normalized in seen:
            continue
        seen.add(normalized)

        container = nearest_text_container(anchor)
        lines = visible_lines(container)
        container_text = " ".join(lines)

        records.append(
            {
                "rank": len(records) + 1,
                "title": title[:160],
                "detail_url": normalized,
                "organizer": extract_labeled_field(lines, FIELD_LABELS["organizer"]),
                "summary": extract_summary(title, lines),
                "view_count": extract_count(container_text, ["浏览", "浏览量", "阅读", "人浏览"]),
                "follow_count": extract_count(container_text, ["关注", "收藏", "人关注"]),
                "signup_deadline": "",
                "contest_time": "",
                "participant_scope": "",
                "fee_or_status": "",
                "tags": extract_tags(container),
                "source_page": source_url,
                "scraped_at": "",
                "parse_status": "list_only",
                "parse_notes": "",
                "detail_text_excerpt": "",
            }
        )
        if len(records) >= limit:
            break

    return records


def parse_detail_page(record: dict[str, Any], page_html: str) -> dict[str, Any]:
    doc = html.fromstring(page_html)
    lines = visible_lines(doc)
    notes: list[str] = []

    for field, labels in FIELD_LABELS.items():
        value = extract_labeled_field(lines, labels)
        if value:
            record[field] = value

    if not record.get("contest_time"):
        fallback_date = extract_date_like(lines)
        if fallback_date:
            record["contest_time"] = fallback_date
            notes.append("contest_time used first date-like fallback")

    if not record.get("summary"):
        title = record.get("title", "")
        record["summary"] = extract_summary(title, lines)
        if record["summary"]:
            notes.append("summary filled from detail page")

    detail_tags = extract_tags(doc)
    if detail_tags:
        existing_tags = [tag for tag in str(record.get("tags", "")).split("、") if tag]
        merged_tags = list(dict.fromkeys(existing_tags + detail_tags.split("、")))
        record["tags"] = "、".join(merged_tags[:12])

    record["detail_text_excerpt"] = compact_excerpt(lines)

    missing = [
        name
        for name in ["signup_deadline", "contest_time", "participant_scope"]
        if not record.get(name)
    ]
    if missing:
        notes.append("missing fields: " + ", ".join(missing))

    record["parse_status"] = "detail_ok" if not missing else "detail_partial"
    record["parse_notes"] = "; ".join(notes)
    return record


def enrich_details(records: list[dict[str, Any]], sleep_seconds: float) -> None:
    scraped_at = now_iso()
    for record in records:
        record["scraped_at"] = scraped_at
        try:
            time.sleep(sleep_seconds)
            detail_html = fetch_html(record["detail_url"])
            parse_detail_page(record, detail_html)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            record["parse_status"] = "detail_failed"
            record["parse_notes"] = f"{type(exc).__name__}: {exc}"


def find_node() -> str:
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    if bundled.exists():
        return str(bundled)
    node = shutil.which("node")
    if node:
        return node
    raise RuntimeError("Node.js was not found. Use the bundled Codex runtime or install Node.js.")


def build_excel(records: list[dict[str, Any]], meta: dict[str, Any], output_path: Path) -> None:
    builder = Path(__file__).with_name("build_saikr_hot_contests_xlsx.mjs")
    if not builder.exists():
        raise RuntimeError(f"Excel builder not found: {builder}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"records": records, "meta": meta}
    with tempfile.TemporaryDirectory(prefix="saikr_hot_") as temp_dir:
        json_path = Path(temp_dir) / "saikr_hot_contests.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(
            [find_node(), str(builder), str(json_path), str(output_path)],
            check=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Saikr hot contests and export Excel.")
    parser.add_argument("--url", default=START_URL, help="Saikr hot contest page URL.")
    parser.add_argument("--limit", type=int, default=50, help="Maximum contest count to export.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output .xlsx path.")
    parser.add_argument("--sleep", type=float, default=0.6, help="Seconds to wait between detail requests.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.limit <= 0 or args.limit > 50:
        raise ValueError("--limit must be between 1 and 50")
    if not is_saikr_url(args.url):
        raise ValueError("--url must be a saikr.com URL")

    started_at = now_iso()
    page_html = fetch_html(args.url)
    records = parse_list_page(page_html, args.url, args.limit)
    if not records:
        raise RuntimeError("No contest links were parsed from the hot contest page.")

    enrich_details(records, args.sleep)

    meta = {
        "source_url": args.url,
        "requested_limit": args.limit,
        "actual_count": len(records),
        "started_at": started_at,
        "finished_at": now_iso(),
        "notes": "Only public Saikr hot contest and contest detail pages were fetched.",
    }
    output_path = Path(args.output)
    build_excel(records, meta, output_path)

    print(f"Exported {len(records)} contests to {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
