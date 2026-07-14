"""
知识库管理模块
功能：
1. 将 event_info 数据同步到向量知识库（用于 RAG 全文检索）
2. 提供知识库语义搜索能力
3. 入库时自动同步到知识库
"""
import json
import logging
from typing import Optional

from coze_coding_dev_sdk import KnowledgeClient, Config, KnowledgeDocument, DataSourceType, ChunkConfig
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context, Context
from langchain.tools import tool

logger = logging.getLogger(__name__)

# 知识库数据集名称
KB_DATASET_NAME = "event_knowledge"

# 知识库 client (lazy init)
_kb_client = None


def _get_kb_client(ctx=None) -> KnowledgeClient:
    """获取或创建知识库 client"""
    global _kb_client
    if _kb_client is None:
        config = Config()
        _kb_client = KnowledgeClient(config=config, ctx=ctx)
    return _kb_client


def _event_to_document_text(event: dict) -> str:
    """
    将 event_info 记录转换为知识库文档文本
    包含标题、正文、来源等完整信息，便于语义检索
    """
    title = event.get("title", "")
    original_text = event.get("original_text", "") or event.get("summary", "")
    source_name = event.get("source_name", "")
    source_url = event.get("source_url", "")
    scope_type = event.get("scope_type", "")
    category = event.get("category", "")
    contest_level = event.get("contest_level", "")
    organizer = event.get("organizer", "")
    signup_deadline = str(event.get("signup_deadline", ""))
    event_time = str(event.get("event_time", ""))
    target_major = event.get("target_major", "")
    target_grade = event.get("target_grade", "")
    tags = event.get("tags", "")
    policy_tags = event.get("policy_tags", "")
    is_ministry = event.get("is_ministry_approved", False)

    # 构建结构化文档文本
    parts = [
        f"【{title}】",
        f"类型：{scope_type} | 分类：{category} | 级别：{contest_level}",
        f"主办方：{organizer}",
        f"报名截止：{signup_deadline} | 活动时间：{event_time}",
        f"适合专业：{target_major} | 适合年级：{target_grade}",
    ]

    if tags:
        parts.append(f"标签：{tags}")
    if policy_tags:
        parts.append(f"政策相关性：{policy_tags}")
    if is_ministry:
        parts.append("【教育部竞赛目录认可】")

    parts.append(f"简介：{event.get('summary', '')}")

    if original_text and original_text != event.get("summary", ""):
        parts.append(f"详情：{original_text}")

    parts.append(f"来源：{source_name} | 链接：{source_url}")

    return "\n".join(parts)


def sync_event_to_kb(event: dict, ctx=None) -> bool:
    """
    将单条 event_info 同步到知识库

    Args:
        event: event_info 字典
        ctx: 请求上下文

    Returns:
        True if success
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="sync_event_to_kb")

    try:
        text = _event_to_document_text(event)
        if not text.strip():
            logger.warning(f"Empty text for event: {event.get('title', 'unknown')}")
            return False

        client = _get_kb_client(ctx)
        doc = KnowledgeDocument(
            source=DataSourceType.TEXT,
            raw_data=text,
        )

        chunk_config = ChunkConfig(
            separator="\n",
            max_tokens=1000,
            remove_extra_spaces=False,
        )

        response = client.add_documents(
            documents=[doc],
            table_name=KB_DATASET_NAME,
            chunk_config=chunk_config,
        )

        if hasattr(response, 'code') and response.code == 0:
            logger.info(f"Synced event to KB: {event.get('title', 'unknown')[:40]}")
            return True
        else:
            msg = getattr(response, 'msg', 'unknown error')
            logger.error(f"KB sync failed for '{event.get('title', 'unknown')[:30]}': {msg}")
            return False

    except Exception as e:
        logger.error(f"KB sync exception for '{event.get('title', 'unknown')[:30]}': {e}")
        return False


def sync_all_events_to_kb(ctx=None) -> dict:
    """
    全量同步 event_info 到知识库

    Returns:
        {"total": int, "success": int, "failed": int}
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="sync_all_events_to_kb")

    from tools.data_sync_workflow import _get_supabase
    supabase = _get_supabase()

    # 查询所有事件
    response = supabase.table("event_info").select("*").execute()
    events = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

    stats = {"total": len(events), "success": 0, "failed": 0}
    logger.info(f"Starting KB sync for {stats['total']} events...")

    for i, event in enumerate(events):
        ok = sync_event_to_kb(event, ctx=ctx)
        if ok:
            stats["success"] += 1
        else:
            stats["failed"] += 1

        if (i + 1) % 20 == 0:
            logger.info(f"KB sync progress: {i+1}/{stats['total']}")

    logger.info(f"KB sync complete: {stats}")
    return stats


@tool
def search_knowledge_base(query: str, top_k: int = 5) -> str:
    """
    知识库语义搜索。在竞赛知识库中搜索相关内容，可用于查找竞赛详情、规则、政策等。

    Args:
        query: 搜索文本
        top_k: 返回结果数量，默认5

    Returns:
        JSON格式的搜索结果
    """
    ctx = request_context.get() or new_context(method="search_kb")

    try:
        client = _get_kb_client(ctx)
        response = client.search(
            query=query,
            table_names=[KB_DATASET_NAME],
            top_k=top_k,
            min_score=0.3,
        )

        results = []
        if hasattr(response, 'code') and response.code == 0:
            for chunk in response.chunks:
                results.append({
                    "content": chunk.content,
                    "score": chunk.score,
                    "doc_id": getattr(chunk, 'doc_id', ''),
                })
        else:
            msg = getattr(response, 'msg', 'unknown error')
            logger.error(f"KB search failed: {msg}")
            return json.dumps({"error": f"知识库搜索失败: {msg}"}, ensure_ascii=False)

        logger.info(f"KB search for '{query[:30]}': found {len(results)} results")
        if not results:
            return json.dumps({"message": "知识库中未找到相关内容"}, ensure_ascii=False)
        return json.dumps(results, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"KB search exception: {e}")
        return json.dumps({"error": f"知识库搜索异常: {str(e)}"}, ensure_ascii=False)
