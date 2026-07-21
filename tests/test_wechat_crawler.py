"""Offline contracts for the WeChat crawler and incremental state."""

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


SHANGHAI = ZoneInfo("Asia/Shanghai")


def _configure_state(monkeypatch, tmp_path):
    from tools import wechat_crawler

    state_file = tmp_path / "wechat-state.json"
    monkeypatch.setattr(wechat_crawler, "STATE_FILE", str(state_file))
    return wechat_crawler, state_file


def test_sogou_listing_extracts_real_publisher_and_unix_time():
    from tools.wechat_crawler import _parse_sogou_article_results

    html = """
    <ul class="news-list"><li>
      <h3><a href="/link?url=temporary"><em>南京大学</em>活动通知</a></h3>
      <p class="txt-info">欢迎报名创新创业竞赛</p>
      <div class="s-p">
        <span class="all-time-y2">南京大学</span>
        <span class="s2"><script>document.write(timeConvert('1784563200'))</script></span>
      </div>
    </li></ul>
    """

    parsed = _parse_sogou_article_results(html, "南京大学")

    assert len(parsed) == 1
    assert parsed[0]["source_name"] == "南京大学"
    assert parsed[0]["target_account"] == "南京大学"
    assert parsed[0]["publish_time"].endswith("+08:00")
    assert parsed[0]["url"].startswith("https://weixin.sogou.com/link?")


def test_sogou_redirect_is_reassembled_without_executing_javascript():
    from tools.wechat_crawler import _resolve_sogou_redirect_html

    trampoline = """
    <script>
      doDangerousThing();
      var url = '';
      url += 'https://mp.';
      url += 'weixin.qq.com/s?__biz=abc&amp;timestamp=123';
      window.location.replace(url);
    </script>
    """
    malicious = "<script>var url=''; url += 'https://evil.example/s';</script>"

    assert _resolve_sogou_redirect_html(trampoline) == "https://mp.weixin.qq.com/s?__biz=abc&timestamp=123"
    assert _resolve_sogou_redirect_html(malicious) is None


def test_http_retry_handles_rate_limit_and_soft_block(monkeypatch):
    from tools import wechat_crawler

    class Response:
        def __init__(self, status, text="ok"):
            self.status_code = status
            self.text = text
            self.headers = {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise wechat_crawler.requests.HTTPError(str(self.status_code))

    class Session:
        def __init__(self, responses):
            self.responses = iter(responses)

        def get(self, *_args, **_kwargs):
            return next(self.responses)

    monkeypatch.setattr(wechat_crawler, "_rate_limit", lambda: None)
    monkeypatch.setattr(wechat_crawler.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(wechat_crawler, "_get_session", lambda: Session([Response(429), Response(200)]))
    assert wechat_crawler._request_with_retry("https://example.com", max_retries=2).status_code == 200

    monkeypatch.setattr(wechat_crawler, "_get_session", lambda: Session([Response(200, "请输入验证码")]))
    assert wechat_crawler._request_with_retry("https://example.com", max_retries=1) is None


def test_wechat_article_parser_extracts_body_metadata_and_stable_identity(monkeypatch):
    from tools import wechat_crawler

    trampoline = "<script>var url='';url += 'https://mp.weixin.qq.com/s?__biz=BIZ&mid=123&idx=2&sn=SN';</script>"
    article_html = """
    <html><head><meta property="og:title" content="创新创业竞赛报名"/></head><body>
      <a id="js_name">南京大学</a>
      <div id="js_content">报名截止时间为2026年8月1日，欢迎参加竞赛。</div>
      <script>
        var ct = "1784563200";
        var biz = "BIZVALUE";
        var mid = "123";
        var idx = "2";
        var sn = "0123456789abcdef0123456789abcdef";
      </script>
    </body></html>
    """

    class Response:
        def __init__(self, url, text):
            self.url = url
            self.text = text

    responses = iter((
        Response("https://weixin.sogou.com/link?token=one", trampoline),
        Response("https://mp.weixin.qq.com/s?src=11", article_html),
    ))
    monkeypatch.setattr(wechat_crawler, "_request_with_retry", lambda *_args, **_kwargs: next(responses))

    result = wechat_crawler.fetch_wechat_article("https://weixin.sogou.com/link?token=one")

    assert result["title"] == "创新创业竞赛报名"
    assert result["author"] == "南京大学"
    assert "报名截止时间" in result["detail_text"]
    assert result["canonical_url"] == "https://mp.weixin.qq.com/s?__biz=BIZVALUE&mid=123&idx=2&sn=0123456789abcdef0123456789abcdef"
    assert result["source_article_id"].startswith("WX-")


def test_article_identity_ignores_transient_url_tokens():
    from tools.wechat_crawler import _build_article_key, _canonicalize_wechat_url

    identifiers = {"biz": "BIZ", "mid": "123", "idx": "1", "sn": "SN"}
    first = _canonicalize_wechat_url("https://mp.weixin.qq.com/s?timestamp=1&signature=A", identifiers)
    second = _canonicalize_wechat_url("https://mp.weixin.qq.com/s?timestamp=2&signature=B", identifiers)

    assert first == second
    assert _build_article_key("A", "2026-07-21T10:00:00+08:00", "First", identifiers) == _build_article_key(
        "B", "2026-07-22T10:00:00+08:00", "Second", identifiers
    )


def test_incremental_crawl_quarantines_wrong_publisher_and_ack_skips_processed(monkeypatch, tmp_path):
    crawler, state_file = _configure_state(monkeypatch, tmp_path)
    now = datetime.now(SHANGHAI).isoformat()
    candidates = [
        {
            "title": "媒体报道南京大学竞赛",
            "url": "https://weixin.sogou.com/link?bad",
            "summary": "竞赛",
            "publish_time": now,
            "source_name": "某媒体",
            "target_account": "南京大学",
        },
        {
            "title": "创新创业竞赛报名",
            "url": "https://weixin.sogou.com/link?good",
            "summary": "报名通知",
            "publish_time": now,
            "source_name": "南京大学",
            "target_account": "南京大学",
        },
    ]
    detail = {
        "title": "创新创业竞赛报名",
        "detail_text": "创新创业竞赛报名截止时间为8月1日",
        "author": "南京大学",
        "publish_time": now,
        "canonical_url": "https://mp.weixin.qq.com/s?__biz=B&mid=1&idx=1",
        "source_article_id": "WX-STABLE",
    }
    fetch_calls = []
    monkeypatch.setattr(crawler, "search_all_accounts", lambda **_kwargs: candidates)
    monkeypatch.setattr(crawler, "get_all_accounts", lambda **_kwargs: [{"name": "南京大学", "aliases": []}])
    monkeypatch.setattr(crawler, "fetch_wechat_article", lambda url: fetch_calls.append(url) or detail)

    first = crawler.crawl_wechat_events(hours=6)
    crawler.mark_wechat_articles_processed(first)
    second = crawler.crawl_wechat_events(hours=6)

    assert [item["source_article_id"] for item in first] == ["WX-STABLE"]
    assert second == []
    assert fetch_calls == ["https://weixin.sogou.com/link?good"]
    state = crawler._load_crawl_state()
    assert state_file.exists()
    assert "WX-STABLE" in state["processed"]
    assert any(item["stage"] == "publisher_verification" for item in state["failures"])


def test_incremental_window_excludes_old_candidates_before_body_fetch(monkeypatch, tmp_path):
    crawler, _ = _configure_state(monkeypatch, tmp_path)
    old = "2020-01-01T00:00:00+08:00"
    monkeypatch.setattr(crawler, "search_all_accounts", lambda **_kwargs: [{
        "title": "旧竞赛报名",
        "url": "https://weixin.sogou.com/link?old",
        "summary": "竞赛",
        "publish_time": old,
        "source_name": "南京大学",
        "target_account": "南京大学",
    }])
    monkeypatch.setattr(crawler, "get_all_accounts", lambda **_kwargs: [{"name": "南京大学"}])
    monkeypatch.setattr(crawler, "fetch_wechat_article", lambda _url: (_ for _ in ()).throw(AssertionError("must not fetch")))

    assert crawler.crawl_wechat_events(hours=6) == []


def test_corrupt_state_recovers_without_crashing(monkeypatch, tmp_path):
    crawler, state_file = _configure_state(monkeypatch, tmp_path)
    state_file.write_text("not-json", encoding="utf-8")

    state = crawler._load_crawl_state()

    assert state["version"] == 1
    assert state["processed"] == {}


def test_rule_fallback_preserves_wechat_source_metadata():
    from tools.event_enrichment import _rule_based_fallback

    fallback = _rule_based_fallback({
        "title": "竞赛报名",
        "detail_text": "报名截止时间：2026年8月1日",
        "source_name": "南京大学",
        "source_url": "https://mp.weixin.qq.com/s?__biz=B&mid=1&idx=1",
        "source_article_id": "WX-ONE",
    })

    assert fallback["source_name"] == "南京大学"
    assert fallback["source_article_id"] == "WX-ONE"
    assert fallback["source_url"].startswith("https://mp.weixin.qq.com/")


def test_wechat_sync_acknowledges_only_terminal_database_results(monkeypatch):
    from tools import data_sync_workflow, wechat_crawler, wechat_data_source

    events = [
        {"title": "成功", "source_article_id": "WX-OK", "candidate_article_id": "WX-CANDIDATE"},
        {"title": "失败", "source_article_id": "WX-ERROR"},
    ]
    acknowledged = []
    monkeypatch.setattr(wechat_data_source, "fetch_wechat_events", lambda **_kwargs: events)
    monkeypatch.setattr(data_sync_workflow, "enrich_batch", lambda raw, **_kwargs: raw)
    monkeypatch.setattr(data_sync_workflow, "sync_events_to_db", lambda *_args, **_kwargs: {
        "added": 1,
        "updated": 0,
        "skipped": 0,
        "errors": 1,
        "details": [
            {"action": "added", "source_article_id": "WX-OK"},
            {"action": "error", "source_article_id": "WX-ERROR"},
        ],
    })
    monkeypatch.setattr(wechat_crawler, "mark_wechat_articles_processed", lambda items: acknowledged.extend(items))

    stats = data_sync_workflow.run_wechat_sync(hours=6, ctx=object())

    assert stats["wechat_acknowledged"] == 1
    assert [item["source_article_id"] for item in acknowledged] == ["WX-OK"]


def test_recrawl_script_delegates_to_shared_wechat_pipeline():
    source = (Path(__file__).resolve().parents[1] / "scripts" / "recrawl_all.py").read_text(encoding="utf-8")

    assert "crawl_wechat_events(hours=0)" in source
    assert "search_all_accounts" not in source
