"""竞赛/活动信息查询工具 - 从数据库查询竞赛和活动信息"""
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from langchain.tools import tool
from postgrest.exceptions import APIError
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)


def _get_client():
    """获取 Supabase 客户端"""
    from storage.database.supabase_client import get_supabase_client
    return get_supabase_client()


def _format_event(event: dict) -> dict:
    """格式化单条事件数据，计算剩余天数"""
    result = {
        "event_id": event.get("event_id"),
        "title": event.get("title"),
        "scope_type": event.get("scope_type"),
        "category": event.get("category"),
        "summary": event.get("summary"),
        "signup_deadline": event.get("signup_deadline"),
        "event_time": event.get("event_time"),
        "target_major": event.get("target_major"),
        "target_grade": event.get("target_grade"),
        "contest_level": event.get("contest_level"),
        "tags": event.get("tags"),
        "policy_tags": event.get("policy_tags"),
        "source_name": event.get("source_name"),
        "source_url": event.get("source_url"),
        "authority_level": event.get("authority_level"),
        "status": event.get("status"),
        "organizer": event.get("organizer"),
        "update_time": event.get("update_time"),
    }

    # 计算报名截止剩余天数
    deadline_str = event.get("signup_deadline")
    if deadline_str:
        try:
            if isinstance(deadline_str, str):
                deadline = datetime.fromisoformat(deadline_str.replace("Z", "+00:00"))
            else:
                deadline = deadline_str
            now = datetime.now(timezone.utc)
            delta = deadline - now
            result["days_remaining"] = max(0, delta.days)
        except Exception:
            result["days_remaining"] = None
    else:
        result["days_remaining"] = None

    return result


def _query_events(
    scope_type: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    target_major: Optional[str] = None,
    target_grade: Optional[str] = None,
    contest_level: Optional[str] = None,
    days_within: Optional[int] = None,
    limit: int = 50,
) -> list:
    """
    通用事件查询逻辑（普通函数，供 tool 调用）
    """
    ctx = request_context.get() or new_context(method="event_query")
    client = _get_client()

    select_fields = (
        "event_id,title,scope_type,category,summary,signup_deadline,"
        "event_time,target_major,target_grade,contest_level,tags,"
        "policy_tags,source_name,source_url,authority_level,status,"
        "organizer,update_time"
    )

    try:
        query = client.table("event_info").select(select_fields)

        if scope_type:
            query = query.eq("scope_type", scope_type)
        if category:
            query = query.eq("category", category)
        if status:
            query = query.eq("status", status)
        if contest_level:
            query = query.eq("contest_level", contest_level)
        if target_major:
            query = query.ilike("target_major", f"%{target_major}%")
        if target_grade:
            query = query.ilike("target_grade", f"%{target_grade}%")

        # DDL 时间筛选：报名截止在 N 天内
        if days_within is not None and days_within > 0:
            now = datetime.now(timezone.utc)
            future = now + timedelta(days=days_within)
            now_str = now.isoformat()
            future_str = future.isoformat()
            query = query.gte("signup_deadline", now_str).lte("signup_deadline", future_str)

        # 关键词搜索（标题模糊匹配）
        if keyword:
            query = query.ilike("title", f"%{keyword}%")

        query = query.order("signup_deadline", desc=False).limit(limit)
        response = query.execute()

        raw_data = response.data if response.data else []
        events = [e for e in raw_data if isinstance(e, dict)]
        return [_format_event(e) for e in events]

    except APIError as e:
        logger.error(f"查询事件失败: {e.message}")
        raise Exception(f"查询失败: {e.message}")


@tool
def query_events(
    scope_type: Optional[str] = None,
    category: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    target_major: Optional[str] = None,
    target_grade: Optional[str] = None,
    contest_level: Optional[str] = None,
    days_within: Optional[int] = None,
    limit: int = 50,
) -> str:
    """查询竞赛和活动信息。支持按类型、分类、状态、关键词、适合专业、适合年级、级别、截止日期范围等条件筛选。

    Args:
        scope_type: 范围类型筛选，可选值：'校外竞赛'、'校内竞赛'、'校内活动'，不传则查全部
        category: 细分类型筛选，如 '程序设计竞赛'、'数学建模'、'五育活动'、'创新创业'、'学术交流' 等
        status: 状态筛选，可选值：'报名中'、'即将截止'、'已截止'、'已结束'，不传默认查所有
        keyword: 关键词搜索（匹配标题）
        target_major: 适合专业筛选（模糊匹配），如 '计算机'、'数学'
        target_grade: 适合年级筛选（模糊匹配），如 '大一'、'大二'
        contest_level: 级别筛选，可选值：'国家级'、'省级'、'校级'、'院级'
        days_within: 查询报名截止在N天内的事件（DDL提醒场景），如 7 表示未来7天截止
        limit: 返回条数上限，默认50
    """
    try:
        events = _query_events(
            scope_type=scope_type,
            category=category,
            status=status,
            keyword=keyword,
            target_major=target_major,
            target_grade=target_grade,
            contest_level=contest_level,
            days_within=days_within,
            limit=limit,
        )
        if not events:
            return "未找到符合条件的竞赛/活动信息。"
        return json.dumps(events, ensure_ascii=False, default=str)
    except Exception as e:
        return f"查询出错: {str(e)}"


@tool
def query_event_detail(event_id: str) -> str:
    """根据事件ID查询单个竞赛/活动的详细信息。

    Args:
        event_id: 事件唯一编号，如 'EVT001'
    """
    try:
        ctx = request_context.get() or new_context(method="event_detail")
        client = _get_client()
        response = client.table("event_info").select("*").eq("event_id", event_id).maybe_single().execute()
        if response is None:
            return f"未找到编号为 {event_id} 的竞赛/活动。"
        event = response.data
        if not isinstance(event, dict):
            return f"未找到编号为 {event_id} 的竞赛/活动。"
        formatted = _format_event(event)
        # 包含原始文本
        formatted["original_text"] = event.get("original_text", "")
        return json.dumps(formatted, ensure_ascii=False, default=str)
    except APIError as e:
        logger.error(f"查询事件详情失败: {e.message}")
        return f"查询失败: {e.message}"
    except Exception as e:
        return f"查询出错: {str(e)}"


@tool
def get_deadline_reminders(days: int = 7) -> str:
    """获取未来N天内即将截止报名的竞赛和活动列表，按截止时间升序排列。

    Args:
        days: 查询未来多少天内截止的事件，默认7天
    """
    try:
        events = _query_events(
            status="报名中",
            days_within=days,
            limit=50,
        )
        # 也查即将截止状态的
        events2 = _query_events(
            status="即将截止",
            days_within=days,
            limit=50,
        )
        # 合并去重
        seen = set()
        merged = []
        for e in events + events2:
            eid = e.get("event_id")
            if eid and eid not in seen:
                seen.add(eid)
                merged.append(e)

        # 按剩余天数排序
        merged.sort(key=lambda x: x.get("days_remaining") if x.get("days_remaining") is not None else 9999)

        if not merged:
            return f"未来{days}天内没有即将截止的竞赛/活动。"

        result_lines = [f"未来{days}天内即将截止的竞赛/活动（共{len(merged)}条）：\n"]
        for i, e in enumerate(merged, 1):
            days_left = e.get("days_remaining", "未知")
            result_lines.append(
                f"{i}. 【{e['title']}】\n"
                f"   类型: {e['scope_type']} | 级别: {e.get('contest_level', '未知')}\n"
                f"   报名截止: {e.get('signup_deadline', '未知')}（剩余{days_left}天）\n"
                f"   比赛时间: {e.get('event_time', '未知')}\n"
                f"   适合专业: {e.get('target_major', '不限')}\n"
                f"   来源: {e.get('source_name', '未知')}"
            )
        return "\n".join(result_lines)
    except Exception as e:
        return f"查询出错: {str(e)}"
