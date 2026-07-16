#!/usr/bin/env python3
"""黑客松手动搜索与同步 CLI。

用法:
    python scripts/search_hackathons.py                    # 搜索+解析+过滤+入库
    python scripts/search_hackathons.py --dry-run           # 只搜索/解析/过滤，不写数据库
    python scripts/search_hackathons.py --dry-run --json    # JSON 输出
    python scripts/search_hackathons.py --limit 30 --verbose
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

# Ensure src is on path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_src = os.path.join(_project_root, "src")
if _src not in sys.path:
    sys.path.insert(0, _src)


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Hackathon search & sync CLI")
    parser.add_argument("--dry-run", action="store_true", help="Search/parse/filter only, no DB write")
    parser.add_argument("--limit", type=int, default=0, help="Max candidates to discover (overrides HACKATHON_SEARCH_LIMIT)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.limit > 0:
        os.environ["HACKATHON_SEARCH_LIMIT"] = str(args.limit)

    _setup_logging(args.verbose)
    logger = logging.getLogger("hackathon_cli")

    from coze_coding_utils.runtime_ctx.context import new_context
    from tools.hackathon_sync import run_hackathon_sync

    ctx = new_context(method="hackathon_cli")
    logger.info(f"Starting hackathon sync (dry_run={args.dry_run}, json={args.json})")

    stats = run_hackathon_sync(ctx=ctx, dry_run=args.dry_run)

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, default=str, indent=2))
    else:
        print("\n" + "=" * 60)
        print("  黑客松搜索同步结果")
        print("=" * 60)
        print(f"  搜索结果数:         {stats['discovered']:>6}")
        print(f"  成功抓取详情:       {stats['fetched']:>6}")
        print(f"  通过全部筛选:       {stats['accepted']:>6}")
        print("-" * 60)
        print(f"    过期过滤:         {stats.get('expired_filtered', 0):>6}")
        print(f"    已关闭过滤:       {stats.get('closed_filtered', 0):>6}")
        print(f"    日期异常过滤:     {stats.get('invalid_date_filtered', 0):>6}")
        print(f"    超远期过滤:       {stats.get('too_far_future_filtered', 0):>6}")
        print(f"    活动已结束过滤:   {stats.get('event_passed_filtered', 0):>6}")
        print(f"    无法验证跳过:     {stats.get('unverified_skipped', 0):>6}")
        print(f"    非黑客松:         {stats.get('not_hackathon', 0):>6}")
        print(f"    抓取失败:         {stats.get('fetch_failed', 0):>6}")
        print(f"    去重:             {stats.get('duplicates', 0):>6}")
        print("-" * 60)
        print(f"  入库新增:           {stats['added']:>6}")
        print(f"  入库更新:           {stats['updated']:>6}")
        print(f"  错误:               {stats['errors']:>6}")
        print("=" * 60)

        # Show skip reasons
        skipped = [d for d in stats.get("details", []) if d.get("action") not in ("added", "updated", "merged", "accepted")]
        if skipped:
            print(f"\n跳过记录 ({len(skipped)} 条):")
            for s in skipped:
                print(f"  [{s.get('action', '?')}] {s.get('title', '?')[:60]}")
                if s.get("source_url"):
                    print(f"         {s['source_url'][:100]}")

    logger.info("Hackathon CLI completed")


if __name__ == "__main__":
    main()
