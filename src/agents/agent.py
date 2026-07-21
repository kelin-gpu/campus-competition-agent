"""校园竞赛与活动信息服务 AI 助手 - Agent 核心逻辑"""
import os
import json
import logging
from typing import Annotated

from langchain.agents import create_agent
from langchain.agents.middleware import wrap_tool_call
from langchain_openai import ChatOpenAI
from langgraph.graph import MessagesState
from langgraph.graph.message import add_messages
from langchain_core.messages import AnyMessage, ToolMessage
from coze_coding_utils.runtime_ctx.context import default_headers

from storage.memory.memory_saver import get_memory_saver
from tools.event_query_tool import query_events, query_event_detail, get_deadline_reminders
from tools.event_parse_tool import parse_notification
from tools.web_search_tool import web_search_events
from tools.user_profile import (
    get_user_profile,
    update_user_profile,
    add_focus_contest,
    remove_focus_contest,
    get_personalized_recommendations,
)
from tools.ministry_catalog_validator import (
    validate_ministry_catalog,
    apply_ministry_catalog_updates,
)

logger = logging.getLogger(__name__)

LLM_CONFIG = "config/agent_llm_config.json"

# 默认保留最近 20 轮对话 (40 条消息)
MAX_MESSAGES = 40


def _windowed_messages(old, new):
    """滑动窗口: 只保留最近 MAX_MESSAGES 条消息"""
    return add_messages(old, new)[-MAX_MESSAGES:]  # type: ignore


class AgentState(MessagesState):
    messages: Annotated[list[AnyMessage], _windowed_messages]


@wrap_tool_call
def handle_tool_errors(request, handler):
    """Handle tool execution errors with custom messages."""
    try:
        return handler(request)
    except Exception as e:
        return ToolMessage(
            content=f"工具执行出错: {str(e)}，请稍后重试或换个方式提问。",
            tool_call_id=request.tool_call["id"]
        )


def build_agent(ctx=None):
    workspace_path = os.getenv("COZE_WORKSPACE_PATH", "/workspace/projects")
    config_path = os.path.join(workspace_path, LLM_CONFIG)

    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)

    api_key = os.getenv("COZE_WORKLOAD_IDENTITY_API_KEY")
    base_url = os.getenv("COZE_INTEGRATION_MODEL_BASE_URL")

    llm = ChatOpenAI(
        model=cfg['config'].get("model"),
        api_key=api_key,
        base_url=base_url,
        temperature=cfg['config'].get('temperature', 0.7),
        streaming=True,
        timeout=cfg['config'].get('timeout', 600),
        extra_body={
            "thinking": {
                "type": cfg['config'].get('thinking', 'disabled')
            }
        },
        default_headers=default_headers(ctx) if ctx else {}
    )

    tools = [
        query_events,
        query_event_detail,
        get_deadline_reminders,
        parse_notification,
        web_search_events,
        # 用户画像与个性化推荐
        get_user_profile,
        update_user_profile,
        add_focus_contest,
        remove_focus_contest,
        get_personalized_recommendations,
        # 教育部竞赛目录校验
        validate_ministry_catalog,
        apply_ministry_catalog_updates,
    ]

    agent = create_agent(
        model=llm,
        system_prompt=cfg.get("sp"),
        tools=tools,
        middleware=[handle_tool_errors],
        checkpointer=get_memory_saver(),
        state_schema=AgentState,
    )

    return agent
