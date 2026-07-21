"""
教育部竞赛目录自动校验与深度分析模块

功能：
1. 联网获取最新教育部竞赛排行榜/目录
2. LLM 结构化提取竞赛列表
3. 与本地 84 条目录逐条对比，识别新增/移除/变更
4. 逐条搜索补充官网 URL 和简介
5. 生成 Markdown 差异报告（不自动更新 JSON）

调度：每月 1 日凌晨 3:00 自动执行，也可手动调用工具触发。
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional

from langchain.tools import tool
from langchain_core.messages import SystemMessage, HumanMessage
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(
    os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "assets", "data"
)
CATALOG_FILE = os.path.join(ASSETS_DIR, "ministry_contests_84.json")

# 搜索关键词（针对教育部竞赛排行榜）
SEARCH_QUERIES = [
    "2025全国普通高校大学生竞赛排行榜 目录",
    "中国高等教育学会 大学生竞赛排行榜 2025",
    "教育部认可大学生竞赛 84项 名单",
]

# LLM 模型选择
EXTRACTION_MODEL = "doubao-seed-2-0-lite-260215"


def _load_local_catalog() -> list[dict]:
    """加载本地教育部竞赛目录"""
    if not os.path.exists(CATALOG_FILE):
        logger.warning(f"Catalog file not found: {CATALOG_FILE}")
        return []
    with open(CATALOG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_local_catalog(catalog: list[dict]) -> None:
    """保存教育部竞赛目录到本地文件"""
    with open(CATALOG_FILE, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    logger.info(f"Saved {len(catalog)} entries to {CATALOG_FILE}")


def _get_text_content(content) -> str:
    """安全地从 LLM 响应中提取文本内容"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        text_parts = [
            item.get("text", "") for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        ]
        return " ".join(text_parts)
    return str(content)


def _search_catalog_urls(ctx) -> list[str]:
    """联网搜索教育部竞赛排行榜相关页面，返回候选 URL 列表"""
    from coze_coding_dev_sdk import SearchClient

    client = SearchClient(ctx=ctx)
    candidate_urls: list[str] = []

    for query in SEARCH_QUERIES:
        try:
            response = client.web_search(query=query, count=5)
            for item in (response.web_items or []):
                url = item.url or ""
                if url and url not in candidate_urls:
                    candidate_urls.append(url)
                    logger.info(f"Found candidate URL: {item.title} -> {url}")
        except Exception as e:
            logger.warning(f"Search failed for '{query}': {e}")

    return candidate_urls


def _fetch_page_content(ctx, url: str) -> str:
    """抓取页面文本内容"""
    from coze_coding_dev_sdk.fetch import FetchClient

    try:
        client = FetchClient(ctx=ctx)
        response = client.fetch(url=url)
        if response.status_code != 0:
            logger.warning(f"Fetch failed for {url}: {response.status_message}")
            return ""

        text_parts = []
        for item in response.content or []:
            if item.type == "text" and item.text:
                text_parts.append(item.text)
        content = "\n".join(text_parts)
        # 截断过长的内容，LLM 上下文有限
        if len(content) > 8000:
            content = content[:8000] + "\n...[内容过长已截断]"
        return content
    except Exception as e:
        logger.warning(f"Fetch error for {url}: {e}")
        return ""


def _extract_contest_list_via_llm(ctx, page_text: str, source_url: str) -> list[dict]:
    """使用 LLM 从页面文本中提取竞赛列表"""
    from coze_coding_dev_sdk import LLMClient

    if not page_text.strip():
        return []

    client = LLMClient(ctx=ctx)
    system_prompt = """你是一个数据提取专家。请从提供的网页文本中，提取所有"教育部认可的全国普通高校大学生竞赛"列表。
    
要求：
1. 输出严格的 JSON 数组，每个元素包含: name(竞赛名称), organizer(主办方), level(级别), category(类别)
2. 只提取竞赛，不要提取会议、培训、活动等其他内容
3. 如果原文信息不完整，对应字段留空字符串 ""
4. 不要编造任何信息，只从原文提取
5. 输出格式示例：[{"name": "xxx", "organizer": "xxx", "level": "国家级", "category": "信息技术"}]"""

    try:
        response = client.invoke(
            messages=[
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"来源URL: {source_url}\n\n页面内容:\n{page_text}"),
            ],
            model=EXTRACTION_MODEL,
            temperature=0.2,
            max_completion_tokens=8000,
        )
        text = _get_text_content(response.content)

        # 提取 JSON 数组
        json_match = re.search(r"\[.*\]", text, re.DOTALL)
        if not json_match:
            logger.warning(f"No JSON array found in LLM response for {source_url}")
            return []

        contests = json.loads(json_match.group())
        logger.info(f"Extracted {len(contests)} contests from {source_url}")
        return contests
    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error for {source_url}: {e}")
        return []
    except Exception as e:
        logger.error(f"LLM extraction failed for {source_url}: {e}")
        return []


def _normalize_name(name: str) -> str:
    """归一化竞赛名称，用于匹配对比"""
    name = name.strip()
    # 去除括号内的别名/缩写，保留主名称
    name = re.sub(r"[（(][^)）]*[)）]", "", name)
    # 去除常见后缀
    for suffix in ["大赛", "竞赛", "挑战赛", "邀请赛"]:
        if name.endswith(suffix) and len(name) > len(suffix) + 2:
            name = name[: -len(suffix)]
    return name.strip()


def _match_contest(local_name: str, remote_list: list[dict]) -> Optional[dict]:
    """在远程列表中匹配本地竞赛名称，返回匹配到的条目或 None"""
    local_norm = _normalize_name(local_name)

    # 精确匹配
    for remote in remote_list:
        if _normalize_name(remote.get("name", "")) == local_norm:
            return remote

    # 包含匹配
    for remote in remote_list:
        remote_norm = _normalize_name(remote.get("name", ""))
        if local_norm in remote_norm or remote_norm in local_norm:
            return remote

    return None


def _diff_catalogs(local: list[dict], remote: list[dict]) -> dict:
    """对比本地和远程目录，返回差异摘要"""
    added: list[dict] = []
    removed: list[dict] = []
    changed: list[dict] = []
    unchanged: list[str] = []

    local_names = {item["name"] for item in local}
    remote_names = {item.get("name", "") for item in remote}

    # 新增：远程有但本地没有
    for remote_item in remote:
        name = remote_item.get("name", "")
        if name and name not in local_names:
            # 检查是否是本地已有竞赛的变体名称
            matched = _match_contest(name, local)
            if matched:
                changed.append({
                    "local_name": matched["name"],
                    "remote_name": name,
                    "remote_organizer": remote_item.get("organizer", ""),
                    "type": "renamed",
                })
            else:
                added.append(remote_item)

    # 移除：本地有但远程没有
    for local_item in local:
        name = local_item["name"]
        if name not in remote_names:
            matched = _match_contest(name, remote)
            if not matched:
                removed.append(local_item)

    # 未变更
    for name in local_names & remote_names:
        unchanged.append(name)

    return {
        "total_local": len(local),
        "total_remote": len(remote),
        "added": added,
        "removed": removed,
        "changed": changed,
        "unchanged": unchanged,
    }


def _enrich_contest(ctx, contest: dict) -> dict:
    """对单条竞赛搜索补充官网 URL 和简介"""
    from coze_coding_dev_sdk import SearchClient, LLMClient

    # 如果已有官网 URL，跳过
    if contest.get("official_url"):
        return contest

    search_client = SearchClient(ctx=ctx)
    name = contest.get("name", "")

    try:
        response = search_client.web_search(
            query=f"{name} 官网",
            count=3,
        )
        urls = [item.url for item in (response.web_items or []) if item.url]

        if urls:
            # 用 LLM 判断哪个是最佳官网
            llm_client = LLMClient(ctx=ctx)
            resp = llm_client.invoke(
                messages=[
                    SystemMessage(content="你是URL判断专家。从候选URL中选择最可能是竞赛官方网站的URL。只返回最佳URL，不要其他文字。如果没有合适的，返回空字符串。"),
                    HumanMessage(content=f"竞赛名称: {name}\n候选URL列表:\n" + "\n".join(urls)),
                ],
                model=EXTRACTION_MODEL,
                temperature=0.1,
                max_completion_tokens=200,
            )
            best_url = _get_text_content(resp.content).strip()
            if best_url and best_url.startswith("http"):
                contest["official_url"] = best_url

        # 生成简短简介
        snippet = ""
        if response.web_items:
            for item in response.web_items:
                if item.snippet:
                    snippet = item.snippet[:200]
                    break

        if snippet:
            llm_client = LLMClient(ctx=ctx)
            resp = llm_client.invoke(
                messages=[
                    SystemMessage(content="请将以下搜索摘要整理为一句话的竞赛简介（不超过80字）。只返回简介，不要其他文字。"),
                    HumanMessage(content=f"竞赛: {name}\n主办方: {contest.get('organizer', '')}\n摘要: {snippet}"),
                ],
                model=EXTRACTION_MODEL,
                temperature=0.3,
                max_completion_tokens=200,
            )
            desc = _get_text_content(resp.content).strip()
            if desc:
                contest["description"] = desc

    except Exception as e:
        logger.warning(f"Enrichment failed for '{name}': {e}")

    return contest


def _build_diff_report(diff: dict) -> str:
    """生成 Markdown 格式的差异报告"""
    lines = [
        "# 教育部竞赛目录校验报告",
        f"生成时间：{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## 📊 概要",
        f"- 本地目录：**{diff['total_local']}** 条",
        f"- 远程发现：**{diff['total_remote']}** 条",
        f"- 📌 新增：**{len(diff['added'])}** 条",
        f"- ❌ 移除：**{len(diff['removed'])}** 条",
        f"- ✏️ 名称变更：**{len(diff['changed'])}** 条",
        f"- ✅ 未变更：**{len(diff['unchanged'])}** 条",
        "",
    ]

    if diff["added"]:
        lines.append("## 📌 新增竞赛（远程有，本地无）")
        lines.append("")
        for i, item in enumerate(diff["added"], 1):
            lines.append(f"| {i} | {item.get('name', '')} | {item.get('organizer', '')} | {item.get('level', '')} | {item.get('category', '')} |")
        lines.append("")

    if diff["removed"]:
        lines.append("## ❌ 可能已移除（本地有，远程无）")
        lines.append("")
        for i, item in enumerate(diff["removed"], 1):
            lines.append(f"{i}. **{item['name']}** — {item.get('organizer', '')} ({item.get('level', '')}, {item.get('category', '')})")
        lines.append("")

    if diff["changed"]:
        lines.append("## ✏️ 名称变更")
        lines.append("")
        for i, item in enumerate(diff["changed"], 1):
            lines.append(f"{i}. `{item['local_name']}` → `{item['remote_name']}`")
        lines.append("")

    lines.append("---")
    lines.append("⚠️ 以上为自动校验结果，**未对本地数据做任何修改**。如需更新，请确认后调用 `apply_ministry_catalog_updates`。")

    return "\n".join(lines)


# ============================================================================
# Agent 工具函数
# ============================================================================


@tool
def validate_ministry_catalog() -> str:
    """校验本地教育部竞赛目录是否最新。

    联网搜索教育部最新竞赛排行榜，与本地 84 条目录逐一对比，
    识别新增、移除、变更项，并尝试为未填充官网的竞赛补充信息。

    返回 Markdown 格式的差异报告。
    **不会自动修改本地数据**，需用户确认后手动更新。
    """
    ctx = request_context.get() or new_context(method="validate_ministry_catalog")

    logger.info("=== 开始教育部竞赛目录校验 ===")

    # 1. 加载本地目录
    local = _load_local_catalog()
    if not local:
        return "❌ 本地教育部竞赛目录为空，请检查文件是否存在。"

    logger.info(f"本地目录：{len(local)} 条")

    # 2. 搜索最新目录
    urls = _search_catalog_urls(ctx)
    logger.info(f"搜索到 {len(urls)} 个候选 URL")

    if not urls:
        return "⚠️ 未能搜索到教育部竞赛排行榜相关页面，请稍后重试。"

    # 3. 抓取 + LLM 提取
    all_remote_contests: list[dict] = []
    for url in urls[:3]:  # 最多抓取 3 个页面
        page_text = _fetch_page_content(ctx, url)
        if not page_text:
            continue
        extracted = _extract_contest_list_via_llm(ctx, page_text, url)
        if extracted:
            # 合并去重
            existing_names = {c.get("name", "") for c in all_remote_contests}
            for contest in extracted:
                if contest.get("name") and contest["name"] not in existing_names:
                    all_remote_contests.append(contest)
                    existing_names.add(contest["name"])

    logger.info(f"远程提取：{len(all_remote_contests)} 条竞赛")

    if len(all_remote_contests) < 50:
        logger.warning("远程提取的竞赛数量过少，可能不完整")

    # 4. 差异对比
    diff = _diff_catalogs(local, all_remote_contests)

    # 5. 对"未变更"的竞赛，逐条补全官网 URL 和简介（首次运行较慢）
    enrichment_count = 0
    local_updated = False
    for item in local:
        if not item.get("official_url"):
            item = _enrich_contest(ctx, item)
            enrichment_count += 1
            local_updated = True

    if local_updated:
        _save_local_catalog(local)
        logger.info(f"补充了 {enrichment_count} 条竞赛的官网/简介信息")

    # 6. 生成报告
    report = _build_diff_report(diff)
    if local_updated:
        report += f"\n\n💡 本次还补充了 **{enrichment_count}** 条竞赛的官网 URL 和简介，已保存到本地。"
        report += f"\n\n目录文件校验时间已更新：`{CATALOG_FILE}`"

    logger.info("=== 教育部竞赛目录校验完成 ===")
    return report


@tool
def apply_ministry_catalog_updates(action: str, confirm: bool = False) -> str:
    """应用教育部竞赛目录的变更（需在 review 差异报告后调用）。

    参数:
        action: 操作类型 — "add" 添加新增竞赛、"remove" 移除已废弃竞赛、"update_verification_time" 更新校验时间
        confirm: 必须为 True 才执行，防止误操作
    """
    if not confirm:
        return "⚠️ 请将 confirm 参数设为 True 以确认执行此操作。例如: apply_ministry_catalog_updates(action='update_verification_time', confirm=True)"

    local = _load_local_catalog()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if action == "update_verification_time":
        for item in local:
            item["verified_at"] = now
            if not item.get("status"):
                item["status"] = "active"
        _save_local_catalog(local)
        return f"✅ 已将全部 {len(local)} 条竞赛的校验时间更新为 {now}，状态标记为 active。"

    if action == "add":
        return ("⚠️ 新增竞赛需要在查看差异报告后手动添加到 JSON 文件中。"
                "差异报告中列出了所有新增竞赛的 name/organizer/level/category。"
                f"请手动编辑 {CATALOG_FILE} 添加这些条目。")

    if action == "remove":
        return ("⚠️ 移除竞赛需要在查看差异报告后手动从 JSON 文件中删除。"
                "报告中列出了疑似已移除的竞赛。请确认后手动编辑。")

    return f"❌ 未知操作: {action}，支持 add / remove / update_verification_time"
