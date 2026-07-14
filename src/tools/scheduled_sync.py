"""
定时数据同步机制
- 每天凌晨2点执行全量同步（教育部+赛氪+微信公众号）
- 每6小时执行一次微信公众号增量同步
- 支持手动触发增量同步
- 可扩展新数据源
"""
import logging
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = logging.getLogger(__name__)

_scheduler = None
_scheduler_lock = threading.Lock()


def _scheduled_sync_job():
    """定时同步任务：每天凌晨2点执行全量同步"""
    logger.info("=== Scheduled full sync job started at {} ===".format(datetime.now().isoformat()))
    try:
        from tools.data_sync_workflow import run_full_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="scheduled_full_sync")
        stats = run_full_sync(ctx=ctx)
        logger.info(f"Scheduled full sync completed: {stats}")
    except Exception as e:
        logger.error(f"Scheduled full sync failed: {e}", exc_info=True)


def _scheduled_wechat_sync_job():
    """定时同步任务：每6小时执行微信公众号增量同步"""
    logger.info("=== Scheduled WeChat sync job started at {} ===".format(datetime.now().isoformat()))
    try:
        from tools.data_sync_workflow import run_wechat_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="scheduled_wechat_sync")
        stats = run_wechat_sync(hours=6, ctx=ctx)
        logger.info(f"Scheduled WeChat sync completed: {stats}")
    except Exception as e:
        logger.error(f"Scheduled WeChat sync failed: {e}", exc_info=True)


def start_scheduler(force_restart: bool = False):
    """启动定时调度器（幂等：多次调用只启动一次）

    Args:
        force_restart: 如果为True，先停止现有调度器再重新启动
    """
    global _scheduler
    with _scheduler_lock:
        if _scheduler is not None and _scheduler.running:
            if not force_restart:
                logger.info("Scheduler already running")
                return _scheduler
            else:
                logger.info("Force restarting scheduler...")
                _scheduler.shutdown(wait=False)
                _scheduler = None

        _scheduler = BackgroundScheduler(
            timezone="Asia/Shanghai",
            job_defaults={
                "coalesce": True,       # 合并错过的执行
                "max_instances": 1,     # 每个job最多1个实例
            }
        )

        # 每天凌晨2点执行全量同步（教育部+赛氪+微信公众号）
        _scheduler.add_job(
            _scheduled_sync_job,
            trigger=CronTrigger(hour=2, minute=0, timezone="Asia/Shanghai"),
            id="daily_full_sync",
            name="每日数据全量同步",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 每6小时执行微信公众号增量同步
        _scheduler.add_job(
            _scheduled_wechat_sync_job,
            trigger=IntervalTrigger(hours=6, timezone="Asia/Shanghai"),
            id="wechat_incremental_sync",
            name="微信公众号增量同步（每6小时）",
            replace_existing=True,
            misfire_grace_time=1800,
        )

        _scheduler.start()
        logger.info("Scheduler started successfully. Jobs: daily_full_sync@02:00, wechat_sync@every 6h")

        # 验证启动
        status = get_scheduler_status()
        logger.info(f"Scheduler verification: running={status['running']}, jobs={len(status['jobs'])}")
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
