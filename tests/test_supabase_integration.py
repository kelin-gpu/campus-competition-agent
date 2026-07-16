"""Opt-in regression tests against an isolated, platform-compatible Supabase."""

import json
import os
import uuid

import pytest


REQUIRED_ENV = (
    "TEST_COZE_SUPABASE_URL",
    "TEST_COZE_SUPABASE_ANON_KEY",
    "TEST_COZE_SUPABASE_SERVICE_ROLE_KEY",
)
INTEGRATION_ENABLED = os.getenv("RUN_SUPABASE_INTEGRATION_TESTS") == "1" and all(
    os.getenv(name) for name in REQUIRED_ENV
)
pytestmark = pytest.mark.skipif(
    not INTEGRATION_ENABLED,
    reason="set RUN_SUPABASE_INTEGRATION_TESTS=1 and TEST_COZE_SUPABASE_*",
)


def _client():
    pytest.importorskip("supabase")
    from supabase import create_client

    return create_client(
        os.environ["TEST_COZE_SUPABASE_URL"],
        os.environ["TEST_COZE_SUPABASE_SERVICE_ROLE_KEY"],
    )


def test_event_persistence_expiry_and_failed_cross_verification(monkeypatch):
    from tools import cross_verify_enrich, data_sync_workflow

    client = _client()
    test_key = f"TEST-{uuid.uuid4().hex[:12].upper()}"
    title = f"{test_key} event persistence"
    event_id = None
    previous_client = data_sync_workflow._supabase_client
    data_sync_workflow._supabase_client = client

    try:
        first = data_sync_workflow.sync_events_to_db(
            [
                {
                    "title": title,
                    "scope_type": "校外竞赛",
                    "summary": "权威简介",
                    "signup_deadline": "2099-08-01T23:59:59+08:00",
                    "event_time": "2099-08-20T08:00:00+08:00",
                    "authority_level": "高",
                    "source_name": "测试权威来源",
                    "model_explanation": "must never reach event_info",
                }
            ]
        )
        assert first["added"] == 1 and first["errors"] == 0
        event_id = first["details"][0]["event_id"]
        assert event_id.startswith("EVT-")

        second = data_sync_workflow.sync_events_to_db(
            [
                {
                    "title": title,
                    "scope_type": "校外竞赛",
                    "summary": "低可信度不得覆盖",
                    "organizer": "测试主办方",
                    "authority_level": "低",
                    "source_name": "测试低可信度来源",
                }
            ]
        )
        assert second["updated"] == 1 and second["errors"] == 0
        row = (
            client.table("event_info")
            .select("*")
            .eq("event_id", event_id)
            .single()
            .execute()
            .data
        )
        assert row["summary"] == "权威简介"
        assert row["organizer"] == "测试主办方"
        assert "model_explanation" not in row

        monkeypatch.setattr(cross_verify_enrich, "get_supabase_client", lambda: client)
        monkeypatch.setattr(
            cross_verify_enrich,
            "_collect_candidates",
            lambda _title, _missing: [
                {
                    "source_name": "source-a",
                    "source_url": "https://example.com/a",
                    "source_domain": "example.com",
                    "candidates": {"contest_level": "国家级"},
                },
                {
                    "source_name": "source-b",
                    "source_url": "https://university.edu.cn/b",
                    "source_domain": "university.edu.cn",
                    "candidates": {"contest_level": "省级"},
                },
            ],
        )
        result = cross_verify_enrich.cross_verify_and_enrich(event_id)
        assert "未通过" in result
        unchanged = (
            client.table("event_info")
            .select("contest_level")
            .eq("event_id", event_id)
            .single()
            .execute()
            .data
        )
        assert unchanged["contest_level"] is None

        client.table("event_info").update(
            {"signup_deadline": "2000-01-01T00:00:00+00:00", "status": "报名中"}
        ).eq("event_id", event_id).execute()
        data_sync_workflow._cleanup_expired(client)
        expired = (
            client.table("event_info")
            .select("event_id,status")
            .eq("event_id", event_id)
            .single()
            .execute()
            .data
        )
        assert expired == {"event_id": event_id, "status": "已截止"}
    finally:
        data_sync_workflow._supabase_client = previous_client
        if event_id:
            client.table("event_info").delete().eq("event_id", event_id).execute()


def test_two_real_profiles_remain_isolated(monkeypatch):
    from tools import user_profile

    client = _client()
    test_key = f"TEST-{uuid.uuid4().hex[:12].upper()}"
    user_ids = [f"{test_key}-A", f"{test_key}-B"]
    current = {"user_id": user_ids[0]}
    previous_client = user_profile._supabase_client
    user_profile._supabase_client = client
    monkeypatch.setattr(user_profile, "_current_user_id", lambda: current["user_id"])

    try:
        user_profile.update_user_profile.invoke(
            {"fields_json": json.dumps({"major": "计算机科学"}, ensure_ascii=False)}
        )
        current["user_id"] = user_ids[1]
        user_profile.update_user_profile.invoke(
            {"fields_json": json.dumps({"major": "应用数学"}, ensure_ascii=False)}
        )

        rows = (
            client.table("user_profile")
            .select("user_id,major")
            .in_("user_id", user_ids)
            .execute()
            .data
        )
        majors = {row["user_id"]: row["major"] for row in rows}
        assert majors == {user_ids[0]: "计算机科学", user_ids[1]: "应用数学"}
    finally:
        user_profile._supabase_client = previous_client
        client.table("user_profile").delete().in_("user_id", user_ids).execute()
