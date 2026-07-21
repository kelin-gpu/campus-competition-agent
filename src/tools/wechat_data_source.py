"""
微信公众号数据源适配器
功能：
1. 封装 wechat_crawler，对接现有 data_sync_workflow 的增量同步接口
2. 提供 fetch_wechat_events(hours=N) 函数
3. 将公众号文章转换为标准 event_info 格式
"""
import logging
from datetime import datetime

from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

from tools.wechat_crawler import (
    crawl_wechat_events,
    get_wechat_accounts,
    refresh_wechat_accounts as _refresh_accounts,
    is_relevant,
    WECHAT_ACCOUNTS,
)
from tools.event_enrichment import (
    _rule_based_fallback,
    enrich_single_event,
    match_ministry_contest,
)

logger = logging.getLogger(__name__)


def fetch_wechat_raw_events(hours: int = 24) -> list:
    """
    获取过去 N 小时内的微信公众号文章（原始格式）

    Args:
        hours: 时间范围（小时），0=不过滤

    Returns:
        原始文章列表 [{"title", "detail_text", "url", "publish_time", "source_name", "author"}, ...]
    """
    logger.info(f"Fetching WeChat events (last {hours}h)...")
    articles = crawl_wechat_events(hours=hours)
    logger.info(f"Fetched {len(articles)} WeChat articles")
    return articles


def fetch_wechat_events(hours: int = 24, ctx=None) -> list:
    """
    获取过去 N 小时内的微信公众号文章，并转换为 data_sync_workflow 可消费的标准格式

    Args:
        hours: 时间范围（小时）
        ctx: 请求上下文

    Returns:
        标准格式事件列表（可直接传给 run_incremental_sync）
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="fetch_wechat_events")

    raw_articles = fetch_wechat_raw_events(hours=hours)

    # 转换为标准格式（与赛氪数据格式对齐）
    standard_events = []
    for article in raw_articles:
        event = {
            "title": article.get("title", ""),
            "detail_text": article.get("detail_text", ""),
            "url": article.get("url", ""),
            "source_url": article.get("canonical_url") or article.get("url", ""),
            "organizer": article.get("author", ""),
            "source_name": article.get("source_name", "微信公众号"),
            "publish_time": article.get("publish_time", ""),
            "source_article_id": article.get("source_article_id") or article.get("_wechat_id", ""),
            "candidate_article_id": article.get("candidate_article_id", ""),
            "target_account": article.get("target_account", ""),
        }
        standard_events.append(event)

    logger.info(f"Converted {len(standard_events)} WeChat articles to standard format")
    return standard_events


def enrich_wechat_events(hours: int = 24, ctx=None) -> list:
    """
    获取公众号文章并进行 AI 字段补全

    Args:
        hours: 时间范围（小时）
        ctx: 请求上下文

    Returns:
        AI 补全后的标准 event_info 字典列表
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="enrich_wechat_events")

    raw_events = fetch_wechat_raw_events(hours=hours)
    enriched = []

    for i, article in enumerate(raw_events):
        title = article.get("title", "")
        logger.info(f"Enriching WeChat event {i+1}/{len(raw_events)}: {title[:50]}")

        raw_event = {
            "title": title,
            "detail_text": article.get("detail_text", ""),
            "url": article.get("url", ""),
            "source_url": article.get("canonical_url") or article.get("url", ""),
            "organizer": article.get("author", ""),
            "source_name": article.get("source_name", "微信公众号"),
            "publish_time": article.get("publish_time", ""),
            "source_article_id": article.get("source_article_id") or article.get("_wechat_id", ""),
            "candidate_article_id": article.get("candidate_article_id", ""),
            "target_account": article.get("target_account", ""),
        }

        try:
            result = enrich_single_event(raw_event, ctx=ctx)
            # 覆盖 source_name 为公众号名
            result["source_name"] = article.get("source_name", "微信公众号")
            result["source_url"] = article.get("canonical_url") or article.get("url", "")
            result["publish_time"] = article.get("publish_time", "")
            result["source_article_id"] = article.get("source_article_id") or article.get("_wechat_id", "")
            result["candidate_article_id"] = article.get("candidate_article_id", "")
            result["target_account"] = article.get("target_account", "")
            result.setdefault("scope_type", "校内活动")  # 公众号来源默认为校内
            enriched.append(result)
        except Exception as e:
            logger.error(f"Failed to enrich WeChat event '{title[:30]}': {e}")
            result = _rule_based_fallback(raw_event, match_ministry_contest(title))
            result["source_name"] = article.get("source_name", "微信公众号")
            result["source_url"] = article.get("canonical_url") or article.get("url", "")
            result["publish_time"] = article.get("publish_time", "")
            result["source_article_id"] = article.get("source_article_id") or article.get("_wechat_id", "")
            result["candidate_article_id"] = article.get("candidate_article_id", "")
            result["target_account"] = article.get("target_account", "")
            result["scope_type"] = "校内活动"
            enriched.append(result)

    logger.info(f"Enriched {len(enriched)} WeChat events")
    return enriched


def get_wechat_sources() -> dict:
    """
    获取当前监控的公众号列表，按校级核心/院系扩展/动态发现三类展示

    Returns:
        {"school_level": list, "college": list, "dynamic": list, "total": int}
    """
    accounts = get_wechat_accounts()
    school_level = [a for a in accounts if a.get("category") == "school_level"]
    college = [a for a in accounts if a.get("category") == "college"]
    dynamic = [a for a in accounts if a.get("category") == "dynamic"]
    return {
        "school_level": school_level,
        "college": college,
        "dynamic": dynamic,
        "total": len(accounts),
    }


def refresh_wechat_accounts() -> dict:
    """
    手动刷新公众号监控列表（绕过缓存，重新搜索）

    Returns:
        {"school_level_count": int, "college_count": int, "dynamic_count": int, "total": int}
    """
    result = _refresh_accounts()
    # Re-fetch to get categorized counts
    accounts = get_wechat_accounts()
    school_level_count = len([a for a in accounts if a.get("category") == "school_level"])
    college_count = len([a for a in accounts if a.get("category") == "college"])
    dynamic_count = len([a for a in accounts if a.get("category") == "dynamic"])
    result["school_level_count"] = school_level_count
    result["college_count"] = college_count
    result["dynamic_count"] = dynamic_count
    logger.info(f"Refreshed WeChat accounts: school_level={school_level_count}, college={college_count}, dynamic={dynamic_count}, total={result['total']}")
    return result
