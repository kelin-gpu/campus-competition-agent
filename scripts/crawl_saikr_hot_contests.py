#!/usr/bin/env python3
"""Crawl Saikr hot contests and export detail-page fields to Excel."""

from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from lxml import html

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from tools.saikr_rules import (
    canonicalize_saikr_url,
    is_likely_saikr_promotion,
    normalize_saikr_title,
    saikr_title_identity,
)


DESKTOP_URL = "https://www.saikr.com/index/hot/contest"
MOBILE_URL = "https://m.saikr.com/index/hot/contest"
DEFAULT_OUTPUT = Path("dataset") / "saikr_hot_contests_top50.xlsx"

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
    "category": ["竞赛类别", "赛事类别", "类别"],
    "registration_time": ["报名时间", "报名截止", "报名日期", "参赛报名"],
    "contest_time": ["竞赛时间", "比赛时间", "活动时间", "比赛日期", "作品提交时间", "参赛作品提交时间"],
    "participant_scope": ["参赛对象", "参赛资格", "面向对象", "参赛人群", "参与对象"],
    "fee_or_status": ["报名费", "参赛费用", "费用", "报名状态", "状态"],
}


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    value = html_lib.unescape(value).replace("\u00a0", " ")
    return re.sub(r"\s+", " ", value).strip()


def sanitize_html(page_html: str) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", page_html)


def parse_doc(page_html: str) -> html.HtmlElement:
    return html.fromstring(sanitize_html(page_html))


def visible_text_lines(doc: html.HtmlElement) -> list[str]:
    doc = html.fromstring(html.tostring(doc, encoding="unicode"))
    for bad in doc.xpath(".//script|.//style|.//noscript"):
        parent = bad.getparent()
        if parent is not None:
            parent.remove(bad)
    lines = [clean_text(line) for line in doc.text_content().splitlines()]
    return [line for line in lines if line]


def compact_visible_text(doc: html.HtmlElement, limit: int = 600) -> str:
    seen: set[str] = set()
    kept: list[str] = []
    for line in visible_text_lines(doc):
        if line in seen or len(line) <= 2:
            continue
        seen.add(line)
        kept.append(line)
        if len(" ".join(kept)) >= limit:
            break
    return " ".join(kept)[:limit]


def full_visible_text(doc: html.HtmlElement) -> str:
    return "\n".join(visible_text_lines(doc))


def meta_content(doc: html.HtmlElement, name: str) -> str:
    values = doc.xpath(
        f"//meta[translate(@name,'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')='{name.lower()}']/@content"
    )
    return clean_text(values[0]) if values else ""


def page_title(doc: html.HtmlElement) -> str:
    title = clean_text(doc.xpath("string(//title)"))
    title = re.sub(r"[-_]?大学生竞赛[-_]?赛氪$", "", title).strip()
    if title.startswith("赛氪 - 全国大学生竞赛活动平台"):
        return ""
    return title


def is_saikr_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    host = parsed.netloc.lower()
    return parsed.scheme in {"http", "https"} and (host == "saikr.com" or host.endswith(".saikr.com"))


def is_contest_detail_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return is_saikr_url(url) and bool(re.search(r"/(vse|vs|contest|races)/", parsed.path.lower()))


def detail_url_candidates(url: str) -> list[str]:
    parsed = urllib.parse.urlparse(url)
    candidates = [url]
    path = parsed.path.rstrip("/")
    if parsed.netloc.endswith("saikr.com") and path.startswith("/vse/"):
        candidates.append(urllib.parse.urlunparse(("https", "m.saikr.com", path, "", "", "")))
        slug = path[len("/vse/") :]
        if slug:
            candidates.append(urllib.parse.urlunparse(("https", "m.saikr.com", f"/{slug}", "", "", "")))
    deduped: list[str] = []
    for candidate in candidates:
        if candidate not in deduped:
            deduped.append(candidate)
    return deduped


def is_generic_detail_page(doc: html.HtmlElement, lines: list[str], expected_title: str) -> bool:
    title = clean_text(doc.xpath("string(//title)"))
    text = "\n".join(lines)
    if title.startswith("赛氪 - 全国大学生竞赛活动平台"):
        return True
    if len(text) < 200 and "全国大学生竞赛活动平台" in text:
        return True
    if expected_title and expected_title[:8] not in text and "竞赛详情" not in text and len(text) < 600:
        return True
    return False


def fetch_html(url: str, timeout: int = 20) -> tuple[str, int]:
    if not is_saikr_url(url):
        raise ValueError(f"Refusing to fetch non-Saikr URL: {url}")
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        body = response.read()
        encoding = response.headers.get_content_charset() or "utf-8"
        return body.decode(encoding, errors="replace"), response.status


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


def parse_list_page(page_html: str, source_url: str) -> list[dict[str, str]]:
    doc = parse_doc(page_html)
    records: list[dict[str, str]] = []
    seen: set[str] = set()

    for anchor in doc.xpath("//a[@href]"):
        href = urllib.parse.urljoin(source_url, anchor.get("href") or "")
        href = urllib.parse.urldefrag(href)[0]
        title = normalize_saikr_title(clean_text(anchor.text_content()))
        if not is_contest_detail_url(href) or href in seen:
            continue
        if len(title) < 4 or title in {"查看详情", "立即报名", "报名参赛"}:
            continue
        if is_likely_saikr_promotion(title):
            continue

        href = canonicalize_saikr_url(href)
        seen.add(href)
        records.append(
            {
                "list_title": title,
                "detail_url": href,
                "source_url": source_url,
                "list_card_text": clean_text(nearest_text_container(anchor).text_content()),
            }
        )

    return records


def merge_records(record_groups: list[list[dict[str, str]]], limit: int) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for group in record_groups:
        for record in group:
            canonical_url = canonicalize_saikr_url(record["detail_url"])
            title_key = saikr_title_identity(record.get("list_title", ""))
            if canonical_url in seen_urls or (title_key and title_key in seen_titles):
                continue
            record["detail_url"] = canonical_url
            seen_urls.add(canonical_url)
            if title_key:
                seen_titles.add(title_key)
            record["rank"] = len(merged) + 1
            merged.append(record)
            if len(merged) >= limit:
                return merged
    return merged


def collect_list_records(urls: list[str], limit: int) -> tuple[list[dict[str, Any]], list[str]]:
    groups: list[list[dict[str, str]]] = []
    notes: list[str] = []
    for url in urls:
        try:
            page_html, status = fetch_html(url)
            records = parse_list_page(page_html, url)
            groups.append(records)
            notes.append(f"{url}: HTTP {status}, {len(records)} contest links")
            if len(merge_records(groups, limit)) >= limit:
                break
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            notes.append(f"{url}: {type(exc).__name__}: {exc}")
    return merge_records(groups, limit), notes


def value_after_label_in_text(text: str, labels: list[str], max_len: int = 180) -> str:
    for label in labels:
        patterns = [
            rf"{re.escape(label)}\s*[:：]\s*([^,，。；;\n]{{1,{max_len}}})",
            rf"{re.escape(label)}\s+([^,，。；;\n]{{1,{max_len}}})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                return clean_text(match.group(1))[:max_len]
    return ""


def value_after_label_in_lines(lines: list[str], labels: list[str], max_len: int = 180) -> str:
    label_pattern = re.compile("|".join(re.escape(label) for label in labels))
    all_label_pattern = re.compile("|".join(re.escape(label) for labels_ in FIELD_LABELS.values() for label in labels_))

    for idx, line in enumerate(lines):
        if not label_pattern.search(line):
            continue
        inline = value_after_label_in_text(line, labels, max_len)
        if inline:
            return inline
        for next_line in lines[idx + 1 : idx + 5]:
            if all_label_pattern.search(next_line):
                continue
            if next_line:
                return next_line[:max_len]
    return ""


def extract_summary(doc: html.HtmlElement, meta_description: str) -> str:
    intro = value_after_label_in_text(meta_description, ["竞赛简介", "简介"], 260)
    if intro:
        return intro
    for line in visible_text_lines(doc):
        if 30 <= len(line) <= 260 and not any(skip in line for skip in ["登录", "注册", "立即报名", "关注", "分享"]):
            return line
    return meta_description[:260]


def enrich_from_detail(record: dict[str, Any], fetched_at: str) -> None:
    try:
        selected_doc = None
        selected_lines: list[str] = []
        selected_status: int | str = ""
        for candidate_url in detail_url_candidates(record["detail_url"]):
            detail_html, status = fetch_html(candidate_url)
            doc = parse_doc(detail_html)
            lines = visible_text_lines(doc)
            selected_doc = doc
            selected_lines = lines
            selected_status = status
            if not is_generic_detail_page(doc, lines, record.get("list_title", "")):
                break

        if selected_doc is None:
            raise RuntimeError("no detail page fetched")

        doc = selected_doc
        lines = selected_lines
        joined = "\n".join(lines)
        meta_description = meta_content(doc, "description")

        record["title"] = page_title(doc) or record["list_title"]
        record["organizer"] = (
            value_after_label_in_text(meta_description, FIELD_LABELS["organizer"])
            or value_after_label_in_lines(lines, FIELD_LABELS["organizer"])
        )
        record["category"] = value_after_label_in_lines(lines, FIELD_LABELS["category"])
        record["registration_time"] = value_after_label_in_lines(lines, FIELD_LABELS["registration_time"])
        record["contest_time"] = (
            value_after_label_in_text(meta_description, FIELD_LABELS["contest_time"])
            or value_after_label_in_lines(lines, FIELD_LABELS["contest_time"])
        )
        record["participant_scope"] = value_after_label_in_lines(lines, FIELD_LABELS["participant_scope"])
        record["fee_or_status"] = value_after_label_in_lines(lines, FIELD_LABELS["fee_or_status"])
        record["summary"] = extract_summary(doc, meta_description)
        record["detail_text"] = full_visible_text(doc)
        record["http_status"] = selected_status
    except (urllib.error.URLError, TimeoutError, ValueError, OSError, RuntimeError) as exc:
        record["title"] = record["list_title"]
        record["organizer"] = ""
        record["category"] = ""
        record["registration_time"] = ""
        record["contest_time"] = ""
        record["participant_scope"] = ""
        record["fee_or_status"] = ""
        record["summary"] = clean_text(record.get("list_card_text", ""))
        record["detail_text"] = ""
        record["http_status"] = type(exc).__name__

    record["fetched_at"] = fetched_at
    record.pop("list_title", None)
    record.pop("list_card_text", None)


def enrich_details(records: list[dict[str, Any]], sleep_seconds: float) -> None:
    fetched_at = now_iso()
    for record in records:
        time.sleep(sleep_seconds)
        enrich_from_detail(record, fetched_at)


def find_node() -> str:
    bundled = Path.home() / ".cache" / "codex-runtimes" / "codex-primary-runtime" / "dependencies" / "node" / "bin" / "node.exe"
    if bundled.exists():
        return str(bundled)
    node = shutil.which("node")
    if node:
        return node
    raise RuntimeError("Node.js was not found. Use the bundled Codex runtime or install Node.js.")


def build_excel(records: list[dict[str, Any]], output_path: Path) -> None:
    builder = Path(__file__).with_name("build_saikr_hot_contests_xlsx.mjs")
    if not builder.exists():
        raise RuntimeError(f"Excel builder not found: {builder}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="saikr_hot_fields_") as temp_dir:
        json_path = Path(temp_dir) / "saikr_hot_contests_fields.json"
        json_path.write_text(json.dumps({"records": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        subprocess.run(
            [find_node(), str(builder), str(json_path), str(output_path)],
            check=True,
            cwd=str(Path(__file__).resolve().parents[1]),
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Crawl Saikr hot contests and export detail-page fields.")
    parser.add_argument("--url", default=DESKTOP_URL, help="Primary Saikr hot contest page URL.")
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

    list_urls = [args.url]
    if args.url != MOBILE_URL:
        list_urls.append(MOBILE_URL)

    records, list_notes = collect_list_records(list_urls, args.limit)
    if not records:
        raise RuntimeError("No contest links were parsed from Saikr hot contest pages.")

    enrich_details(records, args.sleep)
    output_path = Path(args.output)
    build_excel(records, output_path)

    if len(records) < args.limit:
        print(f"Requested {args.limit} contests, but public Saikr pages exposed {len(records)} unique contest links.")
        for note in list_notes:
            print(note)
    print(f"Exported {len(records)} contests to {output_path.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
