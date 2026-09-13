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
from schemas.request_schema import DashboardThreadMessageRequest
from services.streaming_service import build_streaming_response
from services.thread_service import thread_payload
from utils.platform_utils import (normalize_repo_url)
from utils.redis_utils import cleanup_stream

dashboard_router = APIRouter(prefix="/dashboard/api")


@dashboard_router.get("/me")
def dashboard_me() -> dict[str, Any]:
    """返回当前用户信息。

    Returns:
        包含登录名、邮箱、头像和管理员状态的字典。
    """
    return {
        "login": "xihe",
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
    return build_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content)


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
