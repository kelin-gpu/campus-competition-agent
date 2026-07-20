#!/usr/bin/env python3
"""黑客松手动搜索与同步 CLI v2。

用法:
    python scripts/search_hackathons.py                           # 全来源同步
    python scripts/search_hackathons.py --dry-run                 # 只搜索/过滤，不写DB
    python scripts/search_hackathons.py --dry-run --json          # JSON输出
    python scripts/search_hackathons.py --source devfolio --limit 30
    python scripts/search_hackathons.py --source all --limit 200 --verbose
    python scripts/search_hackathons.py --save-report hackathon_dry_run.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

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
    parser = argparse.ArgumentParser(description="Hackathon search & sync CLI v2")
    parser.add_argument("--dry-run", action="store_true", help="No DB write")
    parser.add_argument("--limit", type=int, default=60, help="Max candidates")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--source", type=str, default="all",
        help="Source to use: devfolio, mlh, hackclub, devpost, search, all"
    )
    parser.add_argument("--max-pages", type=int, default=3, help="Max listing pages")
    parser.add_argument("--save-report", type=str, default="", help="Save report JSON to file")

    args = parser.parse_args()

    os.environ["HACKATHON_SEARCH_LIMIT"] = str(args.limit)
    _setup_logging(args.verbose)
    logger = logging.getLogger("hackathon_cli")

    from coze_coding_utils.runtime_ctx.context import new_context
    from tools.hackathon_sync import run_hackathon_sync

    ctx = new_context(method="hackathon_cli")

    source_map = {
        "devfolio": ["devfolio"],
        "mlh": ["mlh"],
        "hackclub": ["hackclub"],
        "devpost": ["devpost"],
        "search": ["general_search"],
        "all": None,
    }
    sources = source_map.get(args.source, source_map["all"])

    logger.info(f"Hackathon sync v2: source={args.source}, dry_run={args.dry_run}, limit={args.limit}")
    stats = run_hackathon_sync(ctx=ctx, dry_run=args.dry_run, sources=sources, limit=args.limit)

    if args.json:
        output = json.dumps(stats, ensure_ascii=False, default=str, indent=2)
        print(output)
    else:
        _print_pretty(stats, args.dry_run)

    if args.save_report:
        with open(args.save_report, "w", encoding="utf-8") as f:
            json.dump(stats, f, ensure_ascii=False, default=str, indent=2)
        logger.info(f"Report saved to {args.save_report}")

    logger.info("Hackathon CLI completed")


def _print_pretty(stats: dict, dry_run: bool) -> None:
    print("\n" + "=" * 70)
    print("  黑客松搜索同步结果 (v2)")
    print("=" * 70)
    print(f"  发现候选:           {stats.get('discovered', 0):>6}")
    print(f"  预去重:             {stats.get('prefetch_duplicates', 0):>6}")
    print(f"  详情页候选:         {stats.get('detail_page_candidates', 0):>6}")
    print(f"  通过筛选:           {stats.get('accepted', 0):>6}")
    print(f"  入库:               {stats.get('added', 0):>6}")
    print("-" * 70)

    # Per-source breakdown
    sources = stats.get("sources", {})
    for src_name, src_stats in sorted(sources.items()):
        print(f"\n  [{src_name}]")
        for k, v in sorted(src_stats.items()):
            if v > 0:
                print(f"    {k}: {v}")

    # Accepted samples (dry-run)
    if dry_run and "accepted_samples" in stats:
        samples = stats["accepted_samples"]
        if samples:
            print(f"\n  接受样例 ({len(samples)} 条):")
            for s in samples:
                print(f"    [{s.get('registration_status', '?')}] {s.get('title', '?')[:60]}")
                print(f"       {s.get('source_url', '')[:100]}")
                if s.get("signup_deadline"):
                    print(f"       截止: {s['signup_deadline']}")

    # Skip summary
    details = stats.get("details", [])
    if details:
        from collections import Counter
        reason_counts = Counter(d.get("action", "?") for d in details)
        print(f"\n  跳过原因统计 ({len(details)} 条):")
        for reason, count in reason_counts.most_common():
            print(f"    {reason}: {count}")

    print("=" * 70)


if __name__ == "__main__":
    main()
