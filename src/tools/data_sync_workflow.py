"""
数据同步工作流
功能：
1. 从多个数据源（赛氪、教育部目录、微信公众号）加载数据
2. AI字段补全
3. 去重合并（标题标准化 + 编辑距离 > 0.85 视为同一项）
4. 批量入库
5. 定时同步机制（可复用，支持扩展新数据源）
"""
import json
import os
import re
import uuid
import logging
from datetime import datetime
from typing import Optional

from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from sqlalchemy.orm import Session
from storage.database.db import get_session

from tools.event_enrichment import (
    enrich_single_event,
    enrich_batch,
    match_ministry_contest,
    _normalize_title,
    _edit_distance,
    _load_ministry_contests,
    ASSETS_DIR,
)

logger = logging.getLogger(__name__)

# Supabase client (lazy init)
_supabase_client = None


def _get_supabase():
    """懒加载 Supabase client"""
    global _supabase_client
    if _supabase_client is None:
        from storage.database.supabase_client import get_supabase_client
        _supabase_client = get_supabase_client()
    return _supabase_client


def _generate_event_id() -> str:
    """生成唯一事件ID"""
    return f"EVT-{uuid.uuid4().hex[:8].upper()}"


def _find_existing_event(supabase, title: str, threshold: float = 0.85) -> Optional[dict]:
    """
    在数据库中查找相似事件（去重检查）
    使用标题标准化 + 编辑距离匹配
    """
    norm_title = _normalize_title(title)

    # 先查所有事件标题（限制数量避免性能问题）
    response = supabase.table("event_info").select("event_id,title").execute()
    existing = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

    for item in existing:
        existing_norm = _normalize_title(item.get("title", ""))
        if _edit_distance(norm_title, existing_norm) >= threshold:
            return item
    return None


def _merge_event_data(existing: dict, new_data: dict) -> dict:
    """
    合并事件数据：保留权威度高的为主记录
    规则：教育部目录 > 高可信度 > 中可信度 > 低可信度
    """
    merged = {**existing}

    # 如果新数据有教育部认证，以新数据为主
    if new_data.get("is_ministry_approved"):
        for key, value in new_data.items():
            if value is not None and value != "" and value != "null":
                merged[key] = value
    else:
        # 只更新空字段
        for key, value in new_data.items():
            if key in ("event_id",):
                continue
            existing_val = merged.get(key)
            if (existing_val is None or existing_val == "" or existing_val == "null") and value is not None:
                merged[key] = value

    merged["update_time"] = datetime.now().isoformat()
    return merged


def _determine_status(signup_deadline: Optional[str]) -> str:
    """
    根据报名截止时间确定状态。
    无有效 DDL 时返回'暂无本届信息'，避免把目录/旧信息误标为可报名。
    """
    if not signup_deadline:
        return "暂无本届信息"
    try:
        from datetime import timezone
        deadline = datetime.fromisoformat(signup_deadline.replace("+08:00", "+08:00"))
        now = datetime.now(timezone.utc)
        diff = (deadline - now).days
        if diff < 0:
            return "已截止"
        elif diff <= 7:
            return "即将截止"
        else:
            return "报名中"
    except Exception:
        return "待确认"


def _is_expired(signup_deadline: Optional[str]) -> bool:
    """判断报名截止时间是否已过期。无 deadline 的视为未过期（保留）。"""
    if not signup_deadline:
        return False
    try:
        from datetime import timezone
        deadline = datetime.fromisoformat(signup_deadline.replace("+08:00", "+08:00"))
        return deadline < datetime.now(timezone.utc)
    except Exception:
        return False


def _validate_timeline(event: dict) -> dict:
    """
    交叉校验 event_time 和 signup_deadline 的时间线逻辑。
    规则：event_time 必须 >= signup_deadline（比赛在报名截止之后举行）。
    如果矛盾（event_time < signup_deadline），清空 event_time，因为无法判断哪个字段正确。
    返回修正后的 event dict。
    """
    deadline = event.get("signup_deadline")
    event_time = event.get("event_time")
    if not deadline or not event_time:
        return event
    try:
        from datetime import timezone
        dl = datetime.fromisoformat(deadline.replace("+08:00", "+08:00"))
        et = datetime.fromisoformat(event_time.replace("+08:00", "+08:00"))
        if et < dl:
            logger.warning(
                f"Timeline conflict: event_time({event_time}) < signup_deadline({deadline}) "
                f"for '{event.get('title', '')[:40]}' — clearing event_time"
            )
            event["event_time"] = None
    except Exception:
        pass
    return event


def load_saikr_data() -> list:
    """
    从赛氪在线爬取热门竞赛数据。
    如果爬取失败，返回空列表（不中断整体同步流程）。
    """
    try:
        from tools.saikr_crawler import crawl_saikr_hot_contests
        logger.info("Crawling saikr hot contests online...")
        result = crawl_saikr_hot_contests(limit=50, sleep_seconds=0.8, fetch_details=True)
        records = result.get("records", [])
        if not records:
            logger.warning(f"Saikr crawler returned {len(records)} records (empty)")
            return []
        # 将爬虫输出标准化为 data_sync_workflow 期望的格式
        events = []
        for rec in records:
            events.append({
                "title": rec.get("title", ""),
                "detail_text": rec.get("detail_text", ""),
                "url": rec.get("detail_url", "") or rec.get("url", ""),
                "source": "赛氪",
                "source_url": rec.get("detail_url", "") or rec.get("url", ""),
                "organizer": rec.get("organizer", ""),
            })
        logger.info(f"Crawled {len(events)} events from saikr.com")
        return events
    except Exception as e:
        logger.error(f"Failed to crawl saikr data (non-fatal): {e}")
        return []


def load_ministry_data() -> list:
    """加载教育部目录数据并转换为标准格式"""
    ministry_list = _load_ministry_contests()
    events = []
    for item in ministry_list:
        events.append({
            "title": item["name"],
            "detail_text": f"{item['name']}由{item['organizer']}主办，属于教育部认可的{item['level']}竞赛，类别为{item['category']}。",
            "url": "",
            "organizer": item["organizer"],
            "_ministry_info": item,
        })
    logger.info(f"Loaded {len(events)} events from ministry catalog")
    return events


def _cleanup_expired(supabase) -> int:
    """
    删除数据库中报名截止时间已过的过期 event_edition 记录。

    Returns:
        被删除的记录数
    """
    from datetime import timezone as tz
    from sqlalchemy import select
    from storage.database.db import get_engine
    from storage.database.catalog_models import EventEdition

    engine = get_engine()
    now = datetime.now(tz.utc)
    deleted_count = 0

    with Session(engine) as session:
        expired = session.execute(
            select(EventEdition).where(
                EventEdition.signup_deadline.isnot(None),
                EventEdition.signup_deadline < now,
            )
        ).scalars().all()

        for edition in expired:
            title = edition.title
            session.delete(edition)
            deleted_count += 1
            logger.info(f"Deleted expired: {title[:50]} (deadline={edition.signup_deadline})")

        session.commit()

    logger.info(f"Expired cleanup complete: {deleted_count} records deleted")
    return deleted_count


def _refresh_all_statuses(supabase) -> int:
    """
    刷新所有 event_edition 的 status 字段，根据 signup_deadline 重新计算。
    无有效截止时间的记录标记为 '暂无本届信息'。

    Returns:
        被更新的记录数
    """
    from datetime import timezone as tz
    from sqlalchemy import select
    from storage.database.db import get_engine
    from storage.database.catalog_models import EventEdition

    engine = get_engine()
    updated_count = 0
    now = datetime.now(tz.utc)

    with Session(engine) as session:
        editions = session.execute(select(EventEdition)).scalars().all()

        for edition in editions:
            current_status = edition.status
            new_status = _determine_status(
                edition.signup_deadline.isoformat() if edition.signup_deadline else None
            )
            if new_status != current_status:
                edition.status = new_status
                edition.updated_at = now
                updated_count += 1
                logger.info(f"Status refreshed: {edition.title[:40]} [{current_status}] -> [{new_status}]")

        session.commit()

    logger.info(f"Status refresh complete: {updated_count}/{len(editions)} records updated")
    return updated_count


def sync_events_to_db(enriched_events: list, ctx=None) -> dict:
    """
    将补全后的事件数据同步到数据库（基于 catalog + edition + evidence 模型）。

    Returns:
        {"added": int, "updated": int, "skipped": int, "errors": int}
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="sync_events")

    from sqlalchemy.orm import Session
    from storage.database.db import get_engine
    from tools.catalog_service import merge_event, _normalize_title

    engine = get_engine()
    stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0, "details": []}

    for event in enriched_events:
        try:
            title = event.get("title", "")
            if not title:
                stats["skipped"] += 1
                continue

            # Filter obvious ads / training before merging
            if _is_likely_ad_or_training(title):
                logger.info(f"Skipping ad/training content: {title[:60]}")
                stats["skipped"] += 1
                stats["details"].append({"action": "skipped_ad", "title": title[:50]})
                continue

            # Skip expired events (no valid current edition)
            if _is_expired(event.get("signup_deadline")):
                logger.info(f"Skipping expired event: {title[:50]} (deadline={event.get('signup_deadline')})")
                stats["skipped"] += 1
                stats["details"].append({"action": "skipped_expired", "title": title[:50]})
                continue

            # Timeline validation
            event = _validate_timeline(event)

            with Session(engine) as session:
                merge_event(
                    session,
                    event,
                    extraction_method=event.get("extraction_method", "sync"),
                    confidence=event.get("confidence", "medium"),
                )
                # Determine added vs updated is handled inside merge_event; we approximate here
                stats["added"] += 1
                stats["details"].append({"action": "merged", "title": title[:50]})

        except Exception as e:
            logger.error(f"Failed to sync event '{event.get('title', 'unknown')[:30]}': {e}")
            stats["errors"] += 1
            stats["details"].append({"action": "error", "title": event.get("title", "unknown")[:50], "error": str(e)})

    logger.info(f"Sync complete: added/merged={stats['added']}, skipped={stats['skipped']}, errors={stats['errors']}")
    return stats


def _is_likely_ad_or_training(title: str) -> bool:
    """Heuristic filter to drop ads, training courses, and planning promotions."""
    if not title:
        return False
    blacklist = [
        "培训", "课程", "辅导班", "保研规划", "保研咨询", "保研定位", "留学", "雅思", "托福", "GRE",
        "考研", "考公", "考编", "教师资格证", "注册会计师", "付费", "会员", "优惠",
        "早鸟", "限时", "团购", "报名咨询", "扫码添加", "免费领取",
    ]
    # 去掉中英文括号、空格、书名号等干扰字符后再匹配，避免"保研）定位"漏过
    normalized = re.sub(r"[\s（）()【】\[\]《》<>「」]", "", title.lower())
    return any(k in normalized for k in blacklist)


def run_full_sync(ctx=None, skip_enrichment: bool = False) -> dict:
    """
    执行完整的数据同步流程：
    1. 加载赛氪数据 + 教育部目录 + 微信公众号
    2. AI字段补全（可被 skip_enrichment 跳过）
    3. 去重合并入库

    Args:
        ctx: 请求上下文
        skip_enrichment: 是否跳过 LLM 字段补全。默认 False。
                         为 True 时仅做规则级 fallback，用于快速同步或 LLM 不可用时。

    Returns:
        同步统计信息
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="full_sync")

    logger.info("=== Starting full data sync ===")

    # 0. 清理过期记录 + 刷新状态
    supabase = _get_supabase()
    try:
        deleted = _cleanup_expired(supabase)
        logger.info(f"Pre-sync expired cleanup: {deleted} records deleted")
    except Exception as e:
        logger.warning(f"Expired cleanup failed (non-fatal): {e}")
    try:
        refreshed = _refresh_all_statuses(supabase)
        logger.info(f"Pre-sync status refresh: {refreshed} records updated")
    except Exception as e:
        logger.warning(f"Status refresh failed (non-fatal): {e}")

    # 1. 加载数据
    saikr_data = load_saikr_data()
    ministry_data = load_ministry_data()

    if not saikr_data:
        logger.warning("Saikr data is empty — will skip saikr enrichment but continue with other sources")

    # 2. 教育部目录数据 → 仅写入 competition_catalog，不创建 edition
    from tools.catalog_service import merge_catalog
    ministry_catalog_count = 0
    db_session = get_session()
    try:
        for item in ministry_data:
            info = item.get("_ministry_info", {})
            catalog = {
                "normalized_title": _normalize_title(item["title"]),
                "original_title": item["title"],
                "organizer": info.get("organizer", ""),
                "category": info.get("category", "其他"),
                "contest_level": info.get("level", "国家级"),
                "authority_level": "高",
                "policy_tags": json.dumps(["保研明确相关", "综测加分"], ensure_ascii=False),
                "scope_type": "校外竞赛",
                "source_name": "教育部竞赛目录",
                "source_url": item.get("source_url", ""),
                "is_ministry_approved": True,
                "status": "active",
            }
            try:
                merge_catalog(db_session, catalog)
                ministry_catalog_count += 1
            except Exception as e:
                logger.warning(f"Failed to merge ministry catalog {item.get('title')}: {e}")
        logger.info(f"Merged {ministry_catalog_count} ministry catalogs")
    finally:
        db_session.close()

    # 3. 赛氪数据AI补全（带超时保护和降级）
    saikr_enriched = []
    if skip_enrichment:
        logger.info("skip_enrichment=True, using rule-based fallback for saikr data")
        from tools.event_enrichment import _rule_based_fallback, match_ministry_contest
        for item in saikr_data or []:
            fallback = _rule_based_fallback(item, match_ministry_contest(item.get("title", "")))
            fallback["source_url"] = item.get("source_url", "") or item.get("detail_url", "")
            fallback["source_name"] = "赛氪"
            fallback["title"] = item.get("title", "")
            saikr_enriched.append(fallback)
    elif saikr_data:
        logger.info(f"Enriching {len(saikr_data)} saikr events with AI...")
        try:
            saikr_enriched = enrich_batch(saikr_data, ctx=ctx)
            logger.info(f"AI enrichment completed: {len(saikr_enriched)} records")
        except Exception as e:
            logger.warning(f"AI enrichment failed ({e}), falling back to rule-based enrichment")
            # 降级：用规则补全代替LLM
            from tools.event_enrichment import _rule_based_fallback, match_ministry_contest
            saikr_enriched = []
            for item in saikr_data:
                fallback = _rule_based_fallback(item, match_ministry_contest(item.get("title", "")))
                fallback["source_url"] = item.get("source_url", "") or item.get("detail_url", "")
                fallback["source_name"] = "赛氪"
                fallback["title"] = item.get("title", "")
                saikr_enriched.append(fallback)
            logger.info(f"Rule-based fallback completed: {len(saikr_enriched)} records")
    else:
        logger.warning("Skipping saikr enrichment (no data crawled)")

    # 4. 微信公众号数据抓取 + AI补全
    wechat_enriched = []
    try:
        if skip_enrichment:
            logger.info("skip_enrichment=True, skipping WeChat AI enrichment")
        else:
            from tools.wechat_data_source import enrich_wechat_events
            logger.info("Fetching and enriching WeChat events...")
            wechat_enriched = enrich_wechat_events(hours=0, ctx=ctx)  # hours=0 不过滤时间，全量抓取
            logger.info(f"Got {len(wechat_enriched)} WeChat events")
    except Exception as e:
        logger.error(f"WeChat sync failed (non-fatal): {e}")

    # 5. 合并赛氪 + 微信数据（教育部目录已在 catalog 层处理）
    all_events = saikr_enriched + wechat_enriched

    # 6. 同步到数据库
    logger.info(f"Syncing {len(all_events)} events to database...")
    db_stats = sync_events_to_db(all_events, ctx=ctx)

    stats = {
        "ministry_catalogs": ministry_catalog_count,
        **db_stats,
    }
    logger.info(f"=== Full sync complete: {stats} ===")
    return stats


def run_incremental_sync(new_raw_events: list, ctx=None) -> dict:
    """
    增量同步：仅处理新增的原始数据

    Args:
        new_raw_events: 新增的原始数据列表（赛氪格式）
        ctx: 请求上下文

    Returns:
        同步统计信息
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="incremental_sync")

    logger.info(f"=== Starting incremental sync with {len(new_raw_events)} new events ===")

    # AI补全
    enriched = enrich_batch(new_raw_events, ctx=ctx)

    # 同步入库
    stats = sync_events_to_db(enriched, ctx=ctx)

    logger.info(f"=== Incremental sync complete: {stats} ===")
    return stats


def run_wechat_sync(hours: int = 6, ctx=None) -> dict:
    """
    微信公众号增量同步

    Args:
        hours: 抓取过去 N 小时的文章
        ctx: 请求上下文

    Returns:
        同步统计信息
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="wechat_sync")

    logger.info(f"=== Starting WeChat sync (last {hours}h) ===")

    try:
        from tools.wechat_data_source import fetch_wechat_events
        wechat_events = fetch_wechat_events(hours=hours, ctx=ctx)

        if not wechat_events:
            logger.info("No new WeChat events found")
            return {"added": 0, "updated": 0, "skipped": 0, "errors": 0}

        # AI补全
        enriched = enrich_batch(wechat_events, ctx=ctx)

        # 同步入库
        stats = sync_events_to_db(enriched, ctx=ctx)
        logger.info(f"=== WeChat sync complete: {stats} ===")
        return stats

    except Exception as e:
        logger.error(f"WeChat sync failed: {e}", exc_info=True)
        return {"added": 0, "updated": 0, "skipped": 0, "errors": 1, "error_msg": str(e)}
