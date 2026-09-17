"""Thread DTO 组装与状态映射工具。

把 Store 中的 thread 字典转换成前端 Dashboard 需要的 payload 格式。
职责边界：

- 状态映射：把后端 ``running/completed/failed`` 映射成前端 ``AgentStatus``。
- PR 信息：从 thread 字段中解析 Pull Request 展示数据。
- 文本过滤：提取用户可见的流式正文。
"""

from __future__ import annotations

from typing import Any


def status_for_frontend(status: str | None) -> str:
    """把后端运行状态映射成前端 AgentStatus。

    Args:
        status: Store 中的 ``latest_run_status`` 字段值。

    Returns:
        前端识别的状态之一：``running`` / ``finished`` / ``error`` / ``idle``。
    """

    if status in {"running", "pushed", "pr_created"}:
        return "running"
    if status == "awaiting_approval":
        return "interrupted"
    if status == "completed":
        return "finished"
    if status == "failed":
        return "error"
    return "idle"


def repo_full_name(thread: dict[str, Any]) -> str:
    """从 thread 字典中提取 ``owner/repo`` 格式的仓库全名。

    Args:
        thread: Store 中的 thread 字典。

    Returns:
        ``owner/repo`` 格式的全名；字段缺失时回退到 ``repo_url``。
    """

    owner = thread.get("repo_owner") or ""
    repo = thread.get("repo_name") or ""
    if owner and repo:
        return f"{owner}/{repo}"
    return thread.get("repo_url") or ""


def pr_payload(thread: dict[str, Any]) -> dict[str, Any] | None:
    """从 thread 字典中解析 Pull Request 展示数据。

    Args:
        thread: Store 中的 thread 字典。

    Returns:
        包含 PR 编号、标题、状态等信息的字典；没有关联 PR 时返回 ``None``。
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
        "title": thread.get("title") or "XiheAgent Pull Request",
        "state": "open",
        "headRef": thread.get("branch_name") or "",
        "baseRef": "master",
        "url": pr_url,
    }


def user_visible_stream_text(text: str) -> str:
    """返回流式正文。

    POST SSE 中的 assistant token/chunk 会直接给前端展示。
    空白文本仍然由调用方丢弃，避免产生空消息。

    Args:
        text: SSE 流式输出的文本片段。

    Returns:
        原样返回的文本，供前端展示。
    """

    return text
