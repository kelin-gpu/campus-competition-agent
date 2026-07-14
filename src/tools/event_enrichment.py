"""
AI字段补全工作流（Skill）
功能：
1. 用LLM从原始爬虫数据的 detail_text 中提取结构化字段
2. 与教育部84项目录做匹配，标记 is_ministry_approved 和 authority_level
3. 输出标准 event_info 结构
"""
import json
import os
import re
import signal
import logging
import signal
from datetime import datetime
from typing import Optional

from coze_coding_dev_sdk import LLMClient
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context
from langchain_core.messages import SystemMessage, HumanMessage

logger = logging.getLogger(__name__)

# 教育部目录缓存
_ministry_contests_cache = None

# 数据文件路径
ASSETS_DIR = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "assets", "data")


def _load_ministry_contests() -> list:
    """加载教育部84项竞赛目录"""
    global _ministry_contests_cache
    if _ministry_contests_cache is not None:
        return _ministry_contests_cache

    filepath = os.path.join(ASSETS_DIR, "ministry_contests_84.json")
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            _ministry_contests_cache = json.load(f)
        logger.info(f"Loaded {len(_ministry_contests_cache)} ministry contests")
    except Exception as e:
        logger.error(f"Failed to load ministry contests: {e}")
        _ministry_contests_cache = []
    return _ministry_contests_cache


def _normalize_title(title: str) -> str:
    """标准化标题：去除年份、届数、多余空格"""
    s = title.strip()
    # 去除年份 (2024, 2025, 2026等)
    s = re.sub(r'\d{4}[年\-]?', '', s)
    # 去除届数 (第一届, 第15届, 十五届等)
    s = re.sub(r'第[一二三四五六七八九十百零\d]+届', '', s)
    # 去除括号及内容
    s = re.sub(r'[（(][^)）]*[)）]', '', s)
    # 去除多余空格
    s = re.sub(r'\s+', '', s)
    return s.lower()


def _edit_distance(s1: str, s2: str) -> float:
    """计算两个字符串的相似度 (0-1, 1=完全相同)"""
    if not s1 or not s2:
        return 0.0
    len1, len2 = len(s1), len(s2)
    dp = [[0] * (len2 + 1) for _ in range(len1 + 1)]
    for i in range(len1 + 1):
        dp[i][0] = i
    for j in range(len2 + 1):
        dp[0][j] = j
    for i in range(1, len1 + 1):
        for j in range(1, len2 + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            dp[i][j] = min(dp[i-1][j] + 1, dp[i][j-1] + 1, dp[i-1][j-1] + cost)
    max_len = max(len1, len2)
    return 1.0 - dp[len1][len2] / max_len if max_len > 0 else 0.0


def match_ministry_contest(title: str, threshold: float = 0.85) -> Optional[dict]:
    """
    与教育部目录做模糊匹配
    返回匹配到的目录项，或 None
    """
    norm_title = _normalize_title(title)
    ministry_list = _load_ministry_contests()

    best_match = None
    best_score = 0.0

    for item in ministry_list:
        norm_name = _normalize_title(item["name"])
        score = _edit_distance(norm_title, norm_name)
        if score > best_score:
            best_score = score
            best_match = item

    if best_score >= threshold:
        logger.info(f"Ministry match: '{title}' -> '{best_match['name']}' (score={best_score:.3f})")
        return best_match
    return None


def _build_enrichment_prompt(raw_event: dict, ministry_match: Optional[dict] = None) -> str:
    """构建LLM提取提示词"""
    title = raw_event.get("title", "")
    detail_text = raw_event.get("detail_text", "")
    # 优先使用 detail_url（详情页），而非 url（可能是列表页）
    url = raw_event.get("detail_url") or raw_event.get("url", "")
    organizer = raw_event.get("organizer", "")

    ministry_hint = ""
    if ministry_match:
        ministry_hint = f"""
重要提示：该竞赛已匹配到教育部竞赛目录：
- 目录名称：{ministry_match['name']}
- 目录主办方：{ministry_match['organizer']}
- 目录级别：{ministry_match['level']}
- 目录分类：{ministry_match['category']}
请优先使用目录中的权威信息。is_ministry_approved 应为 true，authority_level 应为"高"。
"""

    return f"""你是一个竞赛/活动信息结构化提取专家。请从以下竞赛/活动的原始信息中，精确提取结构化字段。

## 原始信息
- 标题：{title}
- 主办方：{organizer}
- 来源链接：{url}
- 详情正文：
{detail_text}
{ministry_hint}
## 提取要求
请严格按以下JSON格式输出，每个字段都必须填写（无法确定的用合理默认值）：

```json
{{
  "title": "竞赛/活动完整名称",
  "scope_type": "校外竞赛 或 校内竞赛 或 校内活动",
  "category": "细分类型，如：程序设计竞赛/数学建模/创新创业/五育活动/学术讲座/电子信息/机械工程 等",
  "summary": "50-100字简介，简明扼要说明竞赛内容、参赛形式、获奖价值",
  "signup_deadline": "YYYY-MM-DDTHH:MM:SS+08:00 格式，如无法确定具体日期则用合理估计",
  "event_time": "YYYY-MM-DDTHH:MM:SS+08:00 格式，比赛/活动时间",
  "target_major": "逗号分隔的适合专业列表，如：计算机科学与技术,软件工程,人工智能",
  "target_grade": "逗号分隔的适合年级，如：大一,大二,大三",
  "contest_level": "国际级 或 国家级 或 省级 或 校级 或 院级",
  "tags": ["标签1", "标签2", "标签3"],
  "policy_tags": ["保研明确相关 或 保研可能相关", "综测加分", "五育明确相关 等"],
  "organizer": "主办方全称",
  "source_name": "来源名称",
  "source_url": "来源链接",
  "authority_level": "高 或 中 或 低",
  "is_ministry_approved": false
}}
```

## 字段填写规则
1. scope_type：教育部目录竞赛/全国性行业竞赛→校外竞赛；学校主办→校内竞赛/校内活动
2. **时间逻辑（重要）**：event_time（比赛/活动时间）必须 >= signup_deadline（报名截止时间），即先报名、后参赛。如果原文无法确定 event_time，填 null 不要编造。
2. category：根据竞赛内容归类到最合适的细分类型
3. summary：50-100字，包含竞赛核心内容和参赛价值
4. target_major：根据竞赛领域推断适合的专业，用逗号分隔
5. target_grade：一般全校性活动写"大一,大二,大三,大四"，专业竞赛写"大二,大三"
6. contest_level：国际级>国家级>省级>校级>院级
7. tags：3-5个标签，如"需要组队""个人赛""算法编程""低年级友好""需要Demo"等
8. policy_tags：根据竞赛级别和类型判断：
   - 国家级以上竞赛且教育部认可 → "保研明确相关"
   - 其他国家级/省级竞赛 → "保研可能相关"
   - 大部分竞赛 → "综测加分"
   - 五育类活动 → "五育明确相关"
9. authority_level：教育部目录竞赛=高，知名全国性竞赛=高，一般竞赛=中，来源不明=低
10. is_ministry_approved：如果匹配到教育部目录则为true

仅输出JSON，不要输出其他内容。"""


def enrich_single_event(raw_event: dict, ctx=None, llm_timeout: int = 30) -> dict:
    """
    对单条原始数据进行AI字段补全

    Args:
        raw_event: 原始数据字典，包含 title, detail_text, url, organizer 等
        ctx: 请求上下文
        llm_timeout: LLM调用超时秒数，默认30秒

    Returns:
        补全后的标准 event_info 字典
    """
    if ctx is None:
        ctx = request_context.get() or new_context(method="enrich_event")

    title = raw_event.get("title", "")

    # 1. 匹配教育部目录
    ministry_match = match_ministry_contest(title)

    # 2. 构建提示词并调用LLM
    prompt = _build_enrichment_prompt(raw_event, ministry_match)

    client = LLMClient(ctx=ctx)
    messages = [
        SystemMessage(content="你是一个竞赛信息结构化提取专家，只输出JSON，不输出其他内容。"),
        HumanMessage(content=prompt)
    ]

    # 设置超时保护
    import signal

    class LLMTimeoutError(Exception):
        pass

    def _timeout_handler(signum, frame):
        raise LLMTimeoutError(f"LLM call timed out after {llm_timeout}s")

    old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
    signal.alarm(llm_timeout)

    try:
        response = client.invoke(
            messages=messages,
            model="doubao-seed-2-0-lite-260215",
            temperature=0.2,
            max_completion_tokens=2000
        )
        signal.alarm(0)  # 取消超时
        content = response.content
        if isinstance(content, list):
            content = " ".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ) if content and not isinstance(content[0], str) else " ".join(content)

        # 提取JSON
        content_str = str(content).strip()
        json_match = re.search(r'```json\s*(.*?)\s*```', content_str, re.DOTALL)
        if json_match:
            content_str = json_match.group(1)
        elif content_str.startswith("```"):
            content_str = content_str[3:].strip()
            if content_str.endswith("```"):
                content_str = content_str[:-3].strip()

        enriched = json.loads(content_str)
    except LLMTimeoutError as e:
        logger.warning(f"LLM enrichment timed out for '{title}': {e}")
        enriched = _rule_based_fallback(raw_event, ministry_match)
    except Exception as e:
        signal.alarm(0)  # 确保取消超时
        logger.error(f"LLM enrichment failed for '{title}': {e}")
        # Fallback: 使用规则提取基本信息
        enriched = _rule_based_fallback(raw_event, ministry_match)
    finally:
        signal.signal(signal.SIGALRM, old_handler)  # 恢复原始handler
        signal.alarm(0)  # 确保取消

    # 3. 确保教育部匹配信息被正确标记
    if ministry_match:
        enriched["is_ministry_approved"] = True
        enriched["authority_level"] = "高"

    # 4. 补充缺失字段
    enriched.setdefault("title", title)
    enriched.setdefault("scope_type", "校外竞赛")
    enriched.setdefault("status", "报名中")
    enriched.setdefault("update_time", datetime.now().isoformat())
    enriched.setdefault("original_text", raw_event.get("detail_text", ""))

    return enriched


def _rule_based_fallback(raw_event: dict, ministry_match: Optional[dict] = None) -> dict:
    """规则兜底：当LLM调用失败时使用"""
    title = raw_event.get("title", "")
    detail_text = raw_event.get("detail_text", "")

    result = {
        "title": title,
        "scope_type": "校外竞赛",
        "category": "其他",
        "summary": detail_text[:100] if detail_text else title,
        "target_major": "全校各专业",
        "target_grade": "大一,大二,大三",
        "tags": json.dumps(["竞赛"], ensure_ascii=False),
        "policy_tags": json.dumps(["综测加分"], ensure_ascii=False),
        "organizer": raw_event.get("organizer", ""),
        "source_url": raw_event.get("detail_url") or raw_event.get("url", ""),
        "source_name": "赛氪",
        "authority_level": "中",
        "status": "报名中",
        "original_text": detail_text,
        "is_ministry_approved": False,
    }

    if ministry_match:
        result["is_ministry_approved"] = True
        result["authority_level"] = "高"
        result["category"] = ministry_match.get("category", "其他")

    # 尝试从detail_text提取日期
    date_patterns = [
        r'报名[时时]?[间间截止]*[：:为]?\s*(\d{4})[年\-](\d{1,2})[月\-](\d{1,2})',
        r'(\d{4})[年\-](\d{1,2})[月\-](\d{1,2}).*?报名',
    ]
    for pattern in date_patterns:
        m = re.search(pattern, detail_text)
        if m:
            y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
            result["signup_deadline"] = f"{y}-{mo}-{d}T23:59:59+08:00"
            break

    return result


class _EnrichTimeout(Exception):
    pass


def _timeout_handler(signum, frame):
    raise _EnrichTimeout("LLM调用超时")


def enrich_batch(raw_events: list, ctx=None, per_record_timeout: int = 30, total_timeout: int = 600) -> list:
    """
    批量AI字段补全（带超时保护）

    Args:
        raw_events: 原始数据列表
        ctx: 请求上下文
        per_record_timeout: 单条记录LLM调用超时秒数（默认30秒）
        total_timeout: 总超时秒数（默认600秒=10分钟）

    Returns:
        补全后的标准 event_info 字典列表（LLM失败的记录用规则兜底）
    """
    import time
    if ctx is None:
        ctx = request_context.get() or new_context(method="enrich_batch")

    results = []
    total = len(raw_events)
    start_time = time.time()
    llm_success = 0
    llm_failed = 0

    for i, event in enumerate(raw_events):
        # 检查总超时
        elapsed = time.time() - start_time
        if elapsed > total_timeout:
            logger.warning(f"enrich_batch 总超时({total_timeout}s)，剩余 {total - i} 条用规则兜底")
            for remaining in raw_events[i:]:
                fallback = _rule_based_fallback(remaining, match_ministry_contest(remaining.get("title", "")))
                results.append(fallback)
                llm_failed += 1
            break

        logger.info(f"Enriching event {i+1}/{total}: {event.get('title', 'unknown')[:30]}...")

        try:
            # 设置单条超时（仅Unix系统支持SIGALRM）
            if hasattr(signal, 'SIGALRM'):
                old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
                signal.alarm(per_record_timeout)

            enriched = enrich_single_event(event, ctx=ctx)

            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

            # 检查LLM是否真的返回了有效数据
            if not enriched.get("summary") and not enriched.get("category"):
                logger.warning(f"LLM返回空结果，降级: {event.get('title', 'unknown')[:40]}")
                fallback = _rule_based_fallback(event, match_ministry_contest(event.get("title", "")))
                results.append(fallback)
                llm_failed += 1
            else:
                results.append(enriched)
                llm_success += 1

        except _EnrichTimeout:
            logger.warning(f"LLM超时({per_record_timeout}s)，降级: {event.get('title', 'unknown')[:40]}")
            fallback = _rule_based_fallback(event, match_ministry_contest(event.get("title", "")))
            results.append(fallback)
            llm_failed += 1
        except Exception as e:
            logger.error(f"Failed to enrich event {i+1}: {e}")
            fallback = _rule_based_fallback(event, match_ministry_contest(event.get("title", "")))
            results.append(fallback)
            llm_failed += 1
        finally:
            if hasattr(signal, 'SIGALRM'):
                signal.alarm(0)

    logger.info(f"Enrichment complete: LLM成功={llm_success}, 降级={llm_failed}, 总耗时={time.time()-start_time:.1f}s")
    return results
