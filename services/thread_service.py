"""Thread 消息组装与 checkpoint 读取服务。

负责从 LangGraph checkpoint 中提取消息并组装成前端需要的 payload。
纯数据转换函数已移至 ``utils.thread_utils``，本模块只保留涉及 I/O 的编排逻辑。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.graph.checkpoint_history import visible_checkpoint_messages
from config.env_config import get_env
from utils.thread_utils import pr_payload, repo_full_name, status_for_frontend
from utils.time_utils import iso_to_millis

logger = logging.getLogger(__name__)


def fetch_visible_messages(thread_id: str) -> list[dict[str, str]]:
    """通过 thread_id 从 checkpoint 获取可见消息列表。

    Args:
        thread_id: LangGraph checkpoint 的会话标识。

    Returns:
        用户可见的消息字典列表，获取失败时返回空列表。
    """

    try:
        from agent.graph.graph import get_checkpointer

        checkpoint = get_checkpointer().get({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.warning("读取 checkpoint 失败：thread_id=%s", thread_id, exc_info=True)
        return []
    return visible_checkpoint_messages(checkpoint)


def message_payload(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """生成前端可展示的消息列表。

    历史正文只从 LangGraph checkpoint 读取。Store 仍然可以写入业务数据、
    run_events、review findings 等内容，但不再参与聊天正文拼接，避免 checkpoint
    和 Store 双数据源导致页面重复、覆盖或乱序。

    当前轮实时过程由 POST SSE 直接推送增量事件；刷新页面后再由 checkpoint 恢复稳定历史。

    Args:
        thread: 包含 ``thread_id`` 等字段的会话字典。

    Returns:
        前端可展示的消息字典列表。
    """

    created_at = thread.get("created_at")
    messages: list[dict[str, Any]] = []
    thread_id = str(thread["thread_id"])
    for index, message in enumerate(fetch_visible_messages(thread_id)):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        content = content.strip()
        if not content:
            continue
        author = (
            message.get("author")
            if message.get("author") in {"user", "agent", "system", "tool"}
            else "agent"
        )
        messages.append(
            {
                "id": message.get("message_id") or f"{thread_id}-history-fallback-{index}",
                "author": author,
                "timestamp": message.get("created_at") or created_at,
                "chunks": [{"kind": "text", "text": content}],
            }
        )
    return messages


def thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    """组装完整的 thread DTO，包含 messages、PR、状态等全部字段。
    
    Args:
        thread: 包含会话原始字段的字典。
    
    Returns:
        前端需要的完整 thread DTO 字典。
    """

    full_name = repo_full_name(thread)
    return {
        "id": thread["thread_id"],
        "title": thread.get("title") or "xihe-agent Task",
        "repo": full_name,
        "repoFullName": full_name,
        "branch": thread.get("branch_name") or "master",
        "model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "effort": None,
        "source": "dashboard",
        "status": status_for_frontend(thread.get("latest_run_status")),
        "createdAt": iso_to_millis(thread.get("created_at")),
        "updatedAt": iso_to_millis(thread.get("updated_at")),
        "messages": message_payload(thread),
        "pr": pr_payload(thread),
        "latestPlan": None,
        "diffStats": None,
        "changedFiles": [],
    }


def thread_meta_payload(thread: dict[str, Any]) -> dict[str, Any]:
    """返回不含 messages 的会话元信息。
    
    实时流只能更新状态、分支、PR 等元信息，不能把历史 messages 重新推给前端，
    否则会覆盖前端当前轮已经追加的用户输入和流式正文。
    
    Args:
        thread: 包含会话原始字段的字典。
    
    Returns:
        去除 messages 后的 thread DTO 字典。
    """

    payload = thread_payload(thread)
    payload.pop("messages", None)
    return payload
