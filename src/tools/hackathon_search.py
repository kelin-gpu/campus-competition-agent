"""
黑客松专项搜索与解析模块。

职责：
- 通过 SearchClient 发起联网搜索，发现候选页面
- 抓取候选详情页，检查标题、正文、报名状态和日期
- 识别页面是否属于可报名的黑客松（排除往届回顾、培训、招聘等）
- 时间过滤：过期/关闭/时间异常/超远期一律不入库
- URL 与标题去重

本模块所有核心判断函数为纯函数，支持注入 now 便于测试。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import hashlib
import ipaddress
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

logger = logging.getLogger(__name__)

ASSETS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "assets", "data")

# ─── 环境变量默认值 ───
def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (ValueError, TypeError):
        logger.warning(f"Invalid env {name}, using default {default}")
        return default


HACKATHON_SEARCH_LIMIT = _env_int("HACKATHON_SEARCH_LIMIT", 60)
HACKATHON_MAX_FUTURE_DAYS = _env_int("HACKATHON_MAX_FUTURE_DAYS", 400)

# ─── 配置加载 ───
_sources_config: Optional[dict] = None


def _load_sources_config() -> dict:
    global _sources_config
    if _sources_config is None:
        path = os.path.join(ASSETS_DIR, "hackathon_sources.json")
        try:
            with open(path, "r", encoding="utf-8") as f:
                _sources_config = json.load(f)
        except Exception:
            logger.warning(f"Failed to load hackathon_sources.json, using defaults")
            _sources_config = {
                "search_queries": ["hackathon registration 2026", "site:devpost.com hackathon"],
                "exclusion_patterns": [],
                "max_future_days": 400,
                "fetch_timeout_sec": 15,
                "fetch_retries": 2,
                "fetch_delay_sec": 1.5,
                "max_page_bytes": 2 * 1024 * 1024,
                "user_agents": [
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
                ],
            }
    return _sources_config


# ─── SSRF 防护 ───
_BLOCKED_SCHEMES = {"file", "ftp", "gopher", "dict", "ldap", "jar"}
_BLOCKED_CIDRS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _is_safe_url(url: str) -> bool:
    """Check URL is safe (http/https only, no internal IPs). Returns True if safe."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    hostname = parsed.hostname
    if not hostname:
        return False
    # Block localhost
    if hostname.lower() in ("localhost", "0.0.0.0", "127.0.0.1", "::1", "[::1]"):
        return False
    try:
        addr = ipaddress.ip_address(hostname)
        for cidr in _BLOCKED_CIDRS:
            if addr in cidr:
                return False
    except ValueError:
        pass  # Not an IP — it's a domain, OK
    return True


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup: strip trailing slash, query params sorting."""
    if not url:
        return ""
    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = parsed.path.rstrip("/") if parsed.path != "/" else "/"
    return f"{netloc}{path}"


# ─── 黑客松判定 ───
_HACKATHON_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"黑客松",
        r"\bhackathon\b",
        r"\bhack\s?day\b",
        r"\bbuildathon\b",
        r"\bhack\s?weekend\b",
        r"\bcodefest\b",
        r"\bdatathon\b",
    ]
]

_EXCLUSION_PATTERNS = [
    re.compile(p, re.IGNORECASE) for p in [
        r"往届", r"回顾", r"获奖名单", r"获奖作品", r"结果公布",
        r"回顾视频", r"精彩瞬间", r"圆满结束", r"成功举办",
        r"past\s+events?", r"winners?", r"recap", r"highlights?",
        r"去年", r"历届", r"上一届",
        r"培训课", r"训练营", r"workshop\s+only",
        r"招聘", r"宣讲会", r"career\s+fair",
        r"submissions?\s+closed", r"event\s+ended",
        r"application\s+period\s+has\s+ended",
    ]
]


def is_hackathon_page(title: str, body_text: str) -> bool:
    """Check if page content is about a hackathon (not past/review/training)."""
    combined = f"{title}\n{body_text[:2000]}"
    # Must match hackathon signals
    if not any(p.search(combined) for p in _HACKATHON_PATTERNS):
        return False
    # Must not match exclusion patterns
    if any(p.search(combined) for p in _EXCLUSION_PATTERNS):
        return False
    return True


# ─── 报名状态检测 ───
_CLOSED_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"registration\s+(is\s+)?(closed|has\s+ended|ended)",
        r"报名\s*(已|已经)?\s*(结束|关闭|截止)",
        r"applications?\s+(are\s+)?closed",
        r"submissions?\s+(are\s+)?closed",
        r"no\s+longer\s+accepting",
        r"event\s+has\s+ended",
        r"活动\s*(已|已经)?\s*结束",
    ]
]

_OPEN_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"registration\s+(is\s+)?(open|now\s+open)",
        r"报名\s*(正在|火热|开放|进行)",
        r"applications?\s+(are\s+)?(open|now\s+open)",
        r"apply\s+now",
        r"register\s+now",
        r"submit\s+your\s+project",
        r"sign\s+up\s+today",
    ]
]


def detect_registration_status(text: str) -> Optional[str]:
    """Detect registration status: 'open', 'closed', or None if uncertain."""
    excerpt = text[:4000]
    if any(p.search(excerpt) for p in _CLOSED_PATTERNS):
        return "closed"
    if any(p.search(excerpt) for p in _OPEN_PATTERNS):
        return "open"
    return None


# ─── 日期提取 ───
_DATE_PATTERNS_EN = [
    # "Registration deadline: 2026-07-15"
    (re.compile(r"registration\s+deadline[:\s]*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", re.I), "date_only"),
    # "deadline: July 15, 2026"
    (re.compile(r"deadline[:\s]*([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})", re.I), "month_name"),
    # "Apply by July 15"
    (re.compile(r"apply\s+by[:\s]*([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", re.I), "month_name_maybe"),
    # "event starts: 2026-07-20" / "hackathon begins: 2026-07-20"
    (re.compile(r"(?:event|hackathon)\s+(?:starts?|begins?|date)[:\s]*(\d{4})[/-](\d{1,2})[/-](\d{1,2})", re.I), "date_only"),
    # ISO dates scattered in text: 2026-07-15
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
]

_DATE_PATTERNS_CN = [
    # 报名截止：2026年7月15日
    (re.compile(r"报名截止[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "date_only"),
    # 报名时间：2026年7月1日 - 2026年7月15日
    (re.compile(r"报名时间[：:].*?(?:至|到|-|–).*?(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "date_only"),
    # 比赛时间：2026年8月1日
    (re.compile(r"(?:比赛|活动|举办)时间[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日"), "date_only"),
    # 2026/07/15
    (re.compile(r"(\d{4})/(\d{1,2})/(\d{1,2})"), "iso"),
]

_MONTH_NAMES = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _make_iso(y: int, m: int, d: int) -> Optional[str]:
    """Build ISO date string. Returns None on invalid date."""
    try:
        dt = datetime(y, m, d)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def _parse_month_name(name: str) -> Optional[int]:
    return _MONTH_NAMES.get(name.lower().strip())


def extract_dates(text: str, now: Optional[datetime] = None) -> dict:
    """Extract signup_deadline and event_time from page text.

    Returns dict with keys: signup_deadline (date str or None), event_time (date str or None),
    has_full_deadline (bool).
    """
    result: Dict[str, Any] = {"signup_deadline": None, "event_time": None, "has_full_deadline": False}

    # --- English patterns first ---
    for pattern, ptype in _DATE_PATTERNS_EN:
        for m in pattern.finditer(text):
            if ptype == "date_only":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                date_str = _make_iso(y, mo, d)
            elif ptype == "month_name":
                mo = _parse_month_name(m.group(1))
                if mo is None:
                    continue
                d = int(m.group(2))
                y = int(m.group(3))
                date_str = _make_iso(y, mo, d)
            elif ptype == "month_name_maybe":
                mo = _parse_month_name(m.group(1))
                if mo is None:
                    continue
                d = int(m.group(2))
                y_str = m.group(3)
                y = int(y_str) if y_str else (now.year if now else datetime.now(timezone.utc).year)
                date_str = _make_iso(y, mo, d)
            elif ptype == "iso":
                y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                date_str = _make_iso(y, mo, d)
            else:
                continue

            if date_str is None:
                continue

            # Assign: first deadline, then event_time
            if result["signup_deadline"] is None:
                result["signup_deadline"] = date_str
                result["has_full_deadline"] = ptype in ("date_only", "iso")
            elif result["event_time"] is None and date_str != result["signup_deadline"]:
                result["event_time"] = date_str
                break
        if result["event_time"]:
            break

    # --- Chinese patterns ---
    if not result["signup_deadline"]:
        for pattern, ptype in _DATE_PATTERNS_CN:
            m = pattern.search(text)
            if m:
                if ptype in ("date_only", "iso"):
                    y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
                    date_str = _make_iso(y, mo, d)
                    if date_str:
                        result["signup_deadline"] = date_str
                        result["has_full_deadline"] = True
                        break

    # Separate event_time extraction for CN
    event_cn = re.search(r"(?:比赛|活动|举办)时间[：:]\s*(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日", text)
    if event_cn and not result["event_time"]:
        y, mo, d = int(event_cn.group(1)), int(event_cn.group(2)), int(event_cn.group(3))
        date_str = _make_iso(y, mo, d)
        if date_str:
            result["event_time"] = date_str

    return result


# ─── 网页抓取 ───
def fetch_detail_page(url: str, timeout: int = 15, retries: int = 2) -> Optional[str]:
    """Fetch a page and return its text content. Returns None on failure."""
    cfg = _load_sources_config()
    if not _is_safe_url(url):
        logger.warning(f"Blocked unsafe URL: {url[:80]}")
        return None

    headers = {
        "User-Agent": cfg["user_agents"][0],
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    max_bytes = cfg.get("max_page_bytes", 2 * 1024 * 1024)
    delay = cfg.get("fetch_delay_sec", 1.5)

    for attempt in range(retries + 1):
        try:
            if attempt > 0:
                time.sleep(delay * (attempt + 1))
            resp = requests.get(
                url, headers=headers, timeout=timeout,
                allow_redirects=True, stream=True,
            )
            if resp.status_code != 200:
                logger.debug(f"HTTP {resp.status_code} for {url[:60]}")
                return None

            chunks: List[bytes] = []
            total = 0
            for chunk in resp.iter_content(chunk_size=8192):
                total += len(chunk)
                if total > max_bytes:
                    logger.debug(f"Page too large ({total} bytes): {url[:60]}")
                    return None
                chunks.append(chunk)

            raw = b"".join(chunks)
            # Try to detect encoding
            content = resp.apparent_encoding or "utf-8"
            try:
                text = raw.decode(content, errors="replace")
            except Exception:
                text = raw.decode("utf-8", errors="replace")

            # Strip tags
            text = re.sub(r"<script[^>]*>.*?</script>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"&nbsp;", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
            return text[:8192]

        except requests.Timeout:
            logger.debug(f"Timeout fetching {url[:60]} (attempt {attempt + 1})")
        except requests.ConnectionError:
            logger.debug(f"Connection error for {url[:60]} (attempt {attempt + 1})")
        except Exception as e:
            logger.debug(f"Fetch error for {url[:60]}: {e}")

    return None


def _extract_title(text: str) -> str:
    """Extract title from HTML text (first h1 or document title)."""
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", m.group(1)).strip()[:200]
    m = re.search(r"<h1[^>]*>(.*?)</h1>", text, re.I | re.DOTALL)
    if m:
        return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(1))).strip()[:200]
    return ""


# ─── 时间过滤（纯函数，支持 now 注入） ───

def _to_utc_dt(date_str: Optional[str], end_of_day: bool = False,
               default_tz: str = "Asia/Shanghai") -> Optional[datetime]:
    """Convert date string to timezone-aware UTC datetime.

    If end_of_day=True, interprets date-only as 23:59:59 in default_tz.
    """
    if not date_str:
        return None
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None  # type: ignore

    # If already has timezone info
    dt = _coerce_iso_to_dt(date_str)
    if dt is None:
        return None

    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc)

    # No timezone — assume it's a date or local datetime
    tz = ZoneInfo(default_tz) if ZoneInfo else timezone(timedelta(hours=8))
    if end_of_day and dt.hour == 0 and dt.minute == 0 and dt.second == 0:
        dt = dt.replace(hour=23, minute=59, second=59, tzinfo=tz)
    else:
        dt = dt.replace(tzinfo=tz)
    return dt.astimezone(timezone.utc)


def _coerce_iso_to_dt(value: str) -> Optional[datetime]:
    """Parse ISO 8601 or date strings into datetime."""
    if not value:
        return None
    try:
        # Try full ISO
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        pass
    # Try date-only
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        pass
    # Try with / separator
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d")
    except ValueError:
        pass
    return None


def filter_event_by_time(
    signup_deadline: Optional[str],
    event_time_str: Optional[str],
    reg_status: Optional[str],
    now: Optional[datetime] = None,
    max_future_days: int = 400,
) -> Tuple[bool, str]:
    """Decide if an event should be accepted based on time/rules.

    Args:
        signup_deadline: Extracted deadline string
        event_time_str: Extracted event start time string
        reg_status: 'open', 'closed', or None
        now: Injectable current time (default: real now)
        max_future_days: Maximum days in future to accept

    Returns:
        (accepted, reason)
    """
    if now is None:
        now = datetime.now(timezone.utc)

    future_limit = now + timedelta(days=max_future_days)

    # 1. Explicitly closed
    if reg_status == "closed":
        return False, "closed_filtered"

    # 2. Signup deadline already passed
    deadline_dt = _to_utc_dt(signup_deadline, end_of_day=True)
    if deadline_dt is not None and deadline_dt < now:
        return False, "expired_filtered"

    # 3. Event time already passed AND no open registration evidence
    event_dt = _to_utc_dt(event_time_str)
    if event_dt is not None and event_dt < now and reg_status != "open":
        return False, "event_passed_filtered"

    # 4. Both dates too far in future
    if deadline_dt is not None and deadline_dt > future_limit:
        return False, "too_far_future_filtered"
    if event_dt is not None and event_dt > future_limit:
        return False, "too_far_future_filtered"

    # 5. Timeline conflict: event_time < signup_deadline
    if deadline_dt is not None and event_dt is not None and event_dt < deadline_dt:
        return False, "invalid_date_filtered"

    # 6. No deadline, no event time, no status → can't verify
    if deadline_dt is None and event_dt is None and reg_status is None:
        return False, "unverified_skipped"

    # 7. No deadline but event is open and in future → allow
    if deadline_dt is None and reg_status == "open":
        if event_dt is None or event_dt > now:
            return True, "accepted"

    # 8. Has valid future deadline → accept
    if deadline_dt is not None and deadline_dt >= now:
        return True, "accepted"

    # 9. No deadline but event_time in future without open evidence: skip
    if deadline_dt is None and event_dt is not None and event_dt > now and reg_status != "open":
        return False, "unverified_skipped"

    return False, "unverified_skipped"


# ─── 去重 ───
def deduplicate_candidates(
    candidates: List[dict],
    existing_titles: Optional[set] = None,
) -> List[dict]:
    """Deduplicate candidates by normalized URL then by normalized title.

    Args:
        candidates: List of candidate dicts with 'source_url' and 'title'
        existing_titles: Set of already-seen normalized titles

    Returns deduplicated list.
    """
    if existing_titles is None:
        existing_titles = set()

    seen_urls: set = set()
    seen_titles: set = set(existing_titles)
    result: List[dict] = []

    for c in candidates:
        url = c.get("source_url", "")
        norm_url = _normalize_url(url)
        title = c.get("title", "")

        if norm_url and norm_url in seen_urls:
            logger.debug(f"Dedup by URL: {title[:40]}")
            continue
        if norm_url:
            seen_urls.add(norm_url)

        norm_title = _normalize_title_light(title)
        if norm_title and norm_title in seen_titles:
            logger.debug(f"Dedup by title: {title[:40]}")
            continue
        if norm_title:
            seen_titles.add(norm_title)

        result.append(c)

    return result


def _normalize_title_light(title: str) -> str:
    """Lightweight title normalization for dedup."""
    if not title:
        return ""
    t = title.strip().lower()
    t = re.sub(r"[【】\[\]{}()（）\"\",.!！。，、：:；;「」『』]", "", t)
    t = re.sub(r"\s+", "", t)
    return t


# ─── 主办方提取 ───
def _extract_organizer(text: str) -> Optional[str]:
    for pat in [
        r"(?:organi[zs]ed|hosted|presented)\s+by[:\s]*([^\n.,;]{4,60})",
        r"(?:主办|承办)(?:方|单位)?[：:]\s*([^\n。，,]{4,40})",
        r"由\s*([^\n。，,]{3,30})\s*(?:主办|承办|组织)",
    ]:
        m = re.search(pat, text, re.I)
        if m:
            return m.group(1).strip()[:80]
    return None


# ─── 标签提取 ───
def _extract_tags(text: str) -> str:
    tags = ["黑客松"]
    lower = text.lower()
    tag_rules = [
        (r"\bai\b|人工智能|artificial intelligence", "AI"),
        (r"\bweb3\b|区块链|blockchain", "Web3"),
        (r"线上|online|virtual|remote", "线上"),
        (r"线下|offline|in[\s-]person|on[\s-]site", "线下"),
        (r"开源|open[\s-]source", "开源"),
        (r"\bml\b|machine learning|机器学习", "机器学习"),
        (r"社会公益|social good|impact", "公益"),
    ]
    for pattern, tag in tag_rules:
        if re.search(pattern, lower):
            tags.append(tag)
    return json.dumps(tags, ensure_ascii=False)

