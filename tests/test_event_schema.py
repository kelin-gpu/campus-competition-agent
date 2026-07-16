from tools.event_schema import EVENT_DB_FIELDS, event_db_payload, merge_event_data


def test_payload_filters_internal_and_unknown_fields():
    payload = event_db_payload(
        {
            "event_id": "EVT-1",
            "title": "测试赛事",
            "_needs_cross_verify": ["event_time"],
            "model_explanation": "not a database column",
        }
    )

    assert payload == {"event_id": "EVT-1", "title": "测试赛事"}
    assert "_needs_cross_verify" not in EVENT_DB_FIELDS


def test_lower_priority_source_only_fills_empty_fields():
    existing = {
        "event_id": "EVT-1",
        "title": "权威标题",
        "summary": "已有简介",
        "organizer": "",
        "authority_level": "高",
        "source_name": "学校官网",
    }
    incoming = {
        "event_id": "SHOULD-NOT-REPLACE",
        "title": "爬虫标题",
        "summary": "",
        "organizer": "南京大学",
        "authority_level": "低",
        "source_name": "第三方页面",
    }

    merged = merge_event_data(existing, incoming)

    assert merged["event_id"] == "EVT-1"
    assert merged["title"] == "权威标题"
    assert merged["summary"] == "已有简介"
    assert merged["organizer"] == "南京大学"
    assert merged["source_name"] == "学校官网"


def test_ministry_source_can_replace_populated_lower_priority_fields():
    existing = {
        "event_id": "EVT-1",
        "title": "旧标题",
        "authority_level": "中",
        "is_ministry_approved": False,
    }
    incoming = {
        "event_id": "EVT-2",
        "title": "教育部目录标题",
        "authority_level": "高",
        "is_ministry_approved": True,
        "extra": "ignored",
    }

    merged = merge_event_data(existing, incoming)

    assert merged["event_id"] == "EVT-1"
    assert merged["title"] == "教育部目录标题"
    assert merged["is_ministry_approved"] is True
    assert "extra" not in merged
