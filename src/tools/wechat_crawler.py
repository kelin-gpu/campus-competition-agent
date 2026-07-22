"""
微信公众号文章爬虫模块
功能：
1. 通过搜狗微信搜索抓取目标公众号的文章列表
2. 解析微信文章页（mp.weixin.qq.com）提取正文内容
3. 输出统一的 {title, detail_text, url, publish_time, source_name, author} 格式

技术方案：
- 搜狗微信搜索接口获取文章列表
- 直接解析微信文章页提取正文
- 遵守 robots 协议，请求间隔 >= 5秒
- 异常处理 + 重试机制
"""
import re
import os
import json
import time
import logging
import hashlib
import html
import random
import tempfile
import threading
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import parse_qs, urlencode, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        logger.warning("Invalid %s=%r; using %s", name, os.getenv(name), default)
        return default

# ============================================================
# 配置
# ============================================================

# 核心公众号列表（永远保留，不受动态发现影响）
# 分类：school_level（校级核心）/ college（院系扩展）/ dynamic（动态发现）
CORE_ACCOUNTS = [
    # ====== 校级核心（8个）======
    {"name": "南京大学", "biz": "", "desc": "南京大学官方公众号", "is_core": True, "category": "school_level"},
    {"name": "南京大学团委", "biz": "", "desc": "共青团南京大学委员会", "is_core": True, "category": "school_level"},
    {"name": "南京大学学生会", "biz": "", "desc": "南京大学学生会", "is_core": True, "category": "school_level"},
    {"name": "南京大学教务处", "biz": "", "desc": "南京大学教务处", "is_core": True, "category": "school_level"},
    {"name": "南京大学研究生院", "biz": "", "desc": "南京大学研究生院", "is_core": True, "category": "school_level"},
    {"name": "南京大学就业创业指导中心", "biz": "", "desc": "就业创业指导", "is_core": True, "category": "school_level"},
    {"name": "南京大学健雄书院", "biz": "", "desc": "健雄书院", "is_core": True, "category": "school_level"},
    {"name": "南大育教", "biz": "", "desc": "南京大学党委学生工作部/本科生院官方号", "is_core": True, "category": "school_level"},
    # ====== 院系扩展（49个）======
    {"name": "南京大学新生学院", "biz": "", "desc": "新生学院", "is_core": True, "category": "college"},
    {"name": "南京大学文学院", "biz": "", "desc": "文学院", "is_core": True, "category": "college"},
    {"name": "南京大学历史学院", "biz": "", "desc": "历史学院", "is_core": True, "category": "college"},
    {"name": "南京大学哲学学院", "biz": "", "desc": "哲学学院", "is_core": True, "category": "college"},
    {"name": "南京大学新闻传播学院", "biz": "", "desc": "新闻传播学院", "is_core": True, "category": "college"},
    {"name": "南京大学法学院", "biz": "", "desc": "法学院", "is_core": True, "category": "college"},
    {"name": "南京大学商学院", "biz": "", "desc": "商学院", "is_core": True, "category": "college"},
    {"name": "南京大学经济学院", "biz": "", "desc": "经济学院", "is_core": True, "category": "college"},
    {"name": "南京大学管理学院", "biz": "", "desc": "管理学院", "is_core": True, "category": "college"},
    {"name": "南京大学外国语学院", "biz": "", "desc": "外国语学院", "is_core": True, "category": "college"},
    {"name": "南京大学政府管理学院", "biz": "", "desc": "政府管理学院", "is_core": True, "category": "college"},
    {"name": "南京大学国际关系学院", "biz": "", "desc": "国际关系学院", "is_core": True, "category": "college"},
    {"name": "南京大学信息管理学院", "biz": "", "desc": "信息管理学院", "is_core": True, "category": "college"},
    {"name": "南京大学社会学院", "biz": "", "desc": "社会学院", "is_core": True, "category": "college"},
    {"name": "南京大学数学学院", "biz": "", "desc": "数学学院", "is_core": True, "category": "college"},
    {"name": "南京大学物理学院", "biz": "", "desc": "物理学院", "is_core": True, "category": "college"},
    {"name": "南京大学天文与空间科学学院", "biz": "", "desc": "天文与空间科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学化学学院", "biz": "", "desc": "化学学院", "is_core": True, "category": "college"},
    {"name": "南京大学化工学院", "biz": "", "desc": "化工学院", "is_core": True, "category": "college"},
    {"name": "南京大学计算机学院", "biz": "", "desc": "计算机学院", "is_core": True, "category": "college"},
    {"name": "南京大学软件学院", "biz": "", "desc": "软件学院", "is_core": True, "category": "college"},
    {"name": "南京大学人工智能学院", "biz": "", "desc": "人工智能学院", "is_core": True, "category": "college"},
    {"name": "NJUAI团学联", "biz": "", "desc": "人工智能学院团委学生联合会", "is_core": True, "category": "college"},
    {"name": "南京大学电子科学与工程学院", "biz": "", "desc": "电子科学与工程学院", "is_core": True, "category": "college"},
    {"name": "南京大学现代工程与应用科学学院", "biz": "", "desc": "现代工程与应用科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学环境学院", "biz": "", "desc": "环境学院", "is_core": True, "category": "college"},
    {"name": "南京大学地球科学与工程学院", "biz": "", "desc": "地球科学与工程学院", "is_core": True, "category": "college"},
    {"name": "南京大学地理与海洋科学学院", "biz": "", "desc": "地理与海洋科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学大气科学学院", "biz": "", "desc": "大气科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学南京赫尔辛基大气与地球系统科学学院", "biz": "", "desc": "南赫学院", "is_core": True, "category": "college"},
    {"name": "南京大学生命科学学院", "biz": "", "desc": "生命科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学医学院", "biz": "", "desc": "医学院", "is_core": True, "category": "college"},
    {"name": "南京大学工程管理学院", "biz": "", "desc": "工程管理学院", "is_core": True, "category": "college"},
    {"name": "南京大学匡亚明学院", "biz": "", "desc": "匡亚明学院", "is_core": True, "category": "college"},
    {"name": "南京大学海外教育学院", "biz": "", "desc": "海外教育学院", "is_core": True, "category": "college"},
    {"name": "南京大学建筑与城市规划学院", "biz": "", "desc": "建筑与城市规划学院", "is_core": True, "category": "college"},
    {"name": "南京大学马克思主义学院", "biz": "", "desc": "马克思主义学院", "is_core": True, "category": "college"},
    {"name": "南京大学艺术学院", "biz": "", "desc": "艺术学院", "is_core": True, "category": "college"},
    {"name": "南京大学智能科学与技术学院", "biz": "", "desc": "智能科学与技术学院", "is_core": True, "category": "college"},
    {"name": "南京大学智能软件与工程学院", "biz": "", "desc": "智能软件与工程学院", "is_core": True, "category": "college"},
    {"name": "南京大学集成电路学院", "biz": "", "desc": "集成电路学院", "is_core": True, "category": "college"},
    {"name": "南京大学数字经济与管理学院", "biz": "", "desc": "数字经济与管理学院", "is_core": True, "category": "college"},
    {"name": "南京大学能源与资源学院", "biz": "", "desc": "能源与资源学院", "is_core": True, "category": "college"},
    {"name": "南京大学国家卓越工程师学院", "biz": "", "desc": "国家卓越工程师学院", "is_core": True, "category": "college"},
    {"name": "南京大学机器人与自动化学院", "biz": "", "desc": "机器人与自动化学院", "is_core": True, "category": "college"},
    {"name": "南京大学未来技术学院", "biz": "", "desc": "未来技术学院", "is_core": True, "category": "college"},
    {"name": "南京大学前沿科学学院", "biz": "", "desc": "前沿科学学院", "is_core": True, "category": "college"},
    {"name": "南京大学先进制造学院", "biz": "", "desc": "先进制造学院", "is_core": True, "category": "college"},
    {"name": "南京大学生物医学工程学院", "biz": "", "desc": "生物医学工程学院", "is_core": True, "category": "college"},
]

# 向后兼容
WECHAT_ACCOUNTS = CORE_ACCOUNTS

# 动态发现的公众号缓存文件路径
CACHE_DIR = os.path.join(os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects"), "assets", "cache")
ACCOUNTS_CACHE_FILE = os.path.join(CACHE_DIR, "wechat_accounts_cache.json")
STATE_FILE = os.getenv(
    "WECHAT_STATE_FILE",
    os.path.join(CACHE_DIR, "wechat_crawl_state.json"),
)

# 缓存有效期（天）
CACHE_TTL_DAYS = 7

# 动态发现公众号最大数量
MAX_DISCOVERED_ACCOUNTS = 50

# 请求间隔（秒），遵守 robots 协议
REQUEST_INTERVAL = 5

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 最大重试次数
MAX_RETRIES = 3

INCREMENTAL_OVERLAP_HOURS = _env_int("WECHAT_INCREMENTAL_OVERLAP_HOURS", 24)
SEARCH_MAX_PAGES = _env_int("WECHAT_SEARCH_MAX_PAGES", 1, minimum=1)
FULL_SEARCH_MAX_PAGES = 3
STATE_VERSION = 1
PROCESSED_TTL_DAYS = 90
FAILURE_TTL_DAYS = 14
MAX_PROCESSED = 5000
MAX_FAILURES = 500
SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")

# 内容过滤关键词（标题或正文包含任一即保留）
RELEVANCE_KEYWORDS = [
    "竞赛", "比赛", "活动", "讲座", "报名", "通知", "选拔",
    "挑战杯", "互联网+", "数学建模", "程序设计", "创新创业",
    "五育", "综测", "保研", "奖学金", "实习", "招聘",
    "论坛", "沙龙", "工作坊", "训练营", "夏令营",
    "ACM", "ICPC", "CTF", "蓝桥杯", "建模",
    "电子设计", "机器人", "人工智能", "大数据",
    "deadline", "截止", "征稿", "征集",
]

# User-Agent 轮换
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
]

# 搜狗微信搜索基础 URL
SOGOU_WEIXIN_URL = "https://weixin.sogou.com/weixin"

# 请求 session（复用连接）
_session = None
_request_lock = threading.Lock()
_state_lock = threading.RLock()
_last_request_at = 0.0


def _get_session() -> requests.Session:
    """获取或创建 HTTP session"""
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
            "User-Agent": random.choice(USER_AGENTS),
        })
    return _session


def _rate_limit():
    """Apply one process-wide polite request interval."""
    global _last_request_at
    with _request_lock:
        elapsed = time.monotonic() - _last_request_at
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_request_at = time.monotonic()


def _looks_blocked(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in (
        "antispider", "请输入验证码", "访问过于频繁", "异常访问", "verifycode",
    ))


def _request_with_retry(url: str, params: dict = None, max_retries: int = MAX_RETRIES) -> Optional[requests.Response]:
    """带重试的 HTTP 请求"""
    session = _get_session()
    for attempt in range(max_retries):
        try:
            _rate_limit()
            resp = session.get(url, params=params, timeout=(5, REQUEST_TIMEOUT))
            if resp.status_code == 429 or 500 <= resp.status_code < 600:
                retry_after = resp.headers.get("Retry-After", "")
                try:
                    delay = float(retry_after)
                except (TypeError, ValueError):
                    delay = REQUEST_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
                if attempt < max_retries - 1:
                    time.sleep(delay)
                    continue
            resp.raise_for_status()
            if _looks_blocked(resp.text):
                raise requests.RequestException("soft block or captcha page")
            return resp
        except requests.RequestException as e:
            logger.warning(f"Request failed (attempt {attempt+1}/{max_retries}): {url} -> {e}")
            if attempt < max_retries - 1:
                wait_time = REQUEST_INTERVAL * (2 ** attempt) + random.uniform(0, 1)
                time.sleep(wait_time)
    logger.error(f"All {max_retries} attempts failed for: {url}")
    return None


def _empty_state() -> dict:
    return {
        "version": STATE_VERSION,
        "updated_at": None,
        "accounts": {},
        "processed": {},
        "failures": [],
    }


def _parse_iso(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=SHANGHAI_TZ)
        return parsed.astimezone(SHANGHAI_TZ)
    except (TypeError, ValueError):
        return None


def _prune_state(state: dict, now: Optional[datetime] = None) -> dict:
    now = (now or datetime.now(SHANGHAI_TZ)).astimezone(SHANGHAI_TZ)
    processed_cutoff = now - timedelta(days=PROCESSED_TTL_DAYS)
    failure_cutoff = now - timedelta(days=FAILURE_TTL_DAYS)

    processed = []
    for key, value in (state.get("processed") or {}).items():
        processed_at = _parse_iso(value)
        if processed_at and processed_at >= processed_cutoff:
            processed.append((key, processed_at.isoformat()))
    processed.sort(key=lambda item: item[1], reverse=True)
    state["processed"] = dict(processed[:MAX_PROCESSED])

    failures = []
    for item in state.get("failures") or []:
        seen_at = _parse_iso(item.get("last_seen_at", ""))
        if seen_at and seen_at >= failure_cutoff:
            failures.append(item)
    failures.sort(key=lambda item: item.get("last_seen_at", ""), reverse=True)
    state["failures"] = failures[:MAX_FAILURES]
    return state


def _load_crawl_state() -> dict:
    with _state_lock:
        if not os.path.exists(STATE_FILE):
            return _empty_state()
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as handle:
                state = json.load(handle)
            if state.get("version") != STATE_VERSION:
                logger.warning("Unsupported WeChat crawl state version; starting clean")
                return _empty_state()
            return _prune_state(state)
        except Exception as exc:
            logger.warning("Failed to load WeChat crawl state: %s", exc)
            return _empty_state()


def _save_crawl_state(state: dict):
    with _state_lock:
        state = _prune_state(state)
        state["version"] = STATE_VERSION
        state["updated_at"] = datetime.now(SHANGHAI_TZ).isoformat()
        directory = os.path.dirname(STATE_FILE) or "."
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="wechat-state-", suffix=".json", dir=directory)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, STATE_FILE)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def _upsert_failure(state: dict, item: dict, stage: str, reason: str):
    key = item.get("source_article_id") or item.get("url") or item.get("title") or reason
    now = datetime.now(SHANGHAI_TZ).isoformat()
    existing = next((entry for entry in state["failures"] if entry.get("key") == key and entry.get("stage") == stage), None)
    payload = {
        "key": key,
        "target_account": item.get("target_account", ""),
        "source_name": item.get("source_name", ""),
        "title": item.get("title", ""),
        "url": item.get("url", ""),
        "stage": stage,
        "reason": reason,
        "attempts": 1,
        "first_seen_at": now,
        "last_seen_at": now,
    }
    if existing:
        payload["attempts"] = int(existing.get("attempts", 0)) + 1
        payload["first_seen_at"] = existing.get("first_seen_at", now)
        state["failures"].remove(existing)
    state["failures"].append(payload)


def _record_failure(item: dict, stage: str, reason: str):
    with _state_lock:
        state = _load_crawl_state()
        _upsert_failure(state, item, stage, reason)
        _save_crawl_state(state)


def mark_wechat_articles_processed(items: list):
    """Acknowledge terminally handled articles and advance per-account cursors."""
    if not items:
        return
    with _state_lock:
        state = _load_crawl_state()
        now = datetime.now(SHANGHAI_TZ).isoformat()
        acknowledged = set()
        for item in items:
            article_id = item.get("source_article_id") or item.get("_wechat_id")
            if not article_id:
                continue
            acknowledged.add(article_id)
            state["processed"][article_id] = now
            candidate_id = item.get("candidate_article_id")
            if candidate_id:
                acknowledged.add(candidate_id)
                state["processed"][candidate_id] = now
            account = _normalize_account_name(item.get("target_account") or item.get("source_name", ""))
            published = _parse_iso(item.get("publish_time", ""))
            if account:
                current = state["accounts"].setdefault(account, {})
                current["last_success_at"] = now
                if published:
                    old = _parse_iso(current.get("last_publish_time", ""))
                    if old is None or published > old:
                        current["last_publish_time"] = published.isoformat()
        if acknowledged:
            state["failures"] = [entry for entry in state["failures"] if entry.get("key") not in acknowledged]
            _save_crawl_state(state)


# ============================================================
# 公众号动态发现 + 缓存机制
# ============================================================

def _normalize_account_name(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").strip().lower()
    return re.sub(r"[\s·•_\-—（）()]+", "", value)


def _account_matches(actual: str, account: dict) -> bool:
    actual_norm = _normalize_account_name(actual)
    allowed = [account.get("name", ""), *(account.get("aliases") or [])]
    return bool(actual_norm) and actual_norm in {_normalize_account_name(name) for name in allowed if name}

def _load_accounts_cache() -> Optional[dict]:
    """加载公众号列表缓存"""
    try:
        if not os.path.exists(ACCOUNTS_CACHE_FILE):
            return None
        with open(ACCOUNTS_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
        # 检查缓存是否过期
        cached_time = datetime.fromisoformat(cache.get("timestamp", "2000-01-01"))
        if datetime.now() - cached_time > timedelta(days=CACHE_TTL_DAYS):
            logger.info("Accounts cache expired, will refresh")
            return None
        logger.info(f"Loaded {len(cache.get('accounts', []))} accounts from cache")
        return cache
    except Exception as e:
        logger.warning(f"Failed to load accounts cache: {e}")
        return None


def _save_accounts_cache(accounts: list):
    """保存公众号列表到缓存"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        cache = {
            "timestamp": datetime.now().isoformat(),
            "accounts": accounts,
        }
        with open(ACCOUNTS_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        logger.info(f"Saved {len(accounts)} accounts to cache")
    except Exception as e:
        logger.warning(f"Failed to save accounts cache: {e}")


def discover_nju_accounts(max_pages: int = 5, max_accounts: int = MAX_DISCOVERED_ACCOUNTS) -> list:
    """
    通过搜狗微信搜索发现所有名称含"南京大学"的公众号

    Args:
        max_pages: 最大搜索页数
        max_accounts: 最大公众号数量（上限保护）

    Returns:
        公众号列表 [{"name": str, "desc": str, "is_core": False}, ...]
    """
    logger.info(f"Discovering NJU WeChat accounts (max_pages={max_pages}, max_accounts={max_accounts})...")

    discovered = {}  # name -> account dict, 用于去重

    for page in range(1, max_pages + 1):
        if page > 1:
            time.sleep(REQUEST_INTERVAL)

        params = {
            "type": "1",  # 1=搜公众号
            "query": "南京大学",
            "page": page,
            "ie": "utf8",
        }

        resp = _request_with_retry(SOGOU_WEIXIN_URL, params=params)
        if resp is None:
            logger.warning(f"Failed to fetch page {page} for account discovery")
            continue

        try:
            soup = BeautifulSoup(resp.text, "lxml")

            # 搜狗公众号搜索结果解析
            for item in soup.select("ul.news-list2 > li") or soup.select("div.gzh-box2"):
                name_tag = item.select_one("p.tit a") or item.select_one("a[data-z]")
                if not name_tag:
                    continue

                name = name_tag.get_text(strip=True)
                if not name or "南京大学" not in name:
                    continue

                # 跳过已在核心列表中的
                if name in {acc["name"] for acc in CORE_ACCOUNTS}:
                    continue

                # 去重
                if name in discovered:
                    continue

                desc_tag = item.select_one("dl:nth-child(3) dd") or item.select_one("span.sp-txt")
                desc = desc_tag.get_text(strip=True)[:50] if desc_tag else ""

                discovered[name] = {
                    "name": name,
                    "desc": desc or f"动态发现: {name}",
                    "is_core": False,
                }

                if len(discovered) >= max_accounts:
                    logger.info(f"Reached max_accounts limit ({max_accounts})")
                    break

            logger.info(f"Page {page}: found {len(discovered)} unique NJU accounts so far")

        except Exception as e:
            logger.error(f"Failed to parse account discovery page {page}: {e}")
            continue

    result = list(discovered.values())
    logger.info(f"Discovered {len(result)} new NJU accounts (excluding core {len(CORE_ACCOUNTS)})")
    return result


def get_all_accounts(force_refresh: bool = False) -> list:
    """
    获取完整的公众号列表（核心 + 动态发现）

    Args:
        force_refresh: 是否强制刷新缓存

    Returns:
        合并去重后的公众号列表，核心公众号排在前面
    """
    # 1. 核心公众号永远保留
    all_accounts = [dict(acc) for acc in CORE_ACCOUNTS]
    core_names = {acc["name"] for acc in CORE_ACCOUNTS}

    # 2. 尝试从缓存加载动态发现的公众号
    discovered = []
    if not force_refresh:
        cache = _load_accounts_cache()
        if cache:
            discovered = cache.get("accounts", [])

    # 3. 缓存不存在或已过期，重新搜索
    if not discovered or force_refresh:
        discovered = discover_nju_accounts()
        if discovered:
            _save_accounts_cache(discovered)

    # 4. 合并去重（核心优先）
    seen_names = set(core_names)
    for acc in discovered:
        name = acc.get("name", "")
        if name and name not in seen_names:
            seen_names.add(name)
            all_accounts.append({
                "name": name,
                "desc": acc.get("desc", ""),
                "is_core": False,
                "category": "dynamic",
            })

    # 5. 排序：校级核心 -> 院系扩展 -> 动态发现，各自按名称排序
    school_level = sorted(
        [a for a in all_accounts if a.get("category") == "school_level"],
        key=lambda x: x["name"],
    )
    college = sorted(
        [a for a in all_accounts if a.get("category") == "college"],
        key=lambda x: x["name"],
    )
    dynamic_list = sorted(
        [a for a in all_accounts if a.get("category") == "dynamic"],
        key=lambda x: x["name"],
    )

    return school_level + college + dynamic_list


# ============================================================
# 搜狗微信搜索 - 获取文章列表
# ============================================================

def _timestamp_to_iso(timestamp: str) -> str:
    try:
        return datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(SHANGHAI_TZ).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def _parse_sogou_article_results(html_text: str, account_name: str) -> list:
    soup = BeautifulSoup(html_text, "lxml")
    articles = []
    for item in soup.select("ul.news-list > li"):
        title_tag = item.select_one("h3 a")
        if not title_tag:
            continue
        title = title_tag.get_text("", strip=True)
        url = str(title_tag.get("href", ""))
        if url and not url.startswith("http"):
            url = urljoin("https://weixin.sogou.com", url)
        summary_tag = item.select_one("p.txt-info")
        publisher_tag = item.select_one("span.all-time-y2")
        time_tag = item.select_one("span.s2")
        time_text = time_tag.get_text(" ", strip=True) if time_tag else ""
        time_script = time_tag.find("script") if time_tag else None
        script_text = time_script.get_text(" ", strip=True) if time_script else ""
        match = re.search(r"timeConvert\(['\"]?(\d+)['\"]?\)", script_text)
        publish_time = _timestamp_to_iso(match.group(1)) if match else time_text
        articles.append({
            "title": title,
            "url": url,
            "sogou_url": url,
            "summary": summary_tag.get_text(" ", strip=True) if summary_tag else "",
            "publish_time": publish_time,
            "source_name": publisher_tag.get_text(" ", strip=True) if publisher_tag else "",
            "target_account": account_name,
        })
    return articles


def search_wechat_articles(account_name: str, page: int = 1) -> list:
    """
    通过搜狗微信搜索获取指定公众号的文章列表

    Args:
        account_name: 公众号名称
        page: 页码（从1开始）

    Returns:
        文章列表 [{"title": str, "url": str, "summary": str, "publish_time": str, "source_name": str}, ...]
    """
    params = {
        "type": "2",  # 2=搜文章, 1=搜公众号
        "query": account_name,
        "page": page,
        "ie": "utf8",
    }

    resp = _request_with_retry(SOGOU_WEIXIN_URL, params=params)
    if resp is None:
        return []

    try:
        articles = _parse_sogou_article_results(resp.text, account_name)
        logger.info(f"Found {len(articles)} articles for '{account_name}' (page {page})")
        return articles

    except Exception as e:
        logger.error(f"Failed to parse search results for '{account_name}': {e}")
        return []


def search_all_accounts(page: int = 1, max_pages: Optional[int] = None) -> list:
    """搜索所有目标公众号的文章（核心 + 动态发现）。"""
    all_accounts = get_all_accounts()
    all_articles = []
    max_pages = max_pages or page
    for i, account in enumerate(all_accounts):
        count = 0
        for page_number in range(page, max_pages + 1):
            articles = search_wechat_articles(account["name"], page=page_number)
            for article in articles:
                article["target_account"] = account["name"]
                article["target_aliases"] = account.get("aliases", [])
            all_articles.extend(articles)
            count += len(articles)
            if not articles:
                break
        logger.info(f"[{i+1}/{len(all_accounts)}] {account['name']}: {count} articles")
    return all_articles


# ============================================================
# 微信文章页解析 - 提取正文
# ============================================================

def _is_wechat_article_url(url: str) -> bool:
    parsed = urlparse(url or "")
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "mp.weixin.qq.com"


def _decode_js_string(value: str) -> str:
    try:
        return bytes(value, "utf-8").decode("unicode_escape") if "\\" in value else value
    except UnicodeDecodeError:
        return value


def _resolve_sogou_redirect_html(html_text: str) -> Optional[str]:
    """Resolve Sogou's string-fragment trampoline without executing JavaScript."""
    fragments = re.findall(r"url\s*\+=\s*(['\"])(.*?)\1\s*;", html_text or "", flags=re.DOTALL)
    if not fragments:
        return None
    resolved = "".join(_decode_js_string(value) for _, value in fragments)
    # Do not use html.unescape here: it treats the valid URL prefix
    # ``&timestamp`` as the legacy entity ``&times`` and corrupts the URL.
    resolved = (
        resolved.replace("&amp;", "&")
        .replace("&#38;", "&")
        .replace("&#x26;", "&")
        .replace("&#X26;", "&")
        .replace("@", "")
    )
    return resolved if _is_wechat_article_url(resolved) else None


def _extract_script_value(text: str, names: tuple[str, ...]) -> str:
    for name in names:
        patterns = (
            rf"(?:var\s+)?{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]",
            rf"['\"]{re.escape(name)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        )
        for pattern in patterns:
            match = re.search(pattern, text or "")
            if match:
                return html.unescape(match.group(1))
    return ""


def _extract_identifier(text: str, names: tuple[str, ...], pattern: str) -> str:
    """Return the first script value that satisfies an identifier grammar."""
    for name in names:
        expressions = (
            rf"(?:var\s+)?{re.escape(name)}\s*=\s*['\"]([^'\"]+)['\"]",
            rf"['\"]{re.escape(name)}['\"]\s*:\s*['\"]([^'\"]+)['\"]",
        )
        for expression in expressions:
            for value in re.findall(expression, text or ""):
                value = html.unescape(value).strip()
                if re.fullmatch(pattern, value):
                    return value
    return ""


def _canonicalize_wechat_url(url: str, identifiers: Optional[dict] = None) -> str:
    if not _is_wechat_article_url(url):
        return ""
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    identifiers = identifiers or {}
    biz = identifiers.get("biz") or (query.get("__biz") or [""])[0]
    mid = identifiers.get("mid") or (query.get("mid") or [""])[0]
    idx = identifiers.get("idx") or (query.get("idx") or [""])[0]
    sn = identifiers.get("sn") or (query.get("sn") or [""])[0]
    stable = {key: value for key, value in (("__biz", biz), ("mid", mid), ("idx", idx), ("sn", sn)) if value}
    if stable:
        return urlunparse(("https", "mp.weixin.qq.com", "/s", "", urlencode(stable), ""))
    return urlunparse(("https", "mp.weixin.qq.com", parsed.path or "/s", "", parsed.query, ""))


def _build_article_key(publisher: str, publish_time: str, title: str, identifiers: Optional[dict] = None) -> str:
    identifiers = identifiers or {}
    if identifiers.get("biz") and identifiers.get("mid") and identifiers.get("idx"):
        identity = f"{identifiers['biz']}:{identifiers['mid']}:{identifiers['idx']}"
    else:
        parsed_time = _parse_iso(publish_time)
        identity = "|".join((
            _normalize_account_name(publisher),
            parsed_time.isoformat() if parsed_time else str(publish_time or ""),
            unicodedata.normalize("NFKC", title or "").strip().lower(),
        ))
    return "WX-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24].upper()


def fetch_wechat_article(url: str) -> Optional[dict]:
    """
    解析微信文章页，提取完整正文

    Args:
        url: 微信文章 URL（mp.weixin.qq.com/s/...）

    Returns:
        {"title": str, "detail_text": str, "author": str, "publish_time": str} 或 None
    """
    if not url:
        return None

    resp = _request_with_retry(url)
    if resp is None:
        return None

    resolved_url = resp.url
    if not _is_wechat_article_url(resolved_url):
        resolved_url = _resolve_sogou_redirect_html(resp.text) or ""
        if not resolved_url:
            logger.warning("Unable to resolve safe WeChat redirect: %s", url)
            return None
        resp = _request_with_retry(resolved_url)
        if resp is None:
            return None

    try:
        soup = BeautifulSoup(resp.text, "lxml")

        # 提取标题
        title_tag = soup.select_one("h1#activity-name") or soup.select_one("h1.rich_media_title")
        title_meta = soup.select_one('meta[property="og:title"]')
        title = title_tag.get_text(strip=True) if title_tag else str(title_meta.get("content", "")).strip() if title_meta else ""

        # 提取作者/公众号
        author_tag = soup.select_one("a#js_name") or soup.select_one("span.rich_media_meta_nickname")
        author = author_tag.get_text(strip=True) if author_tag else _extract_script_value(resp.text, ("nickname", "nick_name"))

        # 提取发布时间
        publish_time = ""
        # 微信文章页的发布时间通常在 script 中
        timestamp = _extract_identifier(resp.text, ("ct", "publish_time"), r"\d{9,12}")
        if timestamp:
            publish_time = _timestamp_to_iso(timestamp)

        # 提取正文
        content_tag = soup.select_one("div#js_content") or soup.select_one("div.rich_media_content")
        if not content_tag:
            logger.warning(f"No content found for: {url}")
            return None

        # 清理正文：移除 script/style，提取纯文本
        for tag in content_tag.select("script, style"):
            tag.decompose()

        detail_text = content_tag.get_text(separator="\n", strip=True)

        # 清理多余空行
        detail_text = re.sub(r'\n{3,}', '\n\n', detail_text)

        if not title and not detail_text:
            return None

        identifiers = {
            "biz": _extract_identifier(resp.text, ("biz", "__biz"), r"[A-Za-z0-9_=-]{6,}"),
            "mid": _extract_identifier(resp.text, ("mid", "appmsgid"), r"\d+"),
            "idx": _extract_identifier(resp.text, ("idx", "itemidx"), r"\d+"),
            "sn": _extract_identifier(resp.text, ("sn",), r"[A-Fa-f0-9]{16,64}"),
        }
        query = parse_qs(urlparse(resp.url).query)
        for key, query_key in (("biz", "__biz"), ("mid", "mid"), ("idx", "idx"), ("sn", "sn")):
            if not identifiers[key]:
                identifiers[key] = (query.get(query_key) or [""])[0]
        canonical_url = _canonicalize_wechat_url(resp.url, identifiers)
        source_article_id = _build_article_key(author, publish_time, title, identifiers)

        return {
            "title": title,
            "detail_text": detail_text,
            "author": author,
            "publish_time": publish_time,
            "canonical_url": canonical_url,
            "url": canonical_url,
            "source_article_id": source_article_id,
            "wechat_identifiers": identifiers,
        }

    except Exception as e:
        logger.error(f"Failed to parse article: {url} -> {e}")
        return None


# ============================================================
# 内容过滤
# ============================================================

def is_relevant(title: str, text: str = "") -> bool:
    """
    判断文章是否与竞赛/活动相关

    Args:
        title: 文章标题
        text: 文章正文（可选，用于辅助判断）

    Returns:
        True 如果相关
    """
    combined = (title + " " + text[:5000]).lower()
    for keyword in RELEVANCE_KEYWORDS:
        if keyword.lower() in combined:
            return True
    return False


# ============================================================
# 完整抓取流程
# ============================================================

def crawl_wechat_events(hours: int = 24) -> list:
    """
    完整抓取流程：搜索 -> 过滤 -> 解析正文

    Args:
        hours: 只返回过去 N 小时内的文章（0=不过滤时间）

    Returns:
        标准格式文章列表 [{"title", "detail_text", "url", "publish_time", "source_name", "author"}, ...]
    """
    logger.info("Starting WeChat crawl (last %sh)...", hours)
    state = _load_crawl_state()
    now = datetime.now(SHANGHAI_TZ)
    cutoff = None if hours == 0 else now - timedelta(hours=max(0, hours) + INCREMENTAL_OVERLAP_HOURS)
    max_pages = FULL_SEARCH_MAX_PAGES if hours == 0 else SEARCH_MAX_PAGES
    articles = search_all_accounts(page=1, max_pages=max_pages)
    accounts = {_normalize_account_name(item["name"]): item for item in get_all_accounts()}
    stats = {
        "search_results": len(articles),
        "publisher_verified": 0,
        "out_of_window": 0,
        "quarantined": 0,
        "redirect_resolved": 0,
        "body_fetched": 0,
        "relevant": 0,
        "processed_skipped": 0,
        "failed": 0,
        "failures_by_stage": {},
    }
    results = []
    seen_candidates = set()
    pending_failures = []

    for article in articles:
        target_name = article.get("target_account", "")
        account = accounts.get(_normalize_account_name(target_name), {
            "name": target_name,
            "aliases": article.get("target_aliases", []),
        })
        account_key = _normalize_account_name(target_name)
        account_cursor = _parse_iso((state.get("accounts", {}).get(account_key) or {}).get("last_publish_time", ""))
        account_cutoff = cutoff
        if cutoff and account_cursor:
            account_cutoff = min(cutoff, account_cursor - timedelta(hours=INCREMENTAL_OVERLAP_HOURS))
        candidate_id = _build_article_key(
            article.get("source_name", ""),
            article.get("publish_time", ""),
            article.get("title", ""),
        )
        article["source_article_id"] = candidate_id
        if not _account_matches(article.get("source_name", ""), account):
            pending_failures.append((dict(article), "publisher_verification", "publisher_mismatch_or_missing"))
            stats["quarantined"] += 1
            stats["failures_by_stage"]["publisher_verification"] = stats["failures_by_stage"].get("publisher_verification", 0) + 1
            continue
        stats["publisher_verified"] += 1

        publish_time = _parse_iso(article.get("publish_time", ""))
        if account_cutoff and publish_time and publish_time < account_cutoff:
            stats["out_of_window"] += 1
            continue

        if candidate_id in seen_candidates or candidate_id in state.get("processed", {}):
            stats["processed_skipped"] += 1
            continue
        seen_candidates.add(candidate_id)

        detail = fetch_wechat_article(article.get("url", ""))
        if detail is None:
            pending_failures.append((dict(article), "article_fetch", "redirect_or_body_unavailable"))
            stats["failed"] += 1
            stats["failures_by_stage"]["article_fetch"] = stats["failures_by_stage"].get("article_fetch", 0) + 1
            continue
        stats["redirect_resolved"] += 1
        stats["body_fetched"] += 1

        actual_publisher = detail.get("author", "") or article.get("source_name", "")
        if not _account_matches(actual_publisher, account):
            failed = {**article, **detail, "source_name": actual_publisher}
            pending_failures.append((failed, "publisher_verification", "article_publisher_mismatch"))
            stats["quarantined"] += 1
            stats["failures_by_stage"]["publisher_verification"] = stats["failures_by_stage"].get("publisher_verification", 0) + 1
            continue

        effective_time = detail.get("publish_time") or article.get("publish_time", "")
        effective_datetime = _parse_iso(effective_time)
        if account_cutoff and (effective_datetime is None or effective_datetime < account_cutoff):
            if effective_datetime is None:
                failed = {**article, **detail, "source_name": actual_publisher}
                pending_failures.append((failed, "publish_time", "missing_or_invalid_publish_time"))
                stats["quarantined"] += 1
                stats["failures_by_stage"]["publish_time"] = stats["failures_by_stage"].get("publish_time", 0) + 1
            else:
                stats["out_of_window"] += 1
            continue

        title = detail.get("title") or article.get("title", "")
        detail_text = detail.get("detail_text", "")
        searchable = " ".join((article.get("summary", ""), detail_text))
        if not is_relevant(title, searchable):
            continue

        source_article_id = detail.get("source_article_id") or candidate_id
        if source_article_id in state.get("processed", {}) or source_article_id in {item["source_article_id"] for item in results}:
            stats["processed_skipped"] += 1
            continue
        canonical_url = detail.get("canonical_url", "")
        results.append({
            "title": title,
            "detail_text": detail_text,
            "url": canonical_url,
            "canonical_url": canonical_url,
            "publish_time": effective_time,
            "source_name": actual_publisher,
            "author": actual_publisher,
            "source_article_id": source_article_id,
            "candidate_article_id": candidate_id,
            "target_account": target_name,
            "_wechat_id": source_article_id,
        })
        stats["relevant"] += 1

    if pending_failures:
        with _state_lock:
            latest_state = _load_crawl_state()
            for failed_item, stage, reason in pending_failures:
                _upsert_failure(latest_state, failed_item, stage, reason)
            _save_crawl_state(latest_state)
    logger.info("WeChat crawl stats: %s", json.dumps(stats, ensure_ascii=False, sort_keys=True))
    return results


def get_wechat_accounts() -> list:
    """获取当前监控的公众号列表，含分类信息"""
    all_accounts = get_all_accounts()
    return [
        {
            "name": acc["name"],
            "desc": acc.get("desc", ""),
            "is_core": acc.get("is_core", False),
            "category": acc.get("category", "dynamic"),
            "aliases": acc.get("aliases", []),
        }
        for acc in all_accounts
    ]


def refresh_wechat_accounts() -> dict:
    """
    强制刷新公众号列表（绕过缓存）

    Returns:
        {"school_level_count": int, "college_count": int, "dynamic_count": int, "total": int, "accounts": list}
    """
    all_accounts = get_all_accounts(force_refresh=True)
    school_level = [a for a in all_accounts if a.get("category") == "school_level"]
    college = [a for a in all_accounts if a.get("category") == "college"]
    dynamic = [a for a in all_accounts if a.get("category") == "dynamic"]
    return {
        "school_level_count": len(school_level),
        "college_count": len(college),
        "dynamic_count": len(dynamic),
        "total": len(all_accounts),
        "accounts": all_accounts,
    }
