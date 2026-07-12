"""
定时数据同步机制
- 每天凌晨2点执行全量同步
- 支持手动触发增量同步
- 可扩展新数据源
"""
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()


def _scheduled_sync_job():
    """定时同步任务：每天凌晨2点执行"""
    logger.info("=== Scheduled sync job started at {} ===".format(datetime.now().isoformat()))
    try:
        from tools.data_sync_workflow import run_full_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="scheduled_sync")
        stats = run_full_sync(ctx=ctx)
        logger.info(f"Scheduled sync completed: {stats}")
    except Exception as e:
        logger.error(f"Scheduled sync failed: {e}", exc_info=True)


def start_scheduler():
    """启动定时调度器（幂等：多次调用只启动一次）"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            logger.info("Scheduler already running")
            return _scheduler

        _scheduler = BackgroundScheduler()

        # 每天凌晨2点执行全量同步
        _scheduler.add_job(
            _scheduled_sync_job,
            trigger=CronTrigger(hour=2, minute=0),
            id="daily_full_sync",
            name="每日数据全量同步",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        _scheduler.start()
        logger.info("Scheduler started. Daily sync scheduled at 02:00")
        return _scheduler


def stop_scheduler():
    """停止定时调度器"""
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            _scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped")
        _scheduler = None


def get_scheduler_status() -> dict:
    """获取调度器状态"""
    global _scheduler
    if _scheduler is None:
        return {"running": False, "jobs": []}

    jobs = []
    for job in _scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })

    return {
        "running": _scheduler.running,
        "jobs": jobs,
    }
