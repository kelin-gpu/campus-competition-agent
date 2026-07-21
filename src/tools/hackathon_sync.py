"""
黑客松专项同步工作流 v2。

架构：
- 多来源适配器发现
- 列表页展开为事件级候选
- 预去重后再抓详情
- 上下文感知日期解析
- 每来源独立统计漏斗
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from tools.data_sync_workflow import sync_events_to_db
from tools.hackathon_adapters.base import HackathonCandidate
from tools.hackathon_adapters.devfolio import DevfolioAdapter
from tools.hackathon_adapters.mlh import MLHAdapter
from tools.hackathon_adapters.hackclub import HackClubAdapter
from tools.hackathon_adapters.devpost import DevpostAdapter
from tools.hackathon_adapters.general_search import GeneralSearchAdapter
from tools.hackathon_search import (
    _load_sources_config,
    _is_safe_url,
    _normalize_url,
    _normalize_title_light,
    _strip_html_tags,
    _extract_title,
    _extract_organizer,
    _extract_tags,
    fetch_detail_page,
    is_hackathon_page,
    detect_registration_status,
    detect_registration_status_v2,
    parse_dates_contextual,
    parse_date_ranges,
    filter_event_by_time,
    is_listing_page,
    DomainRateLimiter,
    HACKATHON_SEARCH_LIMIT,
    HACKATHON_MAX_FUTURE_DAYS,
)

logger = logging.getLogger(__name__)

# 适配器注册表
_ADAPTERS = {
    "devfolio": DevfolioAdapter(),
    "mlh": MLHAdapter(),
    "hackclub": HackClubAdapter(),
    "devpost": DevpostAdapter(),
    "general_search": GeneralSearchAdapter(),
}

# 并发限制
_MAX_WORKERS = 4
_FETCH_TIMEOUT = 15
_FETCH_RETRIES = 2


def _per_source_stats_template() -> dict:
    return {
        "listing_pages_fetched": 0,
        "listing_items_found": 0,
        "search_results_found": 0,
        "prefetch_duplicates": 0,
        "truncated_by_limit": 0,
        "structured_candidates": 0,
        "detail_candidates": 0,
        "details_fetched": 0,
        "parse_success": 0,
        "parse_failed": 0,
        "open_detected": 0,
        "upcoming_detected": 0,
        "closed_filtered": 0,
        "expired_filtered": 0,
        "invalid_date_filtered": 0,
        "unverified_skipped": 0,
        "too_far_future_filtered": 0,
        "not_hackathon": 0,
        "postparse_duplicates": 0,
        "accepted": 0,
        "added": 0,
        "updated": 0,
        "errors": 0,
    }


def run_hackathon_sync(
    ctx=None,
    dry_run: bool = False,
    now: Optional[datetime] = None,
    sources: Optional[List[str]] = None,
    limit: int = 60,
) -> dict:
    """Run full hackathon discovery & sync workflow.

    Args:
        ctx: Request context
        dry_run: If True, no DB write
        now: Injectable current time
        sources: List of adapter names to use (default: all)
        limit: Max candidates total

    Returns:
        Stats dict with per-source breakdown.
    """
    if ctx is None:
        from coze_coding_utils.runtime_ctx.context import new_context
        ctx = new_context(method="hackathon_sync")

    if now is None:
        now = datetime.now(timezone.utc)

    cfg = _load_sources_config()
    max_future_days = cfg.get("max_future_days", HACKATHON_MAX_FUTURE_DAYS)
    search_limit = max(
        1,
        int(os.getenv("HACKATHON_SEARCH_LIMIT", str(limit or HACKATHON_SEARCH_LIMIT))),
    )

    logger.info("=== Hackathon sync v2 started ===")

    # Determine which sources to use
    source_names = sources or list(_ADAPTERS.keys())

    # Phase 1: Discover candidates from all sources
    all_candidates: List[HackathonCandidate] = []
    per_source_stats: Dict[str, dict] = defaultdict(_per_source_stats_template)

    for src_name in source_names:
        if src_name not in _ADAPTERS:
            continue
        adapter = _ADAPTERS[src_name]
        try:
            # ``limit`` is a global processing cap.  Dividing it by the number
            # of adapters starved productive sources (for example MLH returned
            # only four of sixteen events when the global limit was twenty).
            candidates = adapter.discover(ctx, limit=search_limit)
        except Exception as e:
            logger.warning(f"Source {src_name} discovery failed: {e}")
            per_source_stats[src_name]["errors"] += 1
            continue

        src_stats = per_source_stats[src_name]
        if src_name == "general_search":
            src_stats["search_results_found"] = len(candidates)
        else:
            src_stats["listing_items_found"] = len(candidates)

        all_candidates.extend(candidates)

    total_discovered = len(all_candidates)
    logger.info(f"Phase 1: {total_discovered} candidates from {len(source_names)} sources")

    # Phase 2: Pre-fetch dedup
    all_candidates, dedup_by_source = _dedup_candidates_with_stats(all_candidates)
    prefetch_dup = total_discovered - len(all_candidates)
    for src_name, count in dedup_by_source.items():
        per_source_stats[src_name]["prefetch_duplicates"] += count

    # Apply the global cap only after all sources have had a chance to
    # discover and de-duplicate. Round-robin selection avoids source-order
    # bias while still keeping network work bounded.
    all_candidates, truncated_by_source = _limit_candidates_round_robin(
        all_candidates, search_limit
    )
    for src_name, count in truncated_by_source.items():
        per_source_stats[src_name]["truncated_by_limit"] += count

    logger.info(f"Phase 2: {len(all_candidates)} after pre-fetch dedup")

    # Phase 3: Fetch detail pages
    accepted_candidates: List[HackathonCandidate] = []
    rate_limiter = DomainRateLimiter(default_delay=0.5)
    details_list: List[dict] = []

    # Separate listing page candidates from detail page candidates
    structured_candidates = [c for c in all_candidates if _is_structured_listing_candidate(c)]
    structured_candidate_ids = {id(c) for c in structured_candidates}
    listing_candidates = [
        c for c in all_candidates
        if _is_listing_url(c.source_url or "") and id(c) not in structured_candidate_ids
    ]
    detail_candidates = [
        c for c in all_candidates
        if not _is_listing_url(c.source_url or "") and id(c) not in structured_candidate_ids
    ]
    detail_candidates.extend(structured_candidates)
    for candidate in structured_candidates:
        per_source_stats[_candidate_source_key(candidate)]["structured_candidates"] += 1

    # Expand listing pages
    for lc in listing_candidates:
        src_name = _detect_source(lc.source_url or "")
        if src_name in _ADAPTERS:
            adapter = _ADAPTERS[src_name]
            rate_limiter.wait(lc.source_url or "")
            html = fetch_detail_page(lc.source_url or "")
            if html:
                per_source_stats[src_name]["listing_pages_fetched"] += 1
                sub_candidates = adapter.parse_listing(html, lc.source_url or "")
                for raw_candidate in sub_candidates:
                    sc = raw_candidate
                    if isinstance(raw_candidate, dict):
                        sc = adapter.normalize(raw_candidate)
                    if not isinstance(sc, HackathonCandidate):
                        continue
                    sc.discovered_from = f"{src_name}_listing"
                    detail_candidates.append(sc)

    # Dedup again after listing expansion
    detail_candidates = _dedup_candidates_v2(detail_candidates)

    # Fetch details for each candidate
    for i, cand in enumerate(detail_candidates):
        url = cand.source_url or ""
        if not url:
            continue

        src_name = _candidate_source_key(cand)
        per_source_stats[src_name]["detail_candidates"] += 1

        if _is_structured_listing_candidate(cand):
            # High/medium-authority platform listings already contain an
            # event-level title and timeline. Validate those fields directly;
            # re-fetching the shared listing URL used to collapse all events.
            signup_deadline = cand.signup_deadline
            event_start = cand.event_start
            event_end = cand.event_end
            reg_status = (
                _normalize_registration_status(cand.registration_status)
                or ("upcoming" if event_start else None)
            )
            per_source_stats[src_name]["parse_success"] += 1
        else:
            rate_limiter.wait(url)
            html = fetch_detail_page(url, timeout=_FETCH_TIMEOUT, retries=_FETCH_RETRIES)

            if html is None:
                per_source_stats[src_name]["parse_failed"] += 1
                details_list.append({"action": "fetch_failed", "title": cand.title, "source_url": url})
                continue

            per_source_stats[src_name]["details_fetched"] += 1

            # Check if it's a hackathon (non-listing-page check). Search/list
            # discovery can provide a URL without a title, so retain the page
            # title before generic parsing and final validation.
            text = _strip_html_tags(html)
            page_title = _extract_title(html)
            if not cand.title and page_title:
                cand.title = page_title
            if not is_hackathon_page(cand.title or page_title, text):
                per_source_stats[src_name]["not_hackathon"] += 1
                details_list.append({"action": "not_hackathon", "title": cand.title, "source_url": url})
                continue

            # Parse detail page
            if src_name in _ADAPTERS:
                parsed = _ADAPTERS[src_name].parse_detail(html, url)
            else:
                parsed = None

            if parsed:
                # parsed may be a dict (from adapter) or HackathonCandidate
                if isinstance(parsed, HackathonCandidate):
                    cand = parsed
                else:
                    # Merge dict values into existing candidate
                    _merge_parsed(cand, parsed)
                per_source_stats[src_name]["parse_success"] += 1
            else:
                # Use generic HTML parsing
                cand = _parse_generic_detail(cand, html, text)

            # Extract dates with context-aware parser. Structured discovery
            # values are authoritative fallbacks when a detail page omits one.
            dates = parse_dates_contextual(text)
            range_dates = parse_date_ranges(text)
            signup_deadline = (
                range_dates.get("signup_deadline")
                or dates.get("signup_deadline")
                or cand.signup_deadline
            )
            event_start = (
                range_dates.get("event_start")
                or dates.get("event_start")
                or cand.event_start
            )
            event_end = (
                range_dates.get("event_end")
                or dates.get("event_end")
                or cand.event_end
            )

            # Detect registration status, keeping an adapter's explicit value
            # as a fallback.
            reg_status = (
                detect_registration_status_v2(text)
                or detect_registration_status(text)
                or _normalize_registration_status(cand.registration_status)
            )

        # Detect open/upcoming/closed
        if reg_status == "open":
            per_source_stats[src_name]["open_detected"] += 1
        elif reg_status == "upcoming":
            per_source_stats[src_name]["upcoming_detected"] += 1

        # Filter by time
        accepted, reason = filter_event_by_time(
            signup_deadline,
            event_start,
            reg_status,
            now=now,
            max_future_days=max_future_days,
            event_end_str=event_end,
        )

        detail_entry = {
            "action": reason,
            "title": cand.title,
            "source_url": url,
            "source": src_name,
            "deadline": signup_deadline,
            "event_start": event_start,
            "event_end": event_end,
            "reg_status": reg_status,
        }

        if not accepted:
            per_source_stats[src_name][reason] = per_source_stats[src_name].get(reason, 0) + 1
            details_list.append(detail_entry)
            continue

        # Update candidate with parsed data
        cand.signup_deadline = signup_deadline
        cand.event_start = event_start
        cand.event_end = event_end
        cand.registration_status = reg_status
        cand.extraction_method = "contextual_date_parse"

        if not (cand.title or "").strip():
            per_source_stats[src_name]["parse_failed"] += 1
            detail_entry["action"] = "missing_title_filtered"
            details_list.append(detail_entry)
            continue

        accepted_candidates.append(cand)
        per_source_stats[src_name]["accepted"] += 1

    # Phase 4: Post-parse dedup across sources
    accepted_candidates, post_duplicates = _cross_source_dedup_with_stats(accepted_candidates)
    for src_name, count in post_duplicates.items():
        per_source_stats[src_name]["postparse_duplicates"] += count
    # ``accepted`` represents the final post-dedup rows, not the provisional
    # pre-dedup count from the parsing loop.
    for stats in per_source_stats.values():
        stats["accepted"] = 0
    for candidate in accepted_candidates:
        per_source_stats[_candidate_source_key(candidate)]["accepted"] += 1

    # Phase 5: Write to DB
    if not dry_run and accepted_candidates:
        try:
            events = [_candidate_to_event(c) for c in accepted_candidates]
            db_stats = sync_events_to_db(events, ctx=ctx)
            for src_name in per_source_stats:
                per_source_stats[src_name]["added"] = db_stats.get("added", 0) // len(per_source_stats)
                per_source_stats[src_name]["updated"] = db_stats.get("updated", 0) // len(per_source_stats)
                per_source_stats[src_name]["errors"] += db_stats.get("errors", 0)
        except Exception as e:
            logger.error(f"DB sync failed: {e}", exc_info=True)
            for src_name in per_source_stats:
                per_source_stats[src_name]["errors"] += len(accepted_candidates) // len(per_source_stats)
            details_list.append({"action": "db_sync_error", "error": str(e)[:200]})

    # Build final stats
    total_accepted = len(accepted_candidates)
    total_added = sum(s["added"] for s in per_source_stats.values())

    result = {
        "discovered": total_discovered,
        "prefetch_duplicates": prefetch_dup,
        "truncated_by_limit": sum(truncated_by_source.values()),
        "detail_page_candidates": len(detail_candidates),
        "accepted": total_accepted,
        "added": total_added,
        "sources": dict(per_source_stats),
        "details": details_list,
    }

    # In dry-run mode, include the complete accepted dataset for audit/export,
    # plus a compact sample retained for backward-compatible CLI display.
    if dry_run and accepted_candidates:
        result["accepted_records"] = [
            {
                "title": c.title,
                "source_url": c.source_url,
                "source_name": c.source_name,
                "signup_deadline": c.signup_deadline,
                "event_start": c.event_start,
                "event_end": c.event_end,
                "registration_status": c.registration_status,
                "organizer": c.organizer,
                "location": c.location,
                "mode": c.mode,
                "tags": list(c.tags or []),
            }
            for c in accepted_candidates
        ]
        result["accepted_samples"] = result["accepted_records"][:20]

    logger.info(f"Hackathon sync complete: {total_discovered} discovered, "
                f"{total_accepted} accepted, {total_added} added")
    return result


def _is_listing_url(url: str) -> bool:
    """Check if URL is a listing page (not a single event)."""
    if not url:
        return True
    lower = url.lower()
    listing_patterns = [
        "/hackathons" in lower and not re.search(r'/hackathons/[^/]+/.', lower),
        lower.endswith("/hackathons"),
        lower.endswith("/events"),
        lower.endswith("/seasons"),
        re.search(r'/seasons/\d{4}/events/?$', lower),
    ]
    return any(listing_patterns)


def _detect_source(url: str) -> str:
    """Detect which adapter source a URL belongs to."""
    if not url:
        return "unknown"
    domain = urlparse(url).netloc.lower()
    if "devfolio" in domain:
        return "devfolio"
    if "mlh.io" in domain or "mlh.com" in domain:
        return "mlh"
    if "hackclub" in domain or "hackathons.hackclub" in domain:
        return "hackclub"
    if "devpost" in domain:
        return "devpost"
    return "general_search"


def _candidate_source_key(candidate: HackathonCandidate) -> str:
    """Return the adapter key that originally discovered a candidate."""
    discovered_from = (candidate.discovered_from or "").lower()
    source_name = (candidate.source_name or "").lower()
    for key in _ADAPTERS:
        if discovered_from.startswith(key) or source_name == key:
            return key
    if "websearch" in source_name or "general" in discovered_from:
        return "general_search"
    if "hackclub" in source_name:
        return "hackclub"
    if "devfolio" in source_name:
        return "devfolio"
    if "devpost" in source_name:
        return "devpost"
    if source_name == "mlh":
        return "mlh"
    return _detect_source(candidate.source_url or "") or "unknown"


def _is_structured_listing_candidate(candidate: HackathonCandidate) -> bool:
    """Whether platform discovery supplied enough authoritative data to filter.

    A dated event from an authoritative platform listing is already an
    event-level record even when several records share the listing page URL.
    """
    return bool(
        candidate.title.strip()
        and (candidate.event_start or candidate.event_end or candidate.signup_deadline)
        and candidate.source_authority in {"high", "medium"}
    )


def _normalize_registration_status(value: Optional[str]) -> Optional[str]:
    normalized = (value or "").strip().lower()
    status_map = {
        "open": "open",
        "live": "open",
        "registration": "open",
        "upcoming": "upcoming",
        "closed": "closed",
        "ended": "ended",
    }
    return status_map.get(normalized)


def _dedup_candidates_v2(candidates: List[HackathonCandidate]) -> List[HackathonCandidate]:
    """Deduplicate without collapsing events that share a platform listing URL."""
    result, _ = _dedup_candidates_with_stats(candidates)
    return result


def _dedup_candidates_with_stats(
    candidates: List[HackathonCandidate],
) -> tuple[List[HackathonCandidate], Dict[str, int]]:
    """Deduplicate candidates and attribute each dropped row to its source."""
    seen_identities: set = set()
    result: List[HackathonCandidate] = []
    dropped: Dict[str, int] = defaultdict(int)

    for c in candidates:
        url = c.source_url or ""
        norm_url = _normalize_url(url)
        norm_title = _normalize_title_light(c.title or "")
        source_key = _candidate_source_key(c)

        if c.platform_id:
            identity = ("platform", source_key, c.platform_id)
        elif norm_url and not _is_listing_url(url):
            identity = ("url", norm_url)
        elif norm_title:
            # Listing pages legitimately contain many events under one URL.
            # The date disambiguates recurring editions with the same title.
            identity = (
                "listing_event",
                source_key,
                norm_title,
                (c.event_start or c.signup_deadline or c.event_end or "")[:10],
            )
        else:
            identity = ("listing_url", source_key, norm_url)

        if identity in seen_identities:
            dropped[source_key] += 1
            continue
        seen_identities.add(identity)
        result.append(c)

    return result, dict(dropped)


def _limit_candidates_round_robin(
    candidates: List[HackathonCandidate], limit: int
) -> tuple[List[HackathonCandidate], Dict[str, int]]:
    """Apply a global cap while preserving representation from every source."""
    if len(candidates) <= limit:
        return candidates, {}

    grouped: Dict[str, List[HackathonCandidate]] = {}
    source_order: List[str] = []
    for candidate in candidates:
        source_key = _candidate_source_key(candidate)
        if source_key not in grouped:
            grouped[source_key] = []
            source_order.append(source_key)
        grouped[source_key].append(candidate)

    selected: List[HackathonCandidate] = []
    positions = {source: 0 for source in source_order}
    while len(selected) < limit:
        progressed = False
        for source in source_order:
            pos = positions[source]
            if pos >= len(grouped[source]):
                continue
            selected.append(grouped[source][pos])
            positions[source] += 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break

    truncated = {
        source: len(items) - positions[source]
        for source, items in grouped.items()
        if len(items) > positions[source]
    }
    return selected, truncated


def _cross_source_dedup(candidates: List[HackathonCandidate]) -> List[HackathonCandidate]:
    """Deduplicate across sources: same event on multiple platforms."""
    result, _ = _cross_source_dedup_with_stats(candidates)
    return result


def _cross_source_dedup_with_stats(
    candidates: List[HackathonCandidate],
) -> tuple[List[HackathonCandidate], Dict[str, int]]:
    """Cross-source de-duplication with accurate source attribution."""
    result: List[HackathonCandidate] = []
    seen_titles: set = set()
    seen_platform_ids: set = set()
    dropped: Dict[str, int] = defaultdict(int)

    for c in candidates:
        source_key = _candidate_source_key(c)
        pid = getattr(c, "platform_id", None)
        if pid and pid in seen_platform_ids:
            dropped[source_key] += 1
            continue
        if pid:
            seen_platform_ids.add(pid)

        title = _normalize_title_light(c.title or "")
        if title and title in seen_titles:
            dropped[source_key] += 1
            continue
        if title:
            seen_titles.add(title)
        result.append(c)

    return result, dict(dropped)


def _merge_parsed(cand: HackathonCandidate, parsed: dict):
    """Merge parsed dict values into a HackathonCandidate."""
    if parsed.get("title"):
        cand.title = parsed["title"]
    if parsed.get("organizer"):
        cand.organizer = parsed["organizer"]
    if parsed.get("signup_deadline"):
        cand.signup_deadline = parsed["signup_deadline"]
    if parsed.get("event_start"):
        cand.event_start = parsed["event_start"]
    if parsed.get("event_end"):
        cand.event_end = parsed["event_end"]
    if parsed.get("mode"):
        cand.mode = parsed["mode"]
    if parsed.get("summary"):
        cand.summary = parsed["summary"]


def _parse_generic_detail(cand: HackathonCandidate, html: str, text: str) -> HackathonCandidate:
    """Parse a generic hackathon detail page (non-platform-specific)."""
    organizer = _extract_organizer(text) or cand.organizer
    summary = text[:200] if not cand.summary else cand.summary
    cand.organizer = organizer
    cand.summary = summary
    return cand


def _candidate_to_event(cand: HackathonCandidate) -> dict:
    """Convert HackathonCandidate to event dict for sync_events_to_db."""
    tags = list(cand.tags or ["黑客松"])
    if cand.mode == "online" and "线上" not in tags:
        tags.append("线上")
    elif cand.mode == "offline" and "线下" not in tags:
        tags.append("线下")


    return {
        "title": cand.title or "",
        "scope_type": "校外竞赛",
        "category": "黑客松",
        "summary": cand.summary or "",
        "signup_deadline": _to_iso8601_str(cand.signup_deadline),
        "event_time": _to_iso8601_str(cand.event_start),
        "target_major": "",
        "target_grade": "",
        "contest_level": "",
        "tags": json.dumps(tags, ensure_ascii=False),
        "policy_tags": "",
        "source_name": cand.source_name or _determine_source_name("", cand.source_url or ""),
        "source_url": cand.source_url or "",
        "authority_level": _normalize_authority(
            cand.source_authority,
            cand.source_url or "",
        ),
        "status": "待确认" if not cand.signup_deadline else "报名中",
        "organizer": cand.organizer or "",
        "original_text": cand.summary[:500] if cand.summary else "",
        "is_ministry_approved": False,
    }


def _to_iso8601_str(date_str: Optional[str]) -> Optional[str]:
    if not date_str:
        return None
    try:
        dt = datetime.strptime(date_str.strip()[:10], "%Y-%m-%d")
        return dt.replace(tzinfo=timezone(timedelta(hours=8))).isoformat()
    except ValueError:
        return date_str


def _determine_source_name(raw_source: str, url: str) -> str:
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


def _normalize_authority(value: Optional[str], url: str = "") -> str:
    """Normalize adapter authority values to the database's Chinese enum."""
    normalized = (value or "").strip().lower()
    authority_map = {
        "high": "高",
        "medium": "中",
        "low": "低",
        "高": "高",
        "中": "中",
        "低": "低",
    }
    return authority_map.get(normalized) or _determine_authority(url)
