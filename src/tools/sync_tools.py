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
    """启动定时数据同步任务（每天凌晨2点自动执行全量同步）。"""
    try:
        from tools.scheduled_sync import start_scheduler
        start_scheduler()
        return "定时同步已启动！每天凌晨2:00将自动执行全量数据同步。"
    except Exception as e:
        return f"启动定时同步失败：{str(e)}"
