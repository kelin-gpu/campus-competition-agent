"""
数据同步与AI补全工具
供Agent调用的工具函数，封装数据同步工作流
"""
import json
import logging

from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)


@tool
def trigger_full_sync() -> str:
    """触发全量数据同步：加载赛氪数据和教育部目录，进行AI字段补全后去重合并入库。
    适用于首次导入或需要完整刷新数据的场景。"""
    ctx = request_context.get() or new_context(method="trigger_full_sync")
    try:
        from tools.data_sync_workflow import run_full_sync
        stats = run_full_sync(ctx=ctx)
        return (
            f"全量同步完成！\n"
            f"- 新增：{stats['added']} 条\n"
            f"- 更新：{stats['updated']} 条\n"
            f"- 跳过：{stats['skipped']} 条\n"
            f"- 错误：{stats['errors']} 条"
        )
    except Exception as e:
        logger.error(f"Full sync failed: {e}", exc_info=True)
        return f"全量同步失败：{str(e)}"


@tool
def trigger_incremental_sync(raw_events_json: str) -> str:
    """触发增量数据同步：传入新的原始数据（JSON数组格式），进行AI补全后去重入库。
    每条数据应包含 title、detail_text、url 等字段。
    适用于爬虫新增数据后的增量更新。"""
    ctx = request_context.get() or new_context(method="trigger_incremental_sync")
    try:
        raw_events = json.loads(raw_events_json)
        if not isinstance(raw_events, list):
            return "输入格式错误：请传入JSON数组"

        from tools.data_sync_workflow import run_incremental_sync
        stats = run_incremental_sync(raw_events, ctx=ctx)
        return (
            f"增量同步完成！\n"
            f"- 新增：{stats['added']} 条\n"
            f"- 更新：{stats['updated']} 条\n"
            f"- 跳过：{stats['skipped']} 条\n"
            f"- 错误：{stats['errors']} 条"
        )
    except json.JSONDecodeError:
        return "JSON解析失败，请检查输入格式"
    except Exception as e:
        logger.error(f"Incremental sync failed: {e}", exc_info=True)
        return f"增量同步失败：{str(e)}"


@tool
def enrich_single_event_tool(title: str, detail_text: str, url: str = "", organizer: str = "") -> str:
    """对单条竞赛/活动原始数据进行AI字段补全，返回结构化JSON。
    适用于用户提供了一条竞赛通知链接或文本，需要提取结构化信息的场景。"""
    ctx = request_context.get() or new_context(method="enrich_single_event")
    try:
        from tools.event_enrichment import enrich_single_event as _enrich
        raw_event = {
            "title": title,
            "detail_text": detail_text,
            "url": url,
            "organizer": organizer,
        }
        result = _enrich(raw_event, ctx=ctx)
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Enrichment failed: {e}", exc_info=True)
        return f"AI补全失败：{str(e)}"


@tool
def get_sync_status() -> str:
    """查看定时同步任务的状态，包括下次执行时间和任务列表。"""
    try:
        from tools.scheduled_sync import get_scheduler_status
        status = get_scheduler_status()
        if not status["running"]:
            return "定时同步未启动。当前无定时任务运行。"
        jobs_info = []
        for job in status["jobs"]:
            jobs_info.append(f"  - {job['name']}：下次执行 {job['next_run']}")
        return "定时同步运行中\n" + "\n".join(jobs_info)
    except Exception as e:
        return f"查询同步状态失败：{str(e)}"


@tool
def start_scheduled_sync() -> str:
    """启动定时数据同步任务（每天凌晨2点自动执行全量同步，每6小时执行微信公众号增量同步）。"""
    try:
        from tools.scheduled_sync import start_scheduler
        start_scheduler()
        return "定时同步已启动！\n- 每天凌晨2:00：全量数据同步（教育部+赛氪+微信公众号）\n- 每6小时：微信公众号增量同步"
    except Exception as e:
        return f"启动定时同步失败：{str(e)}"


@tool
def trigger_wechat_sync(hours: int = 6) -> str:
    """手动触发微信公众号数据同步：抓取过去N小时内南京大学相关公众号发布的竞赛/活动信息，经AI补全后入库。
    默认抓取过去6小时的文章。"""
    ctx = request_context.get() or new_context(method="trigger_wechat_sync")
    try:
        from tools.data_sync_workflow import run_wechat_sync
        stats = run_wechat_sync(hours=hours, ctx=ctx)
        return (
            f"微信公众号同步完成（过去{hours}小时）！\n"
            f"- 新增：{stats['added']} 条\n"
            f"- 更新：{stats['updated']} 条\n"
            f"- 跳过：{stats['skipped']} 条\n"
            f"- 错误：{stats['errors']} 条"
        )
    except Exception as e:
        logger.error(f"WeChat sync failed: {e}", exc_info=True)
        return f"微信公众号同步失败：{str(e)}"


@tool
def list_wechat_sources() -> str:
    """列出当前监控的微信公众号列表，按校级核心/院系扩展/动态发现三类展示。"""
    try:
        from tools.wechat_data_source import get_wechat_sources
        result = get_wechat_sources()
        school_level = result["school_level"]
        college = result["college"]
        dynamic = result["dynamic"]
        total = result["total"]

        lines = [f"当前监控的微信公众号（共 {total} 个）：\n"]

        lines.append(f"### 一、校级核心（{len(school_level)}个，永久保留）")
        for i, acc in enumerate(school_level, 1):
            lines.append(f"{i}. **{acc['name']}** — {acc['desc']}")

        lines.append(f"\n### 二、院系扩展（{len(college)}个，永久保留）")
        for i, acc in enumerate(college, 1):
            lines.append(f"{i}. **{acc['name']}** — {acc['desc']}")

        if dynamic:
            lines.append(f"\n### 三、动态发现（{len(dynamic)}个）")
            for i, acc in enumerate(dynamic, 1):
                lines.append(f"{i}. **{acc['name']}** — {acc['desc']}")
        else:
            lines.append("\n### 三、动态发现\n暂无动态发现的公众号（缓存为空或尚未执行发现）")

        lines.append(f"\n抓取顺序：校级核心 → 院系扩展 → 动态发现")
        lines.append(f"每6小时自动抓取一次，公众号列表每7天自动刷新。")
        return "\n".join(lines)
    except Exception as e:
        return f"查询公众号列表失败：{str(e)}"


@tool
def refresh_wechat_accounts() -> str:
    """手动刷新微信公众号监控列表：重新搜索所有名称含'南京大学'的公众号，更新缓存。
    校级核心7个+院系扩展48个永远保留，动态发现的公众号会被更新。"""
    try:
        from tools.wechat_data_source import refresh_wechat_accounts as _refresh
        result = _refresh()
        school_level_count = result["school_level_count"]
        college_count = result["college_count"]
        dynamic_count = result["dynamic_count"]
        total = result["total"]
        return (
            f"公众号列表刷新完成！\n"
            f"- 校级核心：{school_level_count} 个（永久保留）\n"
            f"- 院系扩展：{college_count} 个（永久保留）\n"
            f"- 动态发现：{dynamic_count} 个\n"
            f"- 总计：{total} 个\n"
            f"缓存已更新，下次抓取将使用新列表。"
        )
    except Exception as e:
        return f"刷新公众号列表失败：{str(e)}"
