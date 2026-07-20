"""Offline contract tests for sync payloads and service boundaries."""

from unittest.mock import MagicMock, patch

from tools.catalog_service import merge_catalog
from tools.data_sync_workflow import _build_ministry_catalog_payload


def test_ministry_sync_payload_uses_catalog_service_title_contract():
    payload = _build_ministry_catalog_payload({
        "title": "全国大学生数学建模竞赛",
        "_ministry_info": {
            "organizer": "中国工业与应用数学学会",
            "level": "国家级",
            "category": "数学",
        },
    })

    assert payload["title"] == "全国大学生数学建模竞赛"
    assert payload["original_title"] == payload["title"]
    assert "normalized_title" not in payload


def test_merge_catalog_accepts_legacy_normalized_title_payload():
    session = MagicMock()
    expected_catalog = object()

    with patch(
        "tools.catalog_service.get_or_create_catalog",
        return_value=(expected_catalog, False),
    ) as get_or_create:
        result = merge_catalog(session, {
            "normalized_title": "全国大学生数学建模竞赛",
            "source_name": "教育部竞赛目录",
            "scope_type": "校外竞赛",
        })

    assert result is expected_catalog
    assert get_or_create.call_args.args[1] == "全国大学生数学建模竞赛"
    session.commit.assert_called_once_with()
