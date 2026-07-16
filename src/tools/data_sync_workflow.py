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

from tools.event_enrichment import (
    enrich_single_event,
    enrich_batch,
    match_ministry_contest,
    _normalize_title,
    _edit_distance,
    _load_ministry_contests,
    ASSETS_DIR,
)
from tools.event_schema import event_db_payload, merge_event_data

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
    response = supabase.table("event_info").select("*").execute()
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
    return merge_event_data(existing, new_data)


def _determine_status(signup_deadline: Optional[str]) -> str:
    """根据报名截止时间确定状态"""
    if not signup_deadline:
        return "报名中"
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
        return "报名中"


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
        result = crawl_saikr_hot_contests(limit=30, sleep_seconds=0.8, fetch_details=True)
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
    删除数据库中报名截止时间已过的过期记录。

    Returns:
        被删除的记录数
    """
    from datetime import timezone as tz

    response = supabase.table("event_info").select("event_id,title,signup_deadline,status").execute()
    all_records = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

    deleted_count = 0
    now = datetime.now(tz.utc)

    for record in all_records:
        event_id = record.get("event_id")
        deadline_str = record.get("signup_deadline")
        if _is_expired(deadline_str):
            try:
                supabase.table("event_info").delete().eq("event_id", event_id).execute()
                deleted_count += 1
                logger.info(f"Deleted expired: {record.get('title', '')[:50]} (deadline={deadline_str})")
            except Exception as e:
                logger.error(f"Failed to delete expired event {event_id}: {e}")

    logger.info(f"Expired cleanup complete: {deleted_count}/{len(all_records)} records deleted")
    return deleted_count


def _refresh_all_statuses(supabase) -> int:
    """
    刷新所有记录的 status 字段，根据 signup_deadline 重新计算。
    解决数据中过期但状态未更新的问题。

    Returns:
        被更新的记录数
    """
    from datetime import timezone as tz

    response = supabase.table("event_info").select("event_id,title,signup_deadline,status").execute()
    all_records = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

    updated_count = 0
    now = datetime.now(tz.utc)

    for record in all_records:
        event_id = record.get("event_id")
        current_status = record.get("status")
        deadline_str = record.get("signup_deadline")

        new_status = _determine_status(deadline_str)

        if new_status != current_status:
            try:
                supabase.table("event_info").update({
                    "status": new_status,
                    "update_time": now.isoformat(),
                }).eq("event_id", event_id).execute()
                updated_count += 1
                logger.info(f"Status refreshed: {record.get('title', '')[:40]} [{current_status}] -> [{new_status}]")
            except Exception as e:
                logger.error(f"Failed to refresh status for {event_id}: {e}")

    logger.info(f"Status refresh complete: {updated_count}/{len(all_records)} records updated")
    return updated_count


def sync_events_to_db(enriched_events: list, ctx=None) -> dict:
    """
    将补全后的事件数据同步到数据库（去重合并）

    Returns:
        {"added": int, "updated": int, "skipped": int, "errors": int}
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="sync_events")

    supabase = _get_supabase()
    stats = {"added": 0, "updated": 0, "skipped": 0, "errors": 0, "details": []}

    for event in enriched_events:
        try:
            title = event.get("title", "")
            if not title:
                stats["skipped"] += 1
                continue

            # 入库前过滤：报名截止时间已过的直接丢弃
            if _is_expired(event.get("signup_deadline")):
                logger.info(f"Skipping expired event: {title[:50]} (deadline={event.get('signup_deadline')})")
                stats["skipped"] += 1
                stats["details"].append({"action": "skipped_expired", "title": title[:50]})
                continue

            # 入库前校验：event_time 不能早于 signup_deadline
            event = _validate_timeline(event)

            # 去重检查
            existing = _find_existing_event(supabase, title)

            if existing:
                # 已存在 -> 合并更新
                merged = _merge_event_data(existing, event)
                # 合并后再次检查是否过期（新 deadline 可能使旧记录过期）
                if _is_expired(merged.get("signup_deadline")):
                    logger.info(f"Skipping update for expired event: {title[:50]}")
                    stats["skipped"] += 1
                    stats["details"].append({"action": "skipped_expired", "title": title[:50]})
                    continue
                merged["status"] = _determine_status(merged.get("signup_deadline"))
                event_id = existing["event_id"]
                merged["event_id"] = event_id

                update_data = event_db_payload(merged)
                update_data.pop("event_id", None)
                supabase.table("event_info").update(update_data).eq("event_id", event_id).execute()
                stats["updated"] += 1
                stats["details"].append({"action": "updated", "event_id": event_id, "title": title[:50]})
            else:
                # 新事件 -> 插入
                event_id = _generate_event_id()
                event["event_id"] = event_id
                event["status"] = _determine_status(event.get("signup_deadline"))
                event["update_time"] = datetime.now().isoformat()

                # 确保 tags/policy_tags 是 JSON 字符串
                for field in ("tags", "policy_tags"):
                    val = event.get(field)
                    if isinstance(val, list):
                        event[field] = json.dumps(val, ensure_ascii=False)

                insert_data = event_db_payload(event)
                supabase.table("event_info").insert(insert_data).execute()
                stats["added"] += 1
                stats["details"].append({"action": "added", "event_id": event_id, "title": title[:50]})

        except Exception as e:
            logger.error(f"Failed to sync event '{event.get('title', 'unknown')[:30]}': {e}")
            stats["errors"] += 1
            stats["details"].append({"action": "error", "title": event.get("title", "unknown")[:50], "error": str(e)})

    logger.info(f"Sync complete: added={stats['added']}, updated={stats['updated']}, skipped={stats['skipped']}, errors={stats['errors']}")
    return stats


def run_full_sync(ctx=None) -> dict:
    """
    执行完整的数据同步流程：
    1. 加载赛氪数据 + 教育部目录 + 微信公众号
    2. AI字段补全
    3. 去重合并入库

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

    # 2. 教育部目录数据直接标记（不需要AI补全）
    ministry_enriched = []
    for item in ministry_data:
        info = item.get("_ministry_info", {})
        enriched = {
            "title": item["title"],
            "scope_type": "校外竞赛",
            "category": info.get("category", "其他"),
            "summary": f"{info['name']}由{info['organizer']}主办，是教育部认可的{info['level']}竞赛。",
            "contest_level": info.get("level", "国家级"),
            "target_major": "全校各专业",
            "target_grade": "大一,大二,大三",
            "tags": json.dumps(["教育部目录"], ensure_ascii=False),
            "policy_tags": json.dumps(["保研明确相关", "综测加分"], ensure_ascii=False),
            "organizer": info.get("organizer", ""),
            "source_name": "教育部竞赛目录",
            "source_url": "",
            "authority_level": "高",
            "status": "报名中",
            "is_ministry_approved": True,
            "original_text": item.get("detail_text", ""),
        }
        ministry_enriched.append(enriched)

    # 3. 赛氪数据AI补全（带超时保护和降级）
    saikr_enriched = []
    if saikr_data:
        logger.info(f"Enriching {len(saikr_data)} saikr events with AI...")
        try:
            saikr_enriched = enrich_batch(saikr_data, ctx=ctx)
            logger.info(f"AI enrichment completed: {len(saikr_enriched)} records")
        except Exception as e:
            logger.warning(f"AI enrichment failed ({e}), falling back to rule-based enrichment")
            # 降级：用规则补全代替LLM
            from tools.event_enrichment import _rule_based_fallback
            saikr_enriched = []
            for item in saikr_data:
                fallback = _rule_based_fallback(item.get("detail_text", ""), item.get("title", ""))
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
        from tools.wechat_data_source import enrich_wechat_events
        logger.info("Fetching and enriching WeChat events...")
        wechat_enriched = enrich_wechat_events(hours=0, ctx=ctx)  # hours=0 不过滤时间，全量抓取
        logger.info(f"Got {len(wechat_enriched)} WeChat events")
    except Exception as e:
        logger.error(f"WeChat sync failed (non-fatal): {e}")

    # 5. 合并三个数据源（教育部优先）
    all_events = ministry_enriched + saikr_enriched + wechat_enriched

    # 6. 同步到数据库
    logger.info(f"Syncing {len(all_events)} events to database...")
    stats = sync_events_to_db(all_events, ctx=ctx)

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
