"""
数据同步工作流
功能：
1. 从多个数据源（赛氪、教育部目录等）加载数据
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


def load_saikr_data() -> list:
    """加载赛氪爬虫数据"""
    filepath = os.path.join(ASSETS_DIR, "saikr_processed.json")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.info(f"Loaded {len(data)} events from saikr data")
        return data
    except Exception as e:
        logger.error(f"Failed to load saikr data: {e}")
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

            # 去重检查
            existing = _find_existing_event(supabase, title)

            if existing:
                # 已存在 -> 合并更新
                merged = _merge_event_data(existing, event)
                merged["status"] = _determine_status(merged.get("signup_deadline"))
                event_id = existing["event_id"]
                merged["event_id"] = event_id

                update_data = {k: v for k, v in merged.items() if k != "event_id"}
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

                insert_data = {k: v for k, v in event.items() if v is not None and k != "_ministry_info"}
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
    1. 加载赛氪数据 + 教育部目录
    2. AI字段补全
    3. 去重合并入库

    Returns:
        同步统计信息
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="full_sync")

    logger.info("=== Starting full data sync ===")

    # 1. 加载数据
    saikr_data = load_saikr_data()
    ministry_data = load_ministry_data()

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

    # 3. 赛氪数据AI补全
    logger.info(f"Enriching {len(saikr_data)} saikr events with AI...")
    saikr_enriched = enrich_batch(saikr_data, ctx=ctx)

    # 4. 合并两个数据源（教育部优先）
    all_events = ministry_enriched + saikr_enriched

    # 5. 同步到数据库
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
