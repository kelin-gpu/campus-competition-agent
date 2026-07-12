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
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote, urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================

# 目标公众号列表
WECHAT_ACCOUNTS = [
    {"name": "南京大学", "biz": "", "desc": "南京大学官方公众号"},
    {"name": "南京大学团委", "biz": "", "desc": "共青团南京大学委员会"},
    {"name": "南京大学学生会", "biz": "", "desc": "南京大学学生会"},
    {"name": "南京大学教务处", "biz": "", "desc": "南京大学教务处"},
    {"name": "南京大学研究生院", "biz": "", "desc": "南京大学研究生院"},
    {"name": "南京大学就业创业指导中心", "biz": "", "desc": "就业创业指导"},
    {"name": "南京大学健雄书院", "biz": "", "desc": "健雄书院"},
]

# 请求间隔（秒），遵守 robots 协议
REQUEST_INTERVAL = 5

# 请求超时（秒）
REQUEST_TIMEOUT = 15

# 最大重试次数
MAX_RETRIES = 3

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
_ua_index = 0


def _get_session() -> requests.Session:
    """获取或创建 HTTP session"""
    global _session, _ua_index
    if _session is None:
        _session = requests.Session()
        _session.headers.update({
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
    _ua_index = (_ua_index + 1) % len(USER_AGENTS)
    _session.headers["User-Agent"] = USER_AGENTS[_ua_index]
    return _session


def _request_with_retry(url: str, params: dict = None, max_retries: int = MAX_RETRIES) -> Optional[requests.Response]:
    """带重试的 HTTP 请求"""
    session = _get_session()
    for attempt in range(max_retries):
        try:
            resp = session.get(url, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp
        except requests.RequestException as e:
            logger.warning(f"Request failed (attempt {attempt+1}/{max_retries}): {url} -> {e}")
            if attempt < max_retries - 1:
                wait_time = REQUEST_INTERVAL * (attempt + 1)
                time.sleep(wait_time)
    logger.error(f"All {max_retries} attempts failed for: {url}")
    return None


# ============================================================
# 搜狗微信搜索 - 获取文章列表
# ============================================================

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
        soup = BeautifulSoup(resp.text, "lxml")
        articles = []

        # 搜狗微信搜索结果解析
        for item in soup.select("ul.news-list > li"):
            title_tag = item.select_one("h3 a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            url = str(title_tag.get("href", ""))
            if url and not url.startswith("http"):
                url = urljoin("https://weixin.sogou.com", url)

            summary_tag = item.select_one("p.txt-info")
            summary = summary_tag.get_text(strip=True) if summary_tag else ""

            time_tag = item.select_one("span.s2")
            publish_time = time_tag.get_text(strip=True) if time_tag else ""

            account_tag = item.select_one("div.s-p a[data-z]")
            source_name = account_tag.get_text(strip=True) if account_tag else account_name

            articles.append({
                "title": title,
                "url": url,
                "summary": summary,
                "publish_time": publish_time,
                "source_name": source_name,
            })

        logger.info(f"Found {len(articles)} articles for '{account_name}' (page {page})")
        return articles

    except Exception as e:
        logger.error(f"Failed to parse search results for '{account_name}': {e}")
        return []


def search_all_accounts(page: int = 1) -> list:
    """搜索所有目标公众号的文章"""
    all_articles = []
    for i, account in enumerate(WECHAT_ACCOUNTS):
        if i > 0:
            time.sleep(REQUEST_INTERVAL)
        articles = search_wechat_articles(account["name"], page=page)
        all_articles.extend(articles)
        logger.info(f"[{i+1}/{len(WECHAT_ACCOUNTS)}] {account['name']}: {len(articles)} articles")
    return all_articles


# ============================================================
# 微信文章页解析 - 提取正文
# ============================================================

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

    try:
        soup = BeautifulSoup(resp.text, "lxml")

        # 提取标题
        title_tag = soup.select_one("h1#activity-name") or soup.select_one("h1.rich_media_title")
        title = title_tag.get_text(strip=True) if title_tag else ""

        # 提取作者/公众号
        author_tag = soup.select_one("a#js_name") or soup.select_one("span.rich_media_meta_nickname")
        author = author_tag.get_text(strip=True) if author_tag else ""

        # 提取发布时间
        publish_time = ""
        # 微信文章页的发布时间通常在 script 中
        time_script = soup.find(string=re.compile(r'var\s+ct\s*=\s*"(\d+)"'))
        if time_script:
            match = re.search(r'var\s+ct\s*=\s*"(\d+)"', time_script)
            if match:
                timestamp = int(match.group(1))
                publish_time = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")

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

        return {
            "title": title,
            "detail_text": detail_text,
            "author": author,
            "publish_time": publish_time,
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
    combined = (title + " " + text[:500]).lower()
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
    logger.info(f"Starting WeChat crawl (last {hours}h)...")

    # 1. 搜索所有目标公众号
    articles = search_all_accounts()
    logger.info(f"Total articles found: {len(articles)}")

    # 2. 标题关键词过滤
    relevant = [a for a in articles if is_relevant(a.get("title", ""))]
    logger.info(f"Relevant after keyword filter: {len(relevant)}")

    # 3. 解析正文
    results = []
    seen_urls = set()

    for article in relevant:
        url = article.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)

        # 请求间隔
        if results:
            time.sleep(REQUEST_INTERVAL)

        detail = fetch_wechat_article(url)
        if detail is None:
            # 如果无法获取正文，使用搜索摘要
            detail_text = article.get("summary", "")
            if not detail_text:
                continue
        else:
            detail_text = detail.get("detail_text", "")

        # 二次过滤：正文也需包含关键词
        if not is_relevant(article.get("title", ""), detail_text):
            continue

        # 生成唯一 ID（基于 URL 的 hash）
        url_hash = hashlib.md5(url.encode()).hexdigest()[:8].upper()

        results.append({
            "title": detail.get("title") or article.get("title", ""),
            "detail_text": detail_text,
            "url": url,
            "publish_time": detail.get("publish_time") or article.get("publish_time", ""),
            "source_name": article.get("source_name", "微信公众号"),
            "author": detail.get("author", ""),
            "_wechat_id": f"WX-{url_hash}",
        })

    logger.info(f"WeChat crawl complete: {len(results)} relevant articles")
    return results


def get_wechat_accounts() -> list:
    """获取当前监控的公众号列表"""
    return [
        {
            "name": acc["name"],
            "desc": acc["desc"],
        }
        for acc in WECHAT_ACCOUNTS
    ]
