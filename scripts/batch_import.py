"""
批量数据入库脚本
1. 先导入教育部84项竞赛目录（直接入库，无需AI补全）
2. 再导入赛氪50条数据（AI字段补全后入库）
3. 去重合并
"""
import sys
import os
import json
import uuid
import logging
from datetime import datetime

# 设置路径
sys.path.insert(0, "/workspace/projects/src")
os.environ.setdefault("COZE_WORKSPACE_PATH", "/workspace/projects")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    from storage.database.supabase_client import get_supabase_client
    from tools.event_enrichment import _load_ministry_contests, _normalize_title, _edit_distance

    supabase = get_supabase_client()
    assets_dir = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "assets", "data")

    # ========== Step 1: 导入教育部84项竞赛目录 ==========
    logger.info("=== Step 1: Importing ministry contests ===")
    ministry_list = _load_ministry_contests()

    # 获取已有事件标题（去重用）
    resp = supabase.table("event_info").select("event_id,title").execute()
    existing_events = resp.data if hasattr(resp, 'data') and isinstance(resp.data, list) else []
    existing_titles = {_normalize_title(e["title"]) for e in existing_events}

    ministry_added = 0
    ministry_skipped = 0

    for item in ministry_list:
        norm_name = _normalize_title(item["name"])

        # 去重检查
        is_dup = False
        for et in existing_titles:
            if _edit_distance(norm_name, et) >= 0.85:
                is_dup = True
                break

        if is_dup:
            ministry_skipped += 1
            continue

        event_id = f"MIN-{uuid.uuid4().hex[:8].upper()}"
        record = {
            "event_id": event_id,
            "title": item["name"],
            "scope_type": "校外竞赛",
            "category": item.get("category", "其他"),
            "summary": f"{item['name']}由{item['organizer']}主办，是教育部认可的{item['level']}竞赛。",
            "target_major": "全校各专业",
            "target_grade": "大一,大二,大三",
            "contest_level": item.get("level", "国家级"),
            "tags": json.dumps(["教育部目录", "官方认可"], ensure_ascii=False),
            "policy_tags": json.dumps(["保研明确相关", "综测加分"], ensure_ascii=False),
            "organizer": item.get("organizer", ""),
            "source_name": "教育部竞赛目录",
            "source_url": "",
            "authority_level": "高",
            "status": "报名中",
            "is_ministry_approved": True,
            "update_time": datetime.now().isoformat(),
            "original_text": "",
        }

        try:
            supabase.table("event_info").insert(record).execute()
            ministry_added += 1
            existing_titles.add(norm_name)
        except Exception as e:
            logger.error(f"Failed to insert ministry contest '{item['name']}': {e}")

    logger.info(f"Ministry contests: added={ministry_added}, skipped(dup)={ministry_skipped}")

    # ========== Step 2: 导入赛氪50条数据（AI补全） ==========
    logger.info("=== Step 2: Importing saikr data with AI enrichment ===")
    saikr_path = os.path.join(assets_dir, "saikr_processed.json")
    with open(saikr_path, "r", encoding="utf-8") as f:
        saikr_data = json.load(f)

    from tools.event_enrichment import enrich_single_event, match_ministry_contest
    from coze_coding_utils.runtime_ctx.context import new_context
    ctx = new_context(method="batch_import")

    saikr_added = 0
    saikr_updated = 0
    saikr_errors = 0

    for i, raw_event in enumerate(saikr_data):
        title = raw_event.get("title", "")
        logger.info(f"[{i+1}/{len(saikr_data)}] Processing: {title[:40]}...")

        try:
            # AI补全
            enriched = enrich_single_event(raw_event, ctx=ctx)

            # 去重检查
            norm_title = _normalize_title(title)
            is_dup = False
            dup_event_id = None
            for et in existing_titles:
                if _edit_distance(norm_title, et) >= 0.85:
                    is_dup = True
                    # 找到对应的event_id
                    for e in existing_events:
                        if _normalize_title(e["title"]) == et:
                            dup_event_id = e["event_id"]
                            break
                    break

            # 确保 tags/policy_tags 是 JSON 字符串
            for field in ("tags", "policy_tags"):
                val = enriched.get(field)
                if isinstance(val, list):
                    enriched[field] = json.dumps(val, ensure_ascii=False)

            if is_dup and dup_event_id:
                # 更新已有记录
                update_data = {k: v for k, v in enriched.items() if v is not None and k != "event_id"}
                update_data["update_time"] = datetime.now().isoformat()
                supabase.table("event_info").update(update_data).eq("event_id", dup_event_id).execute()
                saikr_updated += 1
                logger.info(f"  -> Updated existing: {dup_event_id}")
            else:
                # 插入新记录
                event_id = f"SAI-{uuid.uuid4().hex[:8].upper()}"
                enriched["event_id"] = event_id
                enriched["update_time"] = datetime.now().isoformat()
                enriched.setdefault("status", "报名中")

                insert_data = {k: v for k, v in enriched.items() if v is not None}
                supabase.table("event_info").insert(insert_data).execute()
                saikr_added += 1
                existing_titles.add(norm_title)
                existing_events.append({"event_id": event_id, "title": title})
                logger.info(f"  -> Added new: {event_id}")

        except Exception as e:
            saikr_errors += 1
            logger.error(f"  -> Error: {e}")

    logger.info(f"Saikr data: added={saikr_added}, updated={saikr_updated}, errors={saikr_errors}")

    # ========== Step 3: 统计 ==========
    resp = supabase.table("event_info").select("event_id").execute()
    total = len(resp.data) if hasattr(resp, 'data') and isinstance(resp.data, list) else 0

    logger.info("=== Import Summary ===")
    logger.info(f"Ministry: added={ministry_added}, skipped={ministry_skipped}")
    logger.info(f"Saikr: added={saikr_added}, updated={saikr_updated}, errors={saikr_errors}")
    logger.info(f"Total events in DB: {total}")

    return {
        "ministry_added": ministry_added,
        "ministry_skipped": ministry_skipped,
        "saikr_added": saikr_added,
        "saikr_updated": saikr_updated,
        "saikr_errors": saikr_errors,
        "total_events": total,
    }


if __name__ == "__main__":
    result = main()
    print(json.dumps(result, ensure_ascii=False, indent=2))
