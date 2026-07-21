"""
定时数据同步机制
- 每天凌晨2点执行全量同步（教育部+赛氪+微信公众号）
- 每6小时执行一次微信公众号增量同步
- 每12小时执行一次黑客松专项同步（可配置）
- 每月1日凌晨3点执行教育部竞赛目录校验
- 支持手动触发增量同步
- 可扩展新数据源
"""
import logging
import os
import threading
import random
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
        stats = run_full_sync(ctx=ctx, skip_enrichment=True)
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


def _scheduled_hackathon_sync_job():
    """定时同步任务：黑客松专项搜索与同步"""
    # 检查环境变量开关
    enabled = os.getenv("HACKATHON_SYNC_ENABLED", "true").lower()
    if enabled not in ("true", "1", "yes"):
        logger.info("Hackathon sync disabled via HACKATHON_SYNC_ENABLED")
        return

    logger.info("=== Scheduled hackathon sync job started at {} ===".format(datetime.now().isoformat()))
    try:
        from tools.hackathon_sync import run_hackathon_sync
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="scheduled_hackathon_sync")
        stats = run_hackathon_sync(ctx=ctx, dry_run=False)
        logger.info(f"Scheduled hackathon sync completed: {stats}")
    except Exception as e:
        logger.error(f"Scheduled hackathon sync failed: {e}", exc_info=True)


def _scheduled_ministry_validation_job():
    """定时任务：每月1日凌晨3点校验教育部竞赛目录"""
    logger.info("=== Scheduled ministry catalog validation started at {} ===".format(datetime.now().isoformat()))
    try:
        from tools.ministry_catalog_validator import validate_ministry_catalog
        from coze_coding_utils.runtime_ctx.context import new_context

        ctx = new_context(method="scheduled_ministry_validation")
        # 注意：validate_ministry_catalog 是 @tool 装饰的函数，使用 .invoke() 调用
        report = validate_ministry_catalog.invoke({})
        logger.info(f"Ministry catalog validation completed:\n{report}")
    except Exception as e:
        logger.error(f"Scheduled ministry catalog validation failed: {e}", exc_info=True)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        logger.warning(f"Invalid env {name}={os.getenv(name)}, using default {default}")
        return default


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

        # 黑客松专项同步（默认每12小时，可通过环境变量调整）
        hackathon_interval = _env_int("HACKATHON_SYNC_INTERVAL_HOURS", 12)
        # 添加少量 jitter，避免固定时刻同时请求
        jitter = random.randint(0, min(600, hackathon_interval * 60))
        _scheduler.add_job(
            _scheduled_hackathon_sync_job,
            trigger=IntervalTrigger(
                hours=hackathon_interval,
                timezone="Asia/Shanghai",
                jitter=jitter,
            ),
            id="hackathon_periodic_sync",
            name=f"黑客松专项同步（每{hackathon_interval}小时）",
            replace_existing=True,
            misfire_grace_time=max(1800, hackathon_interval * 300),
        )

        # 每月1日凌晨3点校验教育部竞赛目录
        _scheduler.add_job(
            _scheduled_ministry_validation_job,
            trigger=CronTrigger(day=1, hour=3, minute=0, timezone="Asia/Shanghai"),
            id="monthly_ministry_validation",
            name="教育部竞赛目录月度校验",
            replace_existing=True,
            misfire_grace_time=7200,
        )

        _scheduler.start()
        logger.info(
            f"Scheduler started: daily_full_sync@02:00, wechat_sync@every 6h, "
            f"hackathon_sync@every {hackathon_interval}h, "
            f"ministry_validation@monthly 1st 03:00"
        )

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
