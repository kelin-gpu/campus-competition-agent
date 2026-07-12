"""通知解析工具 - 从链接或文本中提取竞赛/活动结构化信息并存入数据库"""
import json
import uuid
import logging
from datetime import datetime, timezone
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


def _fetch_url_content(url: str) -> str:
    """获取 URL 页面内容（普通函数）"""
    from coze_coding_dev_sdk.fetch import FetchClient
    ctx = request_context.get() or new_context(method="fetch_url")
    client = FetchClient(ctx=ctx)
    response = client.fetch(url=url)
    if response.status_code != 0:
        raise Exception(f"获取页面失败: {response.status_message}")
    text_parts = []
    for item in response.content:
        if item.type == "text" and item.text:
            text_parts.append(item.text)
    return "\n".join(text_parts)


def _extract_structured_info(text: str, source_url: Optional[str] = None) -> dict:
    """
    从文本中提取结构化信息（普通函数，供 tool 调用）。
    使用简单的规则解析 + 关键词匹配。
    """
    info = {
        "event_id": f"EVT{uuid.uuid4().hex[:6].upper()}",
        "title": "",
        "scope_type": "校内活动",
        "category": "",
        "summary": "",
        "signup_deadline": None,
        "event_time": None,
        "target_major": "",
        "target_grade": "",
        "contest_level": "校级",
        "tags": "[]",
        "policy_tags": "[]",
        "source_name": "",
        "source_url": source_url or "",
        "authority_level": "中",
        "status": "报名中",
        "organizer": "",
        "update_time": datetime.now(timezone.utc).isoformat(),
        "original_text": text[:2000] if text else "",
    }

    lines = text.strip().split("\n") if text else []

    # 提取标题（通常第一行或含"关于"/"通知"的行）
    for line in lines:
        line = line.strip()
        if line and len(line) > 5:
            info["title"] = line[:200]
            break

    # 判断范围类型
    if any(kw in text for kw in ["国际", "全国", "全国大学生", "中国"]):
        info["scope_type"] = "校外竞赛"
        info["contest_level"] = "国家级"
    elif any(kw in text for kw in ["省级", "省"]):
        info["scope_type"] = "校外竞赛"
        info["contest_level"] = "省级"
    elif any(kw in text for kw in ["学院", "书院", "院系"]):
        info["scope_type"] = "校内活动"
        info["contest_level"] = "院级"
    elif any(kw in text for kw in ["竞赛", "比赛", "大赛", "挑战"]):
        info["scope_type"] = "校内竞赛"
        info["contest_level"] = "校级"

    # 判断分类
    if any(kw in text for kw in ["程序设计", "编程", "算法", "ACM", "ICPC"]):
        info["category"] = "程序设计竞赛"
    elif any(kw in text for kw in ["数学建模", "建模"]):
        info["category"] = "数学建模"
    elif any(kw in text for kw in ["创新创业", "创业", "三创", "挑战杯"]):
        info["category"] = "创新创业"
    elif any(kw in text for kw in ["五育", "体育", "美育", "德育", "劳育"]):
        info["category"] = "五育活动"
    elif any(kw in text for kw in ["讲座", "沙龙", "分享"]):
        info["category"] = "学术讲座"
    elif any(kw in text for kw in ["人工智能", "AI", "机器学习"]):
        info["category"] = "人工智能"
    else:
        info["category"] = "其他"

    # 提取简介（取前200字）
    if text:
        clean_text = text.strip()
        info["summary"] = clean_text[:100] if len(clean_text) > 100 else clean_text

    # 提取主办方
    for kw in ["主办", "组织", "承办", "举办"]:
        idx = text.find(kw)
        if idx >= 0:
            segment = text[idx:idx + 50]
            for sep in ["：", ":", "是", "为"]:
                if sep in segment:
                    org = segment.split(sep, 1)[1].strip()[:100]
                    org = org.split("\n")[0].split("，")[0].split("。")[0]
                    info["organizer"] = org
                    break

    # 提取来源名称
    if source_url:
        if "nju.edu.cn" in source_url:
            info["source_name"] = "南京大学官网"
            info["authority_level"] = "高"
        else:
            info["source_name"] = "外部来源"
            info["authority_level"] = "中"

    # 标签
    tags = []
    if any(kw in text for kw in ["组队", "团队", "三人"]):
        tags.append("需要组队")
    if any(kw in text for kw in ["个人", "独立"]):
        tags.append("个人赛")
    if any(kw in text for kw in ["保研"]):
        tags.append("保研相关")
    if any(kw in text for kw in ["五育"]):
        tags.append("五育学分")
    info["tags"] = json.dumps(tags, ensure_ascii=False)

    policy_tags = []
    if any(kw in text for kw in ["保研"]):
        policy_tags.append("保研可能相关")
    if any(kw in text for kw in ["综测", "综合测评"]):
        policy_tags.append("综测加分")
    if any(kw in text for kw in ["五育"]):
        policy_tags.append("五育明确相关")
    info["policy_tags"] = json.dumps(policy_tags, ensure_ascii=False)

    return info


def _insert_event(event_data: dict) -> str:
    """插入事件到数据库（普通函数）"""
    ctx = request_context.get() or new_context(method="insert_event")
    client = _get_client()
    try:
        response = client.table("event_info").insert(event_data).execute()
        data = response.data
        if data and isinstance(data, list) and len(data) > 0:
            first = data[0]
            if isinstance(first, dict):
                return str(first.get("event_id", "unknown"))
        return "unknown"
    except APIError as e:
        logger.error(f"插入事件失败: {e.message}")
        raise Exception(f"插入失败: {e.message}")


@tool
def parse_and_save_notification(input_text: str, source_url: Optional[str] = None) -> str:
    """解析竞赛/活动通知（链接或文本），提取结构化信息并存入数据库。

    当用户提供一个竞赛/活动链接或通知文本时，使用此工具自动提取标题、类型、简介、
    报名时间、比赛时间、适合对象、主办方、级别、标签等字段，并存入数据库。

    Args:
        input_text: 通知文本内容，或者一个URL链接。如果是URL，会自动抓取页面内容后解析。
        source_url: 来源链接（可选）。如果 input_text 是纯文本但你知道来源URL，可以传入。
    """
    try:
        ctx = request_context.get() or new_context(method="parse_notification")

        # 判断输入是 URL 还是文本
        text_content = input_text
        actual_url = source_url

        if input_text.strip().startswith(("http://", "https://")):
            actual_url = input_text.strip()
            try:
                text_content = _fetch_url_content(actual_url)
            except Exception as e:
                logger.warning(f"抓取URL失败: {e}，将直接解析URL文本")
                text_content = input_text

        # 提取结构化信息
        event_data = _extract_structured_info(text_content, actual_url)

        # 去重检查：按标题模糊匹配
        client = _get_client()
        try:
            existing = client.table("event_info").select("event_id,title").ilike(
                "title", f"%{event_data['title'][:20]}%"
            ).limit(5).execute()
            existing_data = existing.data
            if existing_data and isinstance(existing_data, list) and len(existing_data) > 0:
                first_item = existing_data[0]
                dup_title = str(first_item.get("title", "")) if isinstance(first_item, dict) else ""
                dup_id = str(first_item.get("event_id", "")) if isinstance(first_item, dict) else ""
                return json.dumps({
                    "status": "duplicate",
                    "message": f"数据库中已存在相似标题的活动：{dup_title}（ID: {dup_id}）。如需更新请使用更新功能。",
                    "existing": existing_data,
                }, ensure_ascii=False)
        except APIError:
            pass  # 去重检查失败不影响主流程

        # 插入数据库
        event_id = _insert_event(event_data)

        result = {
            "status": "success",
            "message": f"已成功解析并保存竞赛/活动信息，编号: {event_id}",
            "event_id": event_id,
            "parsed_data": {
                "title": event_data["title"],
                "scope_type": event_data["scope_type"],
                "category": event_data["category"],
                "summary": event_data["summary"],
                "contest_level": event_data["contest_level"],
                "organizer": event_data["organizer"],
                "source_name": event_data["source_name"],
                "authority_level": event_data["authority_level"],
            }
        }
        return json.dumps(result, ensure_ascii=False, default=str)

    except Exception as e:
        return f"解析通知出错: {str(e)}"
