"""Thread DTO 组装与状态映射服务。

把 Store 中的 thread 字典转换成前端 Dashboard 需要的 payload 格式。
职责边界：

- 状态映射：把后端 ``running/completed/failed`` 映射成前端 ``AgentStatus``。
- 消息组装：从 LangGraph checkpoint 提取用户可见的历史消息。
- PR 信息：从 thread 字段中解析 Pull Request 展示数据。
- 时间转换：把 ISO 时间戳转成前端使用的毫秒时间戳。
"""

from __future__ import annotations

from typing import Any

from agent.graph.checkpoint_history import visible_checkpoint_messages
from config.env_config import get_env
from utils.time_utils import iso_to_millis


def status_for_frontend(status: str | None) -> str:
    """把后端运行状态映射成前端 AgentStatus。

    Args:
        status: Store 中的 ``latest_run_status`` 字段值。

    Returns:
        前端识别的三种状态之一：``running`` / ``finished`` / ``error`` / ``idle``。
    """

    if status in {"running", "pushed", "pr_created"}:
        return "running"
    if status in {"completed", "awaiting_approval"}:
        return "finished"
    if status == "failed":
        return "error"
    return "idle"


def repo_full_name(thread: dict[str, Any]) -> str:
    """从 thread 字典中提取 ``owner/repo`` 格式的仓库全名。"""

    owner = thread.get("repo_owner") or ""
    repo = thread.get("repo_name") or ""
    if owner and repo:
        return f"{owner}/{repo}"
    return thread.get("repo_url") or ""


def pr_payload(thread: dict[str, Any]) -> dict[str, Any] | None:
    """从 thread 字典中解析 Pull Request 展示数据。

    如果 thread 没有关联 PR，返回 ``None``。
    """

    pr_url = thread.get("pr_url")
    if not pr_url:
        return None
    number = 0
    try:
        number = int(str(pr_url).rstrip("/").split("/")[-1])
    except ValueError:
        number = 0
    return {
        "number": number,
        "title": thread.get("title") or "Xihe Pull Request",
        "state": "open",
        "headRef": thread.get("branch_name") or "",
        "baseRef": "master",
        "url": pr_url,
    }


def _user_visible_text(text: str) -> str:
    """返回用户可见文本。

    前端历史正文只来自 checkpoint 中的 user/assistant 消息。
    Store、run_events 是否参与正文展示，由 ``message_payload`` 的数据来源控制，
    不再靠文本内容做二次过滤。
    """

    return text.strip()


def message_payload(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """生成前端可展示的消息列表。

    历史正文只从 LangGraph checkpoint 读取。Store 仍然可以写入业务数据、
    run_events、review findings 等内容，但不再参与聊天正文拼接，避免 checkpoint
    和 Store 双数据源导致页面重复、覆盖或乱序。

    当前轮实时过程由 POST SSE 直接推送增量事件；刷新页面后再由 checkpoint 恢复稳定历史。
    """

    created_at = thread.get("created_at")
    messages: list[dict[str, Any]] = []
    thread_id = str(thread["thread_id"])
    for index, message in enumerate(visible_checkpoint_messages(thread_id)):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        content = _user_visible_text(content)
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
    """组装完整的 thread DTO，包含 messages、PR、状态等全部字段。"""

    full_name = repo_full_name(thread)
    return {
        "id": thread["thread_id"],
        "title": thread.get("title") or "Xihe Task",
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
    """

    payload = thread_payload(thread)
    payload.pop("messages", None)
    return payload


def user_visible_stream_text(text: str) -> str:
    """返回流式正文。

    POST SSE 中的 assistant token/chunk 会直接给前端展示。
    空白文本仍然由调用方丢弃，避免产生空消息。
    """

    return text
