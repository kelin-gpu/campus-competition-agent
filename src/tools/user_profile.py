"""
用户画像管理 + 个性化推荐工具
功能：
1. 用户画像 CRUD（按扣子运行时用户隔离）
2. 基于画像的个性化推荐（加权打分排序）
3. 从对话中自动抽取画像信息
"""
import json
import logging
from datetime import datetime
from langchain.tools import tool
from coze_coding_utils.log.write_log import request_context
from tools.user_identity import require_context_user_id

logger = logging.getLogger(__name__)

# Supabase client (lazy init)
_supabase_client = None


def _get_supabase():
    global _supabase_client
    if _supabase_client is None:
        from storage.database.supabase_client import get_supabase_client
        _supabase_client = get_supabase_client()
    return _supabase_client


def _current_user_id() -> str:
    """Read the authenticated user identity injected by the Coze runtime."""
    return require_context_user_id(request_context.get())


# ============================================================
# 画像管理
# ============================================================

def _get_or_create_profile(user_id: str) -> dict:
    """获取或创建用户画像"""
    supabase = _get_supabase()
    response = supabase.table("user_profile").select("*").eq("user_id", user_id).execute()
    data = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

    if data:
        return data[0]

    # 创建默认画像
    now = datetime.now().isoformat()
    new_profile = {
        "user_id": user_id,
        "nickname": "",
        "college": "",
        "major": "",
        "grade": "",
        "interest_tags": json.dumps([], ensure_ascii=False),
        "focus_contests": json.dumps([], ensure_ascii=False),
        "notify_preference": "daily",
        "last_active_time": now,
        "create_time": now,
        "update_time": now,
    }
    supabase.table("user_profile").insert(new_profile).execute()
    return new_profile


def _parse_json_field(val) -> list:
    """解析 JSON 字段（可能是字符串或列表）"""
    if val is None:
        return []
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError):
            return []
    return []


@tool
def get_user_profile() -> str:
    """获取用户画像信息。包含专业、年级、学院、兴趣标签、关注竞赛等。
    用户身份由扣子运行上下文自动提供，不接受手工 user_id。"""
    try:
        user_id = _current_user_id()
        profile = _get_or_create_profile(user_id)
        result = {
            "user_id": profile.get("user_id"),
            "nickname": profile.get("nickname", ""),
            "college": profile.get("college", ""),
            "major": profile.get("major", ""),
            "grade": profile.get("grade", ""),
            "interest_tags": _parse_json_field(profile.get("interest_tags")),
            "focus_contests": _parse_json_field(profile.get("focus_contests")),
            "notify_preference": profile.get("notify_preference", "daily"),
            "last_active_time": profile.get("last_active_time"),
        }
        return json.dumps(result, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Failed to get user profile: {e}")
        return f"获取用户画像失败：{str(e)}"


@tool
def update_user_profile(fields_json: str) -> str:
    """更新用户画像信息。fields_json 是 JSON 字符串，支持的字段：
    nickname, college, major, grade, interest_tags(JSON数组), notify_preference(daily/weekly/never)
    示例：'{"major": "计算机科学与技术", "grade": "大二", "interest_tags": ["算法竞赛", "人工智能"]}'"""
    try:
        user_id = _current_user_id()
        fields = json.loads(fields_json)
        if not isinstance(fields, dict):
            return "输入格式错误：fields_json 必须是 JSON 对象"

        supabase = _get_supabase()
        _get_or_create_profile(user_id)

        update_data = {}
        allowed_fields = {"nickname", "college", "major", "grade", "interest_tags", "notify_preference"}

        for key, value in fields.items():
            if key not in allowed_fields:
                continue
            if key == "interest_tags" and isinstance(value, list):
                update_data[key] = json.dumps(value, ensure_ascii=False)
            else:
                update_data[key] = value

        if not update_data:
            return "没有可更新的字段"

        update_data["update_time"] = datetime.now().isoformat()
        update_data["last_active_time"] = datetime.now().isoformat()

        supabase.table("user_profile").update(update_data).eq("user_id", user_id).execute()

        updated_fields = list(update_data.keys())
        return f"用户画像更新成功！已更新字段：{', '.join(updated_fields)}"

    except json.JSONDecodeError:
        return "JSON 解析失败，请检查格式"
    except Exception as e:
        logger.error(f"Failed to update user profile: {e}")
        return f"更新用户画像失败：{str(e)}"


@tool
def add_focus_contest(event_id: str) -> str:
    """添加关注的竞赛/活动。event_id 是竞赛的唯一编号。"""
    try:
        user_id = _current_user_id()
        supabase = _get_supabase()
        profile = _get_or_create_profile(user_id)
        focus = _parse_json_field(profile.get("focus_contests"))

        if event_id in focus:
            return f"竞赛 {event_id} 已在关注列表中"

        focus.append(event_id)
        supabase.table("user_profile").update({
            "focus_contests": json.dumps(focus, ensure_ascii=False),
            "update_time": datetime.now().isoformat(),
        }).eq("user_id", user_id).execute()

        return f"已添加关注竞赛：{event_id}，当前关注 {len(focus)} 个竞赛"

    except Exception as e:
        return f"添加关注失败：{str(e)}"


@tool
def remove_focus_contest(event_id: str) -> str:
    """取消关注竞赛/活动。event_id 是竞赛的唯一编号。"""
    try:
        user_id = _current_user_id()
        supabase = _get_supabase()
        profile = _get_or_create_profile(user_id)
        focus = _parse_json_field(profile.get("focus_contests"))

        if event_id not in focus:
            return f"竞赛 {event_id} 不在关注列表中"

        focus.remove(event_id)
        supabase.table("user_profile").update({
            "focus_contests": json.dumps(focus, ensure_ascii=False),
            "update_time": datetime.now().isoformat(),
        }).eq("user_id", user_id).execute()

        return f"已取消关注竞赛：{event_id}，当前关注 {len(focus)} 个竞赛"

    except Exception as e:
        return f"取消关注失败：{str(e)}"


# ============================================================
# 个性化推荐
# ============================================================

def _calculate_relevance_score(event: dict, profile: dict) -> float:
    """
    计算单个事件与用户画像的相关性得分

    评分规则：
    - 专业匹配：+3
    - 年级匹配：+2
    - 兴趣标签匹配：每个 +1
    - 教育部目录竞赛：+2
    - 关注列表中的竞赛：+5
    - DDL 紧迫度：7天内 +3，30天内 +2，其他 +1
    - 级别加分：国际级 +3，国家级 +2，省级 +1
    """
    score = 0.0

    major = profile.get("major", "")
    grade = profile.get("grade", "")
    interest_tags = _parse_json_field(profile.get("interest_tags"))
    focus_contests = _parse_json_field(profile.get("focus_contests"))

    event_id = event.get("event_id", "")
    event_major = event.get("target_major", "")
    event_grade = event.get("target_grade", "")
    event_category = event.get("category", "")
    event_level = event.get("contest_level", "")
    event_tags = _parse_json_field(event.get("tags"))
    is_ministry = event.get("is_ministry_approved", False)

    # 1. 专业匹配 (+3)
    if major and event_major:
        if major in event_major or any(m in event_major for m in major.split(",")):
            score += 3

    # 2. 年级匹配 (+2)
    if grade and event_grade:
        if grade in event_grade:
            score += 2

    # 3. 兴趣标签匹配 (每个 +1)
    for tag in interest_tags:
        tag_lower = tag.lower()
        if tag_lower in event_category.lower() or tag_lower in event_tags:
            score += 1
        # 也检查 policy_tags
        policy_tags = _parse_json_field(event.get("policy_tags"))
        if any(tag_lower in pt.lower() for pt in policy_tags):
            score += 0.5

    # 4. 教育部目录 (+2)
    if is_ministry:
        score += 2

    # 5. 关注列表 (+5)
    if event_id in focus_contests:
        score += 5

    # 6. DDL 紧迫度
    days_remaining = event.get("days_remaining")
    if days_remaining is not None:
        try:
            days = int(days_remaining)
            if 0 <= days <= 7:
                score += 3
            elif days <= 30:
                score += 2
            elif days > 0:
                score += 1
        except (ValueError, TypeError):
            pass

    # 7. 级别加分
    level_scores = {"国际级": 3, "国家级": 2, "省级": 1}
    score += level_scores.get(event_level, 0)

    return score


@tool
def get_personalized_recommendations(limit: int = 10) -> str:
    """获取基于用户画像的个性化竞赛/活动推荐列表。
    推荐算法综合考虑：专业匹配度、年级匹配度、兴趣标签、教育部目录、DDL紧迫度、竞赛级别。
    用户身份由扣子运行上下文自动提供。"""
    try:
        user_id = _current_user_id()
        supabase = _get_supabase()
        profile = _get_or_create_profile(user_id)

        # 查询所有报名中的事件
        response = supabase.table("event_info").select("*").in_(
            "status", ["报名中", "即将截止"]
        ).execute()
        events = response.data if hasattr(response, 'data') and isinstance(response.data, list) else []

        if not events:
            return "当前没有正在报名的竞赛/活动。"

        # 计算每个事件的得分
        scored_events = []
        for event in events:
            score = _calculate_relevance_score(event, profile)
            scored_events.append((score, event))

        # 按得分降序排序
        scored_events.sort(key=lambda x: x[0], reverse=True)

        # 取前 limit 个
        top_events = scored_events[:limit]

        # 格式化输出
        results = []
        for i, (score, event) in enumerate(top_events, 1):
            results.append({
                "rank": i,
                "title": event.get("title", ""),
                "event_id": event.get("event_id", ""),
                "scope_type": event.get("scope_type", ""),
                "category": event.get("category", ""),
                "contest_level": event.get("contest_level", ""),
                "signup_deadline": event.get("signup_deadline", ""),
                "days_remaining": event.get("days_remaining"),
                "source_url": event.get("source_url", ""),
                "is_ministry_approved": event.get("is_ministry_approved", False),
                "relevance_score": round(score, 1),
                "recommendation_reasons": _build_reasons(event, profile, score),
            })

        output = {
            "user_profile_summary": {
                "major": profile.get("major", "未设置"),
                "grade": profile.get("grade", "未设置"),
                "interest_tags": _parse_json_field(profile.get("interest_tags")),
            },
            "total_candidates": len(events),
            "recommendations": results,
        }
        return json.dumps(output, ensure_ascii=False, indent=2)

    except Exception as e:
        logger.error(f"Failed to get personalized recommendations: {e}")
        return f"获取个性化推荐失败：{str(e)}"


def _build_reasons(event: dict, profile: dict, score: float) -> list:
    """构建推荐理由列表，综合专业匹配、年级匹配、政策标签、DDL紧迫度等"""
    reasons = []
    major = profile.get("major", "")
    grade = profile.get("grade", "")
    event_major = event.get("target_major", "")
    event_grade = event.get("target_grade", "")
    policy_tags = _parse_json_field(event.get("policy_tags"))
    category = event.get("category", "")
    tags = _parse_json_field(event.get("tags"))
    organizer = event.get("organizer", "")

    # 1. 专业匹配
    if major and event_major:
        if major in event_major:
            reasons.append(f"专业核心竞赛（{major}）")
        elif any(m in event_major for m in major.split(",")):
            reasons.append(f"专业相关（{major}）")

    # 2. 年级匹配
    if grade and event_grade:
        if grade in event_grade:
            reasons.append(f"适合当前年级（{grade}）")
        else:
            reasons.append("可跨年级参与")

    # 3. 政策标签分析
    if "保研明确相关" in policy_tags:
        reasons.append("保研加分项")
    if "综测加分" in policy_tags:
        reasons.append("综测加分项")
    if "奖学金评定" in policy_tags:
        reasons.append("奖学金评定相关")
    if "五育" in str(policy_tags):
        reasons.append("五育认定")

    # 4. 教育部认证
    if event.get("is_ministry_approved"):
        reasons.append("教育部官方认证竞赛")

    # 5. 竞赛级别
    level = event.get("contest_level", "")
    level_reasons = {"国际级": "国际级高含金量赛事", "国家级": "国家级权威竞赛", "省级": "省级竞赛"}
    if level in level_reasons:
        reasons.append(level_reasons[level])

    # 6. DDL紧迫度
    days = event.get("days_remaining")
    if days is not None:
        try:
            d = int(days)
            if 0 <= d <= 3:
                reasons.append(f"⏰ 即将截止（仅剩{d}天）")
            elif 4 <= d <= 7:
                reasons.append(f"本周截止（{d}天）")
            elif 8 <= d <= 30:
                reasons.append(f"本月截止（{d}天）")
            else:
                reasons.append(f"充裕准备时间（{d}天）")
        except (ValueError, TypeError):
            pass
    else:
        reasons.append("报名时间待确认，建议主动查询")

    # 7. 分类标签
    if "数学建模" in str(tags) or "数学建模" in category:
        reasons.append("经典数学建模赛事")
    if "创新创业" in str(tags) or "创新创业" in category:
        reasons.append("创新创业类竞赛")
    if "程序设计" in str(tags) or "ACM" in str(tags) or "ICPC" in str(tags):
        reasons.append("程序设计竞赛")

    # 8. 权威主办方
    if "教育部" in organizer or "中国数学会" in organizer or "中国计算机学会" in organizer:
        reasons.append(f"权威主办方（{organizer[:15]}）")

    return reasons
