#!/usr/bin/env python3
"""全量数据重新爬取脚本
按顺序爬取所有数据源并入库：
1. 清理过期数据
2. 赛氪热门竞赛爬取
3. 教育部竞赛目录加载
4. 微信公众号文章爬取
5. 黑客松数据爬取
"""
import sys
import os
import json
import time
import logging

sys.path.insert(0, "/workspace/projects/src")
os.environ.setdefault("COZE_WORKSPACE_PATH", "/workspace/projects")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("recrawl_all")


def print_section(title: str):
    logger.info("=" * 60)
    logger.info(f"  {title}")
    logger.info("=" * 60)


def step1_clean_expired():
    """清理过期数据 & 刷新状态"""
    print_section("Step 1: 清理过期数据")
    try:
        from tools.data_sync_workflow import _cleanup_expired, _refresh_all_statuses, _get_supabase
        supabase = _get_supabase()
        deleted = _cleanup_expired(supabase)
        logger.info(f"已清理 {deleted} 条过期记录")
        refreshed = _refresh_all_statuses(supabase)
        logger.info(f"已刷新 {refreshed} 条记录状态")
        return {"deleted": deleted, "refreshed": refreshed}
    except Exception as e:
        logger.error(f"清理过期数据失败: {e}", exc_info=True)
        return {"error": str(e)}


def step2_saikr_crawl():
    """赛氪热门竞赛爬取"""
    print_section("Step 2: 赛氪热门竞赛爬取")
    try:
        from tools.saikr_crawler import crawl_saikr_hot_contests
        result = crawl_saikr_hot_contests(limit=50, sleep_seconds=0.8, fetch_details=True)
        records = result.get("records", [])
        logger.info(f"赛氪爬取完成: {len(records)} 条记录")
        # 转换为标准格式
        events = []
        for rec in records:
            events.append({
                "title": rec.get("title", ""),
                "detail_text": rec.get("detail_text", ""),
                "url": rec.get("detail_url", "") or rec.get("url", ""),
                "source": "赛氪",
                "source_name": "赛氪",
                "source_url": rec.get("detail_url", "") or rec.get("url", ""),
                "organizer": rec.get("organizer", ""),
            })
        # 保存原始数据到 assets
        output_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), "assets", "data", "saikr_crawled_latest.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(events, f, ensure_ascii=False, indent=2)
        logger.info(f"赛氪原始数据已保存至: {output_path}")
        return {"count": len(events), "events": events}
    except Exception as e:
        logger.error(f"赛氪爬取失败: {e}", exc_info=True)
        return {"count": 0, "error": str(e), "events": []}


def step3_ministry_catalog():
    """教育部竞赛目录加载"""
    print_section("Step 3: 教育部竞赛目录加载")
    try:
        from tools.data_sync_workflow import load_ministry_data
        ministry_data = load_ministry_data()
        logger.info(f"教育部目录加载完成: {len(ministry_data)} 条")
        return {"count": len(ministry_data), "data": ministry_data}
    except Exception as e:
        logger.error(f"教育部目录加载失败: {e}", exc_info=True)
        return {"count": 0, "error": str(e)}


def step4_wechat_crawl():
    """微信公众号文章爬取"""
    print_section("Step 4: 微信公众号文章爬取")
    try:
        from tools.wechat_crawler import search_all_accounts, is_relevant, fetch_wechat_article, REQUEST_INTERVAL
        import hashlib
        import time as _time

        # 1. 搜索所有目标公众号
        articles = search_all_accounts()
        logger.info(f"Total articles found: {len(articles)}")

        # 2. 标题关键词过滤
        relevant = [a for a in articles if is_relevant(a.get("title", ""))]
        logger.info(f"Relevant after keyword filter: {len(relevant)}")

        # 3. 解析正文（修复 detail=None 时的 bug）
        results = []
        seen_urls = set()

        for article in relevant:
            url = article.get("url", "")
            if not url or url in seen_urls:
                continue
            seen_urls.add(url)

            if results:
                _time.sleep(REQUEST_INTERVAL)

            detail = fetch_wechat_article(url)
            if detail is None:
                detail_text = article.get("summary", "")
                if not detail_text:
                    continue
                title = article.get("title", "")
                publish_time = article.get("publish_time", "")
                author = ""
            else:
                detail_text = detail.get("detail_text", "")
                title = detail.get("title") or article.get("title", "")
                publish_time = detail.get("publish_time") or article.get("publish_time", "")
                author = detail.get("author", "")

            if not is_relevant(title, detail_text):
                continue

            url_hash = hashlib.md5(url.encode()).hexdigest()[:8].upper()
            results.append({
                "title": title,
                "detail_text": detail_text,
                "url": url,
                "publish_time": publish_time,
                "source_name": article.get("source_name", "微信公众号"),
                "author": author,
                "_wechat_id": f"WX-{url_hash}",
            })

        logger.info(f"微信公众号爬取完成: {len(results)} 篇相关文章")
        # 保存原始数据
        output_path = os.path.join(os.getenv("COZE_WORKSPACE_PATH"), "assets", "data", "wechat_crawled_latest.json")
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        logger.info(f"微信原始数据已保存至: {output_path}")
        return {"count": len(results), "articles": results}
    except Exception as e:
        logger.error(f"微信公众号爬取失败: {e}", exc_info=True)
        return {"count": 0, "error": str(e), "articles": []}


def step5_hackathon_crawl():
    """黑客松数据爬取"""
    print_section("Step 5: 黑客松数据爬取")
    try:
        from tools.hackathon_sync import run_hackathon_sync
        from coze_coding_utils.runtime_ctx.context import new_context
        ctx = new_context(method="recrawl_hackathon")
        stats = run_hackathon_sync(ctx=ctx, dry_run=False, limit=60)
        logger.info(f"黑客松同步完成: {json.dumps(stats, ensure_ascii=False, default=str)}")
        return stats
    except Exception as e:
        logger.error(f"黑客松爬取失败: {e}", exc_info=True)
        return {"error": str(e)}


def step6_full_sync_to_db(saikr_events, ministry_data, wechat_articles):
    """AI 补全 + 入库"""
    print_section("Step 6: AI 补全 + 数据入库")
    try:
        from tools.data_sync_workflow import run_full_sync
        from coze_coding_utils.runtime_ctx.context import new_context
        ctx = new_context(method="recrawl_full_sync")
        stats = run_full_sync(ctx=ctx, skip_enrichment=False)
        logger.info(f"全量同步入库完成: {json.dumps(stats, ensure_ascii=False, default=str)}")
        return stats
    except Exception as e:
        logger.error(f"全量同步入库失败: {e}", exc_info=True)
        return {"error": str(e)}


def step7_verify():
    """验证数据库中数据量"""
    print_section("Step 7: 验证数据库数据")
    try:
        from sqlalchemy import text
        from storage.database.db import get_engine
        engine = get_engine()
        with engine.connect() as conn:
            catalog_count = conn.execute(text("SELECT COUNT(*) FROM competition_catalog")).scalar()
            edition_count = conn.execute(text("SELECT COUNT(*) FROM event_edition")).scalar()
            evidence_count = conn.execute(text("SELECT COUNT(*) FROM field_evidence")).scalar()
            status_dist = conn.execute(text(
                "SELECT status, COUNT(*) FROM event_edition GROUP BY status ORDER BY COUNT(*) DESC"
            )).fetchall()
            source_dist = conn.execute(text(
                "SELECT source_name, COUNT(*) FROM event_edition GROUP BY source_name ORDER BY COUNT(*) DESC"
            )).fetchall()

        result = {
            "competition_catalog": catalog_count,
            "event_edition": edition_count,
            "field_evidence": evidence_count,
            "status_distribution": {row[0]: row[1] for row in status_dist},
            "source_distribution": {row[0]: row[1] for row in source_dist},
        }
        logger.info(f"数据库验证结果: {json.dumps(result, ensure_ascii=False, indent=2)}")
        return result
    except Exception as e:
        logger.error(f"数据库验证失败: {e}", exc_info=True)
        return {"error": str(e)}


def main():
    start_time = time.time()
    logger.info("=" * 60)
    logger.info("  全量数据重新爬取 - 开始")
    logger.info("=" * 60)

    results = {}

    # Step 1: 清理过期数据
    results["step1_clean"] = step1_clean_expired()

    # Step 2: 赛氪爬取
    saikr_result = step2_saikr_crawl()
    results["step2_saikr"] = {"count": saikr_result["count"]}
    if "error" in saikr_result:
        results["step2_saikr"]["error"] = saikr_result["error"]

    # Step 3: 教育部目录
    ministry_result = step3_ministry_catalog()
    results["step3_ministry"] = {"count": ministry_result["count"]}
    if "error" in ministry_result:
        results["step3_ministry"]["error"] = ministry_result["error"]

    # Step 4: 微信公众号爬取
    wechat_result = step4_wechat_crawl()
    results["step4_wechat"] = {"count": wechat_result["count"]}
    if "error" in wechat_result:
        results["step4_wechat"]["error"] = wechat_result["error"]

    # Step 5: 黑客松爬取
    hackathon_result = step5_hackathon_crawl()
    results["step5_hackathon"] = {k: v for k, v in hackathon_result.items() if k != "per_source_stats"}

    # Step 6: 全量同步入库（赛氪 + 教育部 + 微信）
    sync_result = step6_full_sync_to_db(
        saikr_result.get("events", []),
        ministry_result.get("data", []),
        wechat_result.get("articles", []),
    )
    results["step6_sync"] = sync_result

    # Step 7: 验证
    results["step7_verify"] = step7_verify()

    elapsed = time.time() - start_time
    logger.info("=" * 60)
    logger.info(f"  全量数据重新爬取 - 完成 (耗时 {elapsed:.1f}s)")
    logger.info("=" * 60)

    # 输出摘要
    summary = {
        "elapsed_seconds": round(elapsed, 1),
        "saikr_crawled": results.get("step2_saikr", {}).get("count", 0),
        "ministry_loaded": results.get("step3_ministry", {}).get("count", 0),
        "wechat_crawled": results.get("step4_wechat", {}).get("count", 0),
        "hackathon_sync": {k: v for k, v in results.get("step5_hackathon", {}).items() if k != "error"},
        "db_sync": results.get("step6_sync", {}),
        "db_verify": results.get("step7_verify", {}),
    }
    logger.info(f"\n{'='*60}\n摘要:\n{json.dumps(summary, ensure_ascii=False, indent=2)}\n{'='*60}")

    return results


if __name__ == "__main__":
    main()
