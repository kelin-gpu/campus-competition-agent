import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools.user_identity import MissingUserIdentityError, require_context_user_id


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_context_user_id_is_required_and_trimmed():
    assert require_context_user_id(SimpleNamespace(user_id="  coze-user-1  ")) == "coze-user-1"
    with pytest.raises(MissingUserIdentityError):
        require_context_user_id(None)
    with pytest.raises(MissingUserIdentityError):
        require_context_user_id(SimpleNamespace(user_id="  "))


def test_profile_tools_do_not_accept_user_id_arguments():
    source_path = PROJECT_ROOT / "src/tools/user_profile.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    expected_args = {
        "get_user_profile": [],
        "update_user_profile": ["fields_json"],
        "add_focus_contest": ["event_id"],
        "remove_focus_contest": ["event_id"],
        "get_personalized_recommendations": ["limit"],
    }

    functions = {
        node.name: [arg.arg for arg in node.args.args]
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in expected_args
    }
    assert functions == expected_args
    assert "default_user" not in source_path.read_text(encoding="utf-8")


def test_agent_exposes_only_student_safe_tools():
    config = json.loads(
        (PROJECT_ROOT / "config/agent_llm_config.json").read_text(encoding="utf-8")
    )
    assert set(config["tools"]) == {
        "query_events",
        "query_event_detail",
        "get_deadline_reminders",
        "parse_notification",
        "web_search_events",
        "get_user_profile",
        "update_user_profile",
        "add_focus_contest",
        "remove_focus_contest",
        "get_personalized_recommendations",
    }
    forbidden = {
        "trigger_full_sync",
        "trigger_incremental_sync",
        "start_scheduled_sync",
        "trigger_wechat_sync",
        "refresh_wechat_accounts",
        "cleanup_expired_events",
        "cross_verify_and_enrich",
        "search_knowledge_base",
    }
    assert forbidden.isdisjoint(config["tools"])
