from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_sync_workflow_never_hard_deletes_expired_events():
    source = (PROJECT_ROOT / "src/tools/data_sync_workflow.py").read_text(encoding="utf-8")
    assert '.table("event_info").delete()' not in source
    assert '"status": "已截止"' in source


def test_notification_tool_is_preview_only():
    source = (PROJECT_ROOT / "src/tools/event_parse_tool.py").read_text(encoding="utf-8")
    preview_start = source.index("def parse_notification")
    preview_source = source[preview_start:]

    assert "_insert_event(" not in preview_source
    assert '"status": "preview"' in preview_source
