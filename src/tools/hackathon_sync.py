"""
黑客松专项同步工作流。

职责：
- 编排黑客松搜索 → 抓取 → 解析 → 过滤 → 去重 → 入库全流程
- 不将工具注册给 Agent（属于后台同步链路，非用户可调用管理工具）
- 入库复用 data_sync_workflow.sync_events_to_db 与 catalog_service.merge_event
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from tools.data_sync_workflow import sync_events_to_db
from tools.hackathon_search import (
    _load_sources_config,
    _is_safe_url,
    fetch_detail_page,
    extract_dates,
    is_hackathon_page,
    detect_registration_status,
    deduplicate_candidates,
    filter_event_by_time,
    _extract_organizer,
    _extract_tags,
    HACKATHON_SEARCH_LIMIT,
    HACKATHON_MAX_FUTURE_DAYS,
)

logger = logging.getLogger(__name__)


def _search_hackathons(ctx, limit: int = 60) -> List[dict]:
    """Use SearchClient to discover hackathon candidate pages."""
    cfg = _load_sources_config()
    queries = cfg.get("search_queries", [])

    if not queries:
        logger.warning("No hackathon search queries configured")
        return []

    try:
        from coze_coding_dev_sdk import SearchClient
        client = SearchClient(ctx=ctx)
    except Exception as e:
        logger.error(f"Failed to init SearchClient: {e}")
        return []

    all_candidates: List[dict] = []
    per_query = max(1, limit // len(queries))

    for query in queries:
        if len(all_candidates) >= limit:
            break
        try:
            response = client.web_search(query=query, count=per_query)
            if not response or not response.web_items:
                continue
            for item in response.web_items:
                url = getattr(item, "url", "") or ""
                if not _is_safe_url(url):
                    continue
                all_candidates.append({
                    "title": getattr(item, "title", ""),
                    "source_url": url,
                    "snippet": getattr(item, "snippet", "") or "",
                    "source_name": getattr(item, "site_name", "") or "",
                    "discovery_query": query,
                })
            time.sleep(0.5)
        except Exception as e:
            logger.warning(f"Search query '{query[:40]}' failed: {e}")

    logger.info(f"Search discovered {len(all_candidates)} candidate URLs")
    return all_candidates[:limit]


def run_hackathon_sync(
    ctx=None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """Run full hackathon search & sync workflow.

    Args:
        ctx: Request context
        dry_run: If True, search/parse/filter only, no DB write
        now: Injectable current time (for testing)

    Returns:
        Statistics dict with detailed action log.
    """
    if ctx is None:
        from coze_coding_utils.runtime_ctx.context import new_context
        ctx = new_context(method="hackathon_sync")

    if now is None:
        now = datetime.now(timezone.utc)

    cfg = _load_sources_config()
    fetch_timeout = cfg.get("fetch_timeout_sec", 15)
    fetch_retries = cfg.get("fetch_retries", 2)
    max_future_days = cfg.get("max_future_days", HACKATHON_MAX_FUTURE_DAYS)
    search_limit = int(os.getenv("HACKATHON_SEARCH_LIMIT", str(HACKATHON_SEARCH_LIMIT)))

    stats: Dict[str, Any] = {
        "discovered": 0,
        "fetched": 0,
        "accepted": 0,
        "expired_filtered": 0,
        "closed_filtered": 0,
        "invalid_date_filtered": 0,
        "too_far_future_filtered": 0,
        "event_passed_filtered": 0,
        "unverified_skipped": 0,
        "not_hackathon": 0,
        "fetch_failed": 0,
        "duplicates": 0,
        "added": 0,
        "updated": 0,
        "errors": 0,
        "details": [],
    }

    logger.info("=== Hackathon sync started ===")

    # 1. Search
    candidates = _search_hackathons(ctx, limit=search_limit)
    stats["discovered"] = len(candidates)

    if not candidates:
        logger.info("No hackathon candidates discovered")
        return stats

    # 2. Fetch detail pages + parse
    accepted_candidates: List[dict] = []

    for i, cand in enumerate(candidates):
        url = cand.get("source_url", "")
        if not url:
            continue

        # Rate limit
        if i > 0:
            time.sleep(cfg.get("fetch_delay_sec", 1.5))

        logger.debug(f"Fetching [{i+1}/{len(candidates)}]: {cand.get('title', 'unknown')[:50]}")

        page_text = fetch_detail_page(url, timeout=fetch_timeout, retries=fetch_retries)
        if page_text is None:
            stats["fetch_failed"] += 1
            stats["details"].append({
                "action": "fetch_failed",
                "title": cand.get("title", "")[:50],
                "source_url": url[:120],
            })
            continue

        stats["fetched"] += 1

        # 3. Identify hackathon
        if not is_hackathon_page(cand.get("title", ""), page_text):
            stats["not_hackathon"] += 1
            stats["details"].append({
                "action": "not_hackathon",
                "title": cand.get("title", "")[:50],
                "source_url": url[:120],
            })
            continue

        # 4. Extract dates and status
        dates = extract_dates(page_text, now=now)
        reg_status = detect_registration_status(page_text)

        # 5. Filter by time
        accepted, reason = filter_event_by_time(
            dates.get("signup_deadline"),
            dates.get("event_time"),
            reg_status,
            now=now,
            max_future_days=max_future_days,
        )

        if not accepted:
            stats[reason] = stats.get(reason, 0) + 1
            stats["details"].append({
                "action": reason,
                "title": cand.get("title", "")[:50],
                "source_url": url[:120],
                "deadline": dates.get("signup_deadline"),
                "event_time": dates.get("event_time"),
                "reg_status": reg_status,
            })
            continue

        # 6. Build event record
        organizer = _extract_organizer(page_text)
        tags = _extract_tags(page_text)

        event = {
            "title": cand.get("title", ""),
            "scope_type": "校外竞赛",
            "category": "黑客松",
            "summary": (cand.get("snippet", "") or page_text[:200]),
            "signup_deadline": _to_iso8601(dates.get("signup_deadline")),
            "event_time": _to_iso8601(dates.get("event_time")),
            "target_major": "",
            "target_grade": "",
            "contest_level": "",
            "tags": tags,
            "policy_tags": "",
            "source_name": _determine_source_name(cand.get("source_name", ""), url),
            "source_url": url,
            "authority_level": _determine_authority(url),
            "status": "待确认",
            "organizer": organizer or "",
            "original_text": page_text[:500],
            "is_ministry_approved": False,
            "extraction_method": "hackathon_sync",
            "confidence": "medium",
        }

        accepted_candidates.append(event)
        stats["accepted"] += 1

    # 7. Deduplicate
    after_dedup = deduplicate_candidates(accepted_candidates)
    stats["duplicates"] = len(accepted_candidates) - len(after_dedup)

    # 8. Write to DB (skip if dry_run)
    if not dry_run and after_dedup:
        try:
            db_stats = sync_events_to_db(after_dedup, ctx=ctx)
            stats["added"] = db_stats.get("added", 0)
            stats["updated"] = db_stats.get("updated", 0)
            stats["errors"] = db_stats.get("errors", 0)
        except Exception as e:
            logger.error(f"DB sync failed: {e}", exc_info=True)
            stats["errors"] = len(after_dedup)
            stats["details"].append({
                "action": "db_sync_error",
                "error": str(e)[:200],
            })

    logger.info(f"Hackathon sync complete: discovered={stats['discovered']}, "
                f"fetched={stats['fetched']}, accepted={stats['accepted']}, "
                f"added={stats['added']}, errors={stats['errors']}")

    return stats


def _to_iso8601(date_str: Optional[str]) -> Optional[str]:
    """Convert date string to ISO 8601 with timezone."""
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    except ValueError:
        return date_str


def _determine_source_name(raw_source: str, url: str) -> str:
    """Determine clean source name."""
    if raw_source and raw_source not in ("", "unknown"):
        return raw_source[:80]
    parsed = urlparse(url)
    domain = parsed.netloc.lower()
    if "devpost" in domain:
        return "Devpost"
    if "devfolio" in domain:
        return "Devfolio"
    if "mlh.io" in domain:
        return "MLH"
    return domain[:80]


def _determine_authority(url: str) -> str:
    """Determine authority level from source."""
    domain = urlparse(url).netloc.lower()
    high_domains = {"devpost.com", "devfolio.co", "mlh.io"}
    for hd in high_domains:
        if hd in domain:
            return "高"
    edu_domains = {".edu", ".edu.cn", ".ac.", ".ac.cn", ".gov", ".gov.cn"}
    for ed in edu_domains:
        if ed in domain:
            return "高"
    return "中"
