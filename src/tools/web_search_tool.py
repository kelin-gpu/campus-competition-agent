"""联网搜索工具 - 用于搜索竞赛/活动的补充信息"""
import json
import logging

from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)


@tool
def web_search_events(query: str, count: int = 5) -> str:
    """联网搜索竞赛/活动相关信息。当数据库中信息不足或用户询问最新信息时使用。
    搜索结果仅供参考，需标注来源为"联网搜索（结果仅供参考）"。

    Args:
        query: 搜索关键词，如"2025年蓝桥杯报名时间"、"南京大学五育活动"
        count: 返回结果数量，默认5条
    """
    try:
        ctx = request_context.get() or new_context(method="web_search")
        from coze_coding_dev_sdk import SearchClient
        client = SearchClient(ctx=ctx)

        response = client.web_search(
            query=query,
            count=count,
        )

        if not response.web_items:
            return f"未搜索到关于「{query}」的相关信息。"

        results = []
        for i, item in enumerate(response.web_items, 1):
            results.append({
                "title": item.title,
                "url": item.url,
                "snippet": item.snippet[:200] if item.snippet else "",
                "source": item.site_name,
                "publish_time": item.publish_time,
                "authority": item.auth_info_des or "",
            })

        return json.dumps({
            "query": query,
            "source": "联网搜索（结果仅供参考）",
            "results": results,
        }, ensure_ascii=False, default=str)

    except Exception as e:
        logger.error(f"联网搜索失败: {e}")
        return f"联网搜索出错: {str(e)}"
