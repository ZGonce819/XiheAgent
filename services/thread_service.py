"""Thread 消息组装与 checkpoint 读取服务。

负责从 LangGraph checkpoint 中提取消息并组装成前端需要的 payload。
纯数据转换函数已移至 ``utils.thread_utils``，本模块只保留涉及 I/O 的编排逻辑。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent.graph.checkpoint_history import visible_checkpoint_messages
from config.env_config import get_env
from utils.platform_utils import detect_platform, get_platform_display_name
from utils.thread_utils import pr_payload, repo_full_name, status_for_frontend
from utils.time_utils import iso_to_millis

logger = logging.getLogger(__name__)


def _last_user_text(messages: list[dict[str, Any]]) -> str:
    for message in reversed(messages):
        author = message.get("author") or message.get("role")
        if author != "user":
            continue
        chunks = message.get("chunks")
        if isinstance(chunks, list):
            return "".join(
                str(chunk.get("text") or "")
                for chunk in chunks
                if isinstance(chunk, dict) and chunk.get("kind") == "text"
            ).strip()
        return str(message.get("content") or "").strip()
    return ""


def fetch_visible_messages(thread_id: str) -> list[dict[str, str]]:
    """通过 thread_id 从 checkpoint 获取可见消息列表。

    优先从 ``channel_values.messages`` 读取；如果为空（当前 Redis checkpointer
    版本不内联写入该字段），则回退到扫描 ``checkpoint_write:*`` 条目重建消息。

    Args:
        thread_id: LangGraph checkpoint 的会话标识。

    Returns:
        用户可见的消息字典列表，获取失败时返回空列表。
    """

    try:
        from agent.graph.graph import get_checkpointer

        checkpointer = get_checkpointer()
        checkpoint = checkpointer.get({"configurable": {"thread_id": thread_id}})
        messages = visible_checkpoint_messages(checkpoint)
        if messages:
            return messages

        from agent.graph.checkpoint_history import extract_messages_from_writes

        redis_client = getattr(checkpointer, "_redis", None) or getattr(
            checkpointer, "client", None
        )
        if redis_client is None:
            return []
        return extract_messages_from_writes(redis_client, thread_id)
    except Exception:
        logger.warning("读取 checkpoint 失败：thread_id=%s", thread_id, exc_info=True)
        return []


def _messages_from_run_events(
    thread_id: str,
    run_events: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """从 run_events 中重建前端可展示的任务计划消息。

    实时流式传输时，前端通过 SSE 接收 ``todo_delta`` 事件并构建任务计划消息。
    刷新页面后这些 SSE 事件不再可用，需要从 ``run_events`` 表中重建任务计划，
    使历史视图也能展示 Agent 的执行计划。

    Agent 的最终文本回复已保存在 checkpoint 中，不需要从 run_events 重建。

    Args:
        thread_id: 会话 ID。
        run_events: 该会话的运行事件列表。

    Returns:
        重建的任务计划消息字典列表。
    """

    messages: list[dict[str, Any]] = []
    latest_todo_by_run: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}

    for event in run_events:
        event_id = str(event.get("id") or "")
        kind = str(event.get("kind") or "")
        detail = event.get("detail")

        if kind != "todo" or not detail:
            continue

        parts = event_id.split(":")
        try:
            todos_index = parts.index("todos")
            run_id = parts[todos_index + 1]
        except (ValueError, IndexError):
            continue
        try:
            parsed = json.loads(detail) if isinstance(detail, str) else detail
        except (json.JSONDecodeError, TypeError):
            continue
        todos = parsed.get("todos") if isinstance(parsed, dict) else None
        if not todos:
            continue

        latest_todo_by_run[run_id] = (event, todos)

    for run_id, (event, todos) in latest_todo_by_run.items():
        message_id = f"{thread_id}-live-plan-{run_id}"
        messages.append({
            "id": message_id,
            "author": "agent",
            "timestamp": event.get("created_at"),
            "chunks": [
                {"kind": "text", "text": "任务计划"},
                {"kind": "todo", "todos": todos},
            ],
        })

    return messages


def message_payload(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """生成前端可展示的消息列表。

    历史正文从 LangGraph checkpoint 读取，同时从 ``run_events`` 重建任务计划和
    Agent 流式文本段落，使刷新页面后仍能展示运行过程中的中间状态。

    Args:
        thread: 包含 ``thread_id`` 等字段的会话字典。

    Returns:
        前端可展示的消息字典列表。
    """

    created_at = thread.get("created_at")
    thread_id = str(thread["thread_id"])

    messages: list[dict[str, Any]] = []
    for index, message in enumerate(fetch_visible_messages(thread_id)):
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        role = message.get("role") or ""
        author = "user" if role == "user" else "agent"
        messages.append(
            {
                "id": message.get("id") or f"{thread_id}-history-fallback-{index}",
                "author": author,
                "timestamp": message.get("created_at") or created_at,
                "chunks": [{"kind": "text", "text": content}],
            }
        )

    run_events = thread.get("run_events") or []
    event_messages = _messages_from_run_events(thread_id, run_events)
    if event_messages:
        user_index = -1
        for i, msg in enumerate(messages):
            if msg.get("author") == "user":
                user_index = i
        if user_index >= 0:
            messages[user_index + 1:user_index + 1] = event_messages
        else:
            messages.extend(event_messages)

    if not messages:
        user_prompt = (thread.get("user_prompt") or "").strip()
        if user_prompt:
            messages.append(
                {
                    "id": f"{thread_id}-fallback-prompt",
                    "author": "user",
                    "timestamp": created_at,
                    "chunks": [{"kind": "text", "text": user_prompt}],
                }
            )
    elif not any(m.get("author") == "user" for m in messages):
        user_prompt = (thread.get("user_prompt") or "").strip()
        if user_prompt:
            messages.insert(
                0,
                {
                    "id": f"{thread_id}-fallback-prompt",
                    "author": "user",
                    "timestamp": created_at,
                    "chunks": [{"kind": "text", "text": user_prompt}],
                },
            )
    return messages


def conversation_snapshot(
    thread: dict[str, Any],
    *,
    edited_message_id: str | None = None,
) -> dict[str, Any]:
    """Build the read-only UI state archived before a message edit."""

    messages = message_payload(thread)
    edited_user_index = None
    if edited_message_id:
        user_index = 0
        for message in messages:
            if message.get("role") == "user" or message.get("author") == "user":
                if str(message.get("id") or "") == edited_message_id:
                    edited_user_index = user_index
                    break
                user_index += 1
    return {
        "messages": messages,
        "runEvents": thread.get("run_events") or [],
        "editedMessageId": edited_message_id,
        "editedUserIndex": edited_user_index,
    }


def _local_work_dirs(thread: dict[str, Any]) -> tuple[str, str] | None:
    """解析本地环境会话的本地工作目录（相对路径 + 绝对路径）。

    解析规则与运行时保持一致：
    1. 优先取项目绑定的本地文件夹（通过 project_name 反查）。
    2. 其次取仓库映射表登记的目录。
    3. 未登记时回退到默认的 ``projects/<repo>``。
    沙箱环境或未指定仓库时返回 ``None``。

    Args:
        thread: 包含会话原始字段的字典。

    Returns:
        ``(工作区相对路径, 绝对路径)`` 元组；不适用时返回 ``None``。
    """
    if (thread.get("backend_type") or "local") != "local":
        return None
    repo_url = thread.get("repo_url") or ""
    if not repo_url:
        return None
    try:
        from agent.graph.graph import get_store
        from config.app_config import get_settings

        store = get_store()
        workspace_root = get_settings().workspace_root.resolve()
        thread_id = str(thread.get("thread_id") or "")

        # 优先取项目绑定的本地文件夹（与运行时 resolve_project_local_folder 对齐）
        project_name = thread.get("project_name")
        if project_name:
            try:
                record = store.get_project_folder(project_name)
                folder = record.get("local_folder") if record else None
                if isinstance(folder, str) and folder.strip():
                    abs_path = Path(folder)
                    try:
                        relative = abs_path.resolve().relative_to(workspace_root).as_posix()
                    except ValueError:
                        relative = abs_path.name
                    return relative, str(abs_path)
            except Exception:
                logger.warning("解析项目本地文件夹失败：project_name=%s", project_name, exc_info=True)

        # 回退到仓库映射表
        from services.repo_rollback import repository_path

        abs_path = repository_path(repo_url, store)
        try:
            relative = abs_path.resolve().relative_to(workspace_root).as_posix()
        except ValueError:
            relative = abs_path.name
        return relative, str(abs_path)
    except Exception:
        logger.warning("解析本地工作目录失败：repo_url=%s", repo_url, exc_info=True)
        return None


def thread_payload(
    thread: dict[str, Any], *, include_versions: bool = False
) -> dict[str, Any]:
    """组装完整的 thread DTO，包含 messages、run_events、PR、状态等全部字段。

    Args:
        thread: 包含会话原始字段的字典。

    Returns:
        前端需要的完整 thread DTO 字典。
    """

    full_name = repo_full_name(thread)
    repo_url = thread.get("repo_url") or ""
    platform = ""
    platform_display = ""
    if repo_url:
        try:
            platform = detect_platform(repo_url)
            platform_display = get_platform_display_name(platform)
        except Exception:
            pass

    run_events = thread.get("run_events") or []
    work_dirs = _local_work_dirs(thread)
    payload = {
        "id": thread["thread_id"],
        "title": thread.get("title") or "XiheAgent Task",
        "repo": full_name,
        "repoFullName": full_name,
        "repoUrl": repo_url.removesuffix(".git"),
        "platform": platform,
        "platformDisplay": platform_display,
        "branch": thread.get("branch_name") or "master",
        "model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "effort": None,
        "source": "dashboard",
        "status": status_for_frontend(thread.get("latest_run_status")),
        "backendType": thread.get("backend_type") or "local",
        "projectName": thread.get("project_name"),
        "localWorkDir": work_dirs[0] if work_dirs else None,
        "localWorkDirFull": work_dirs[1] if work_dirs else None,
        "createdAt": iso_to_millis(thread.get("created_at")),
        "updatedAt": iso_to_millis(thread.get("updated_at")),
        "messages": message_payload(thread),
        "runEvents": run_events,
        "pr": pr_payload(thread),
        "latestPlan": None,
        "diffStats": None,
        "changedFiles": [],
    }
    try:
        from config.approval_runtime import get_pending_approval, public_approval

        payload["pendingApproval"] = public_approval(
            get_pending_approval(str(thread["thread_id"]))
        )
    except Exception:
        payload["pendingApproval"] = None
    if include_versions:
        try:
            from agent.graph.graph import get_store

            store = get_store()
            saved_view = store.get_conversation_view(str(thread["thread_id"]))
            if saved_view and _last_user_text(saved_view) == _last_user_text(payload["messages"]):
                payload["messages"] = saved_view
            payload["conversationVersions"] = store.list_conversation_versions(
                str(thread["thread_id"])
            )
        except Exception:
            logger.warning(
                "读取会话版本失败：thread_id=%s", thread.get("thread_id"), exc_info=True
            )
            payload["conversationVersions"] = []
    return payload


def thread_meta_payload(thread: dict[str, Any]) -> dict[str, Any]:
    """返回不含 messages 的会话元信息。
    
    实时流只能更新状态、分支、PR 等元信息，不能把历史 messages 重新推给前端，
    否则会覆盖前端当前轮已经追加的用户输入和流式正文。
    
    Args:
        thread: 包含会话原始字段的字典。
    
    Returns:
        去除 messages 后的 thread DTO 字典。
    """

    payload = thread_payload(thread, include_versions=True)
    payload.pop("messages", None)
    return payload
