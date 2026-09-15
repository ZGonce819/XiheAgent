"""Dashboard 前端接口。

提供会话管理、SSE 流式输出等 Dashboard 所需的 HTTP 接口。
前端通过 POST SSE 实时接收 Agent 运行事件和流式正文。
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from agent.runtime import (
    delete_task,
    get_task,
    list_tasks,
)
from config.app_config import get_settings
from config.env_config import get_env
from schemas.request_schema import DashboardThreadMessageRequest, DashboardEditPreviewRequest
from services.thread_service import fetch_visible_messages
from services.streaming_service import build_streaming_response
from services.thread_service import thread_payload
from utils.platform_utils import (normalize_repo_url)
from utils.redis_utils import cleanup_stream

dashboard_router = APIRouter(prefix="/dashboard/api")


def _is_latest_user_message(message_id: str, thread_id: str, messages: list[dict[str, Any]], original_content: str | None = None) -> bool:
    """校验消息确实是当前线程最后一条用户消息。

    实时 SSE 消息使用 ``<thread>-user-<run>`` ID，历史 checkpoint 可能使用
    LangChain 原始 UUID。两种 ID 都只允许匹配最后一条用户消息；线程前缀
    只是兼容实时消息，不会放宽到任意旧消息。
    """
    user_messages = [message for message in messages if message.get("role") == "user"]
    if not user_messages:
        return False
    latest_id = str(user_messages[-1].get("id") or "")
    if latest_id == message_id or message_id.startswith(f"{thread_id}-user-"):
        return True
    if original_content is not None:
        return str(user_messages[-1].get("content") or "").strip() == original_content.strip()
    return False


@dashboard_router.get("/me")
def dashboard_me() -> dict[str, Any]:
    """返回当前用户信息。

    Returns:
        包含登录名、邮箱、头像和管理员状态的字典。
    """
    return {
        "login": "XiheAgent",
        "email": None,
        "avatar_url": None,
        "is_admin": True,
        "slack_oauth_enabled": False,
    }


@dashboard_router.get("/options")
def dashboard_options() -> dict[str, Any]:
    """返回页面选项（模型列表等）。

    Returns:
        包含可用模型列表和默认配置的字典。
    """
    model = get_env("MAIN_MODEL", "deepseek-v4-pro")
    return {
        "models": [
            {
                "id": model,
                "label": model,
                "efforts": ["default"],
                "default_effort": "default",
                "supports_images": False,
            }
        ],
        "default_agent_model": model,
        "default_agent_reasoning_effort": "default",
        "default_agent_subagent_model": model,
        "default_agent_subagent_reasoning_effort": "default",
    }


@dashboard_router.get("/threads")
def dashboard_threads(limit: int = 50) -> list[dict[str, Any]]:
    """返回会话列表。

    Args:
        limit: 最大返回会话数，默认 50。

    Returns:
        会话摘要字典列表。
    """
    return [thread_payload(thread) for thread in list_tasks(limit=limit)]


@dashboard_router.get("/threads/{thread_id}")
def dashboard_thread_detail(thread_id: str) -> dict[str, Any]:
    """返回单个会话详情（含消息历史）。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        会话详情字典。

    Raises:
        HTTPException: 会话不存在时返回 404。
    """
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread_payload(task)


@dashboard_router.post("/threads/stream-message")
async def dashboard_stream_new_message(body: DashboardThreadMessageRequest) -> StreamingResponse:
    """创建新会话并开始流式对话。

    Args:
        body: 请求体，包含仓库地址和首条消息内容。

    Returns:
        SSE 流式响应。

    Raises:
        HTTPException: 仓库地址解析失败时返回 400。
    """
    try:
        repo_url = normalize_repo_url(body.repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    thread_id = str(uuid.uuid4())
    return build_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content)


@dashboard_router.post("/threads/{thread_id}/stream-message")
async def dashboard_stream_existing_message(
    thread_id: str,
    body: DashboardThreadMessageRequest,
) -> StreamingResponse:
    """向已有会话追加对话轮次。

    Args:
        thread_id: 目标会话唯一标识。
        body: 请求体，包含仓库地址和消息内容。

    Returns:
        SSE 流式响应。

    Raises:
        HTTPException: 仓库地址解析失败时返回 400。
    """
    task = get_task(thread_id)
    try:
        repo_url = normalize_repo_url(body.repo, fallback=(task or {}).get("repo_url"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.edit_message_id:
        if not body.rollback_confirmed:
            raise HTTPException(status_code=400, detail="请先确认回退影响")
        messages = fetch_visible_messages(thread_id)
        if not _is_latest_user_message(body.edit_message_id, thread_id, messages, body.edit_message_content):
            raise HTTPException(status_code=409, detail="只能编辑最近一条用户消息")
    try:
        return build_streaming_response(
            thread_id=thread_id,
            repo_url=repo_url,
            content=body.content,
            rollback_message_id=body.edit_message_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@dashboard_router.post("/threads/{thread_id}/edit-preview")
def dashboard_edit_preview(thread_id: str, body: DashboardEditPreviewRequest) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    messages = fetch_visible_messages(thread_id)
    if not _is_latest_user_message(body.message_id, thread_id, messages, body.original_content):
        raise HTTPException(status_code=409, detail="只能编辑最近一条用户消息")
    index = next((i for i, message in enumerate(messages) if message.get("id") == body.message_id), len(messages) - 1)
    return {
        "message_id": body.message_id,
        "messages_to_replace": max(0, len(messages) - index),
        "runs_to_replace": 1 if task.get("latest_run") else 0,
        "code_changes_detected": any(
            event.get("kind") in {"edit", "delete", "move"}
            for event in (task.get("run_events") or [])
        ),
        "requires_rollback": bool(task.get("run_events") or len(messages) > index + 1),
    }


@dashboard_router.delete("/threads/{thread_id}", status_code=204)
def dashboard_delete_thread(thread_id: str) -> None:
    """删除指定会话及其关联数据。

    Args:
        thread_id: 待删除会话的唯一标识。

    Raises:
        HTTPException: 会话不存在时返回 404。
    """
    deleted = delete_task(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    if get_settings().use_redis_stream:
        cleanup_stream(thread_id)
    return None
