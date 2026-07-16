from pathlib import Path

from tools.cross_verify_rules import (
    cross_check,
    extract_deadline,
    extract_event_time,
    extract_organizer,
    source_domain,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _candidate(url: str, value: str, name: str = "source") -> dict:
    return {
        "source_name": name,
        "source_url": url,
        "candidates": {"signup_deadline": value},
    }


def test_calendar_dates_are_validated():
    assert extract_deadline("报名截止：2026年2月30日") is None
    assert extract_event_time("比赛时间：2026-13-01") is None
    assert extract_deadline("报名截止：2026年2月28日") == (
        "2026-02-28T23:59:59+08:00"
    )


def test_organizer_returns_the_organization_not_regex_label():
    assert extract_organizer("主办单位：中国计算机学会。") == "中国计算机学会"
    assert extract_organizer("由 教育部高等教育司 主办") == "教育部高等教育司"


def test_subdomains_of_one_site_are_not_independent_sources():
    value = "2026-08-01T23:59:59+08:00"
    verified, trace = cross_check(
        [
            _candidate("https://news.example.com/a", value, "news"),
            _candidate("https://events.example.com/b", value, "events"),
        ]
    )

    assert verified == {}
    assert trace["signup_deadline"]["total_sources"] == 1


def test_two_independent_domains_must_agree_and_are_traced():
    value = "2026-08-01T23:59:59+08:00"
    verified, trace = cross_check(
        [
            _candidate("https://contest.example.com/a", value, "official"),
            _candidate("https://university.edu.cn/b", value, "university"),
        ]
    )

    assert verified == {"signup_deadline": value}
    field_trace = trace["signup_deadline"]
    assert field_trace["agree_count"] == 2
    assert field_trace["total_sources"] == 2
    assert {source["source_name"] for source in field_trace["sources"]} == {
        "official",
        "university",
    }
    assert all(source["source_url"] for source in field_trace["sources"])


def test_independent_sources_that_disagree_do_not_verify():
    verified, trace = cross_check(
        [
            _candidate("https://example.com/a", "2026-08-01T23:59:59+08:00"),
            _candidate("https://university.edu.cn/b", "2026-08-02T23:59:59+08:00"),
        ]
    )

    assert verified == {}
    assert trace["signup_deadline"]["agree_count"] == 1


def test_source_domain_uses_registrable_site_boundary():
    assert source_domain("https://news.example.com/a") == "example.com"
    assert source_domain("https://events.university.edu.cn/a") == "university.edu.cn"


def test_internal_writer_has_safe_timestamp_and_no_agent_decorator():
    source = (PROJECT_ROOT / "src/tools/cross_verify_enrich.py").read_text(
        encoding="utf-8"
    )

    assert '@tool\ndef cross_verify_and_enrich' not in source
    assert 'update_data["update_time"] = "now()"' not in source
    assert "datetime.now(timezone.utc).isoformat()" in source
    assert "seen_domains" in source
