"""Dashboard 前端接口。

提供会话管理、SSE 流式输出等 Dashboard 所需的 HTTP 接口。
前端通过 POST SSE 实时接收 Agent 运行事件和流式正文。
"""

from __future__ import annotations

import asyncio
import json
import re
import threading
import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.graph.checkpoint_history import visible_checkpoint_messages
from agent.runtime import (
    delete_task,
    get_task,
    initialize_task_record,
    list_tasks,
    run_agent_task,
)
from config.env_config import get_env

dashboard_router = APIRouter(prefix="/dashboard/api")

DEFAULT_REPO_URL = "https://gitee.com/msb-goldbin/ai_coding.git"


# ---------------------------------------------------------------------------
# 请求体
# ---------------------------------------------------------------------------


class DashboardThreadMessageRequest(BaseModel):
    """Dashboard 发送消息的请求体。"""

    content: str
    images: list[dict[str, Any]] | None = None
    repo: str | None = None
    model_id: str | None = None
    effort: str | None = None


# ---------------------------------------------------------------------------
# 内部辅助函数
# ---------------------------------------------------------------------------


def _normalize_dashboard_repo_url(repo: str | None, *, fallback: str | None = None) -> str:
    """把前端传入的仓库字段统一整理成后端运行时可解析的 Gitee URL。

    前端为了方便展示和输入，通常使用 ``owner/repo`` 这种简写；但运行时、
    仓库映射、仓库记忆和 Gitee API 封装都统一依赖完整 URL。这里在 Dashboard
    API 边界做一次规范化，避免把 ``owner/repo`` 直接传给
    ``parse_gitee_repo_url`` 后触发 500。

    支持格式：
    - ``https://gitee.com/owner/repo``
    - ``https://gitee.com/owner/repo.git``
    - ``owner/repo``
    """
    text = (repo or fallback or DEFAULT_REPO_URL).strip()
    if not text:
        return DEFAULT_REPO_URL

    if text.startswith(("http://", "https://")):
        parsed = text.strip().removesuffix(".git").rstrip("/")
        if re.fullmatch(r"https?://gitee\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", parsed):
            return text
        raise HTTPException(
            status_code=400,
            detail="仅支持 Gitee 仓库地址（https://gitee.com/owner/repo）。",
        )

    shorthand = text.removesuffix(".git").strip().strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", shorthand):
        return f"https://gitee.com/{shorthand}.git"

    raise HTTPException(
        status_code=400,
        detail="Gitee 仓库地址格式不正确，请输入完整 Gitee URL 或 owner/repo。",
    )


def _sse_part(event: str, data: dict[str, Any]) -> str:
    """输出标准命名 SSE 事件。

    POST SSE 主链路采用 ``event: xxx`` + ``data: json``，
    前端解析器直接按命名事件处理，不需要把 event 包在 data JSON 里。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def _timestamp_ms(value: str | None) -> int:
    """把 ISO 时间戳转换成前端使用的毫秒时间戳。"""
    if not value:
        return int(datetime.now().timestamp() * 1000)
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)


def _status_for_frontend(status: str | None) -> str:
    """把后端运行状态映射成前端 AgentStatus。"""
    if status in {"running", "pushed", "pr_created"}:
        return "running"
    if status in {"completed", "awaiting_approval"}:
        return "finished"
    if status == "failed":
        return "error"
    return "idle"


def _repo_full_name(thread: dict[str, Any]) -> str:
    """拼接仓库全名（owner/repo）。"""
    owner = thread.get("repo_owner") or ""
    repo = thread.get("repo_name") or ""
    if owner and repo:
        return f"{owner}/{repo}"
    return thread.get("repo_url") or ""


def _pr_payload(thread: dict[str, Any]) -> dict[str, Any] | None:
    """从会话数据中提取 Pull Request 信息。"""
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
    不再过滤 DeepAgents 产生的英文摘要或过程文本。
    Store、run_events 是否参与正文展示，由 ``_message_payload`` 的数据来源控制，
    不靠文本内容做二次过滤。
    """
    return text.strip()


def _user_visible_stream_text(text: str) -> str:
    """返回流式正文。

    POST SSE 中的 assistant token/chunk 直接给前端展示。
    空白文本仍由调用方丢弃，避免产生空消息。
    """
    return text


def _message_payload(thread: dict[str, Any]) -> list[dict[str, Any]]:
    """生成前端可展示的消息列表。

    历史正文只从 LangGraph checkpoint 读取。Store 仍然记录业务数据、
    run_events、review findings 等内容，但不参与聊天正文拼接，避免
    checkpoint 和 Store 双数据源导致页面重复、覆盖或乱序。

    当前轮实时过程由 POST SSE 直接推送增量事件；刷新页面后由 checkpoint 恢复稳定历史。
    """
    created_at = thread.get("created_at") or datetime.now().isoformat()
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


def _thread_payload(thread: dict[str, Any]) -> dict[str, Any]:
    """生成完整的会话详情（含 messages）。"""
    repo_name = _repo_full_name(thread)
    return {
        "id": thread["thread_id"],
        "title": thread.get("title") or "Xihe Task",
        "repo": repo_name,
        "repoFullName": repo_name,
        "branch": thread.get("branch_name") or "master",
        "model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "effort": None,
        "source": "dashboard",
        "status": _status_for_frontend(thread.get("latest_run_status")),
        "createdAt": _timestamp_ms(thread.get("created_at")),
        "updatedAt": _timestamp_ms(thread.get("updated_at")),
        "messages": _message_payload(thread),
        "pr": _pr_payload(thread),
        "latestPlan": None,
        "diffStats": None,
        "changedFiles": [],
    }


def _thread_meta_payload(thread: dict[str, Any]) -> dict[str, Any]:
    """返回不含 messages 的会话元信息。

    实时流只能更新状态、分支、PR 等元信息，不能把历史 messages 重新推给前端，
    否则会覆盖前端当前轮已经追加的用户输入和流式正文。
    """
    payload = _thread_payload(thread)
    payload.pop("messages", None)
    return payload


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------


@dashboard_router.get("/me")
def dashboard_me() -> dict[str, Any]:
    """返回当前用户信息。"""
    return {
        "login": "xihe",
        "email": None,
        "avatar_url": None,
        "is_admin": True,
        "slack_oauth_enabled": False,
    }


@dashboard_router.get("/options")
def dashboard_options() -> dict[str, Any]:
    """返回页面选项（模型列表等）。"""
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
    """返回会话列表。"""
    return [_thread_payload(thread) for thread in list_tasks(limit=limit)]


@dashboard_router.get("/threads/{thread_id}")
def dashboard_thread_detail(thread_id: str) -> dict[str, Any]:
    """返回单个会话详情（含消息历史）。"""
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return _thread_payload(task)


def _post_streaming_response(
    *, thread_id: str, repo_url: str, content: str
) -> StreamingResponse:
    """直接执行本轮 Agent，并把过程事件作为 POST SSE 响应返回。

    新链路：请求体中携带本轮用户输入，后端在同一条 StreamingResponse 中
    先发送 user_message，再直接运行 Agent。Store 仍会记录任务摘要、PR、
    run_events 和仓库记忆，但前端实时正文不再依赖 Store 轮询。
    """
    initialize_task_record(repo_url=repo_url, prompt=content, thread_id=thread_id)
    initial_task = get_task(thread_id)
    if initial_task is None:
        raise HTTPException(status_code=500, detail="task was not persisted")

    async def event_iter():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

        def enqueue(event: str, data: dict[str, Any]) -> None:
            payload = dict(data)
            payload.setdefault("thread_id", thread_id)
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def event_sink(event: str, data: dict[str, Any]) -> None:
            payload = dict(data)
            if event == "text_delta":
                payload["content"] = _user_visible_stream_text(
                    str(payload.get("content") or "")
                )
                if not payload["content"].strip():
                    return
            enqueue(event, payload)

        def worker() -> None:
            try:
                run_agent_task(
                    repo_url=repo_url,
                    prompt=content,
                    thread_id=thread_id,
                    event_sink=event_sink,
                )
                latest = get_task(thread_id) or initial_task
                enqueue("thread_done", _thread_meta_payload(latest))
                enqueue("done", {})
            except Exception as exc:
                latest = get_task(thread_id) or initial_task
                enqueue("error", {"message": str(exc), "detail": str(exc)})
                enqueue("thread_done", _thread_meta_payload(latest))
                enqueue("done", {})

        yield _sse_part("thread_snapshot", _thread_meta_payload(initial_task))
        yield _sse_part(
            "user_message",
            {
                "thread_id": thread_id,
                "message_id": f"{thread_id}-user-{uuid.uuid4()}",
                "author": "user",
                "content": content,
            },
        )
        startup_message_id = f"{thread_id}-assistant-startup-{uuid.uuid4()}"
        yield _sse_part(
            "message_start",
            {
                "thread_id": thread_id,
                "message_id": startup_message_id,
                "author": "agent",
            },
        )
        yield _sse_part(
            "text_delta",
            {
                "thread_id": thread_id,
                "message_id": startup_message_id,
                "content": "正在理解需求并准备仓库上下文...\n\n",
                "mode": "append",
            },
        )

        thread = threading.Thread(
            target=worker, name=f"xihe-stream-{thread_id}", daemon=True
        )
        thread.start()

        while True:
            event, data = await queue.get()
            yield _sse_part(event, data)
            if event == "done":
                break

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@dashboard_router.post("/threads/stream-message")
async def dashboard_stream_new_message(body: DashboardThreadMessageRequest) -> StreamingResponse:
    """创建新会话并开始流式对话。"""
    repo_url = _normalize_dashboard_repo_url(body.repo)
    thread_id = str(uuid.uuid4())
    return _post_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content)


@dashboard_router.post("/threads/{thread_id}/stream-message")
async def dashboard_stream_existing_message(
    thread_id: str,
    body: DashboardThreadMessageRequest,
) -> StreamingResponse:
    """向已有会话追加对话轮次。"""
    task = get_task(thread_id)
    repo_url = _normalize_dashboard_repo_url(
        body.repo, fallback=(task or {}).get("repo_url")
    )
    return _post_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content)


@dashboard_router.delete("/threads/{thread_id}", status_code=204)
def dashboard_delete_thread(thread_id: str) -> None:
    """删除指定会话及其关联数据。"""
    deleted = delete_task(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    return None
