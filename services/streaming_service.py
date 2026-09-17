"""SSE 流式编排服务。

负责把一次用户输入转换成 POST SSE 响应，支持两种事件分发模式：

- **asyncio.Queue 模式**（默认）：单进程内直接推送，适合开发和个人使用。
- **Redis Stream 模式**（可选）：通过 Redis Stream 分发事件，适合多进程/多实例部署。

核心职责：
1. 初始化 thread 记录，构造 SSE 事件迭代器。
2. 在后台线程中运行 Agent，把事件通过队列或 Redis Stream 推给前端。
3. 提供标准 SSE 格式化工具函数。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import uuid
from typing import Any

from fastapi.responses import StreamingResponse

from agent.runtime.events import clear_sse_callback, register_sse_callback
from agent.runtime.task_router import (
    get_task,
    initialize_task_record,
    resume_agent_task,
    run_agent_task,
)
from config.approval_runtime import get_pending_approval
from agent.graph.checkpoint_history import checkpoint_before_latest_user
from agent.graph.graph import get_checkpointer
from config.app_config import get_settings
from config.constants import SSE_HEADERS
from services.thread_service import thread_meta_payload
from utils.redis_utils import (
    cleanup_stream,
    ensure_consumer_group,
    publish_event,
    read_events,
)
from utils.thread_utils import user_visible_stream_text

logger = logging.getLogger(__name__)


def sse_part(event: str, data: dict[str, Any]) -> str:
    """格式化单个 SSE 事件片段。

    采用 ``event: xxx`` + ``data: json`` 标准格式，与前端 SSE 解析器对齐。

    Args:
        event: 事件名称。
        data: 事件负载字典。

    Returns:
        符合 SSE 协议的文本片段字符串。
    """

    encoded = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {encoded}\n\n"


def build_streaming_response(
    *,
    thread_id: str,
    repo_url: str,
    content: str,
    rollback_message_id: str | None = None,
    rollback_message_content: str | None = None,
    model_id: str | None = None,
    backend_type: str | None = None,
    approval_decision: str | None = None,
    approval_id: str | None = None,
) -> StreamingResponse:
    """构造 POST SSE 流式响应。

    请求体中携带本轮用户输入，后端在同一条 StreamingResponse 中先发送
    ``user_message``，再直接运行 Agent。Store 仍会记录任务摘要、PR、
    run_events 和仓库记忆，但前端实时正文不再依赖 Store 轮询。

    Args:
        thread_id: 当前会话 ID。
        repo_url: 规范化后的 Gitee 仓库 URL。
        content: 用户本轮输入文本。

    Returns:
        FastAPI ``StreamingResponse``，媒体类型为 ``text/event-stream``。
    """

    checkpoint_id: str | None = None
    if rollback_message_id:
        checkpointer = get_checkpointer()
        redis_client = getattr(checkpointer, "_redis", None) or getattr(checkpointer, "client", None)
        if redis_client is not None:
            checkpoint_id = checkpoint_before_latest_user(redis_client, thread_id)
        # A first-turn edit has no parent checkpoint. Removing only the graph
        # state gives the edited prompt a clean branch while preserving the
        # dashboard thread and its audit records.
        if checkpoint_id is None:
            # 某些 RedisSaver 版本在分支运行后无法枚举旧父节点。此时
            # 当前请求已经通过前端确认了回退影响，清理图状态并从编辑后的
            # 消息建立新分支，保证连续编辑仍可用。
            if redis_client is None:
                raise RuntimeError("无法访问会话 checkpoint，已拒绝回退")
            try:
                checkpointer.delete_thread(thread_id)
            except Exception as exc:
                raise RuntimeError("无法清理首轮 checkpoint，已拒绝回退") from exc

    if approval_decision is None:
        initialize_task_record(repo_url=repo_url, prompt=content, thread_id=thread_id, backend_type=backend_type)
    initial_task = get_task(thread_id)
    if initial_task is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="task was not persisted")

    settings = get_settings()
    use_redis = settings.use_redis_stream

    pending = get_pending_approval(thread_id) if approval_decision else None
    run_id = str((pending or {}).get("runId") or uuid.uuid4())

    async def event_iter():
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()
        tool_group_index = 0
        active_tool_group_id: str | None = None

        def send_event(event: str, data: dict[str, Any]) -> None:
            """统一事件发送：Redis Stream 模式走 Redis，否则走 asyncio.Queue。"""
            payload = dict(data)
            payload.setdefault("thread_id", thread_id)
            if use_redis:
                publish_event(thread_id, event, payload)
            else:
                loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def event_sink(event: str, data: dict[str, Any]) -> None:
            nonlocal active_tool_group_id
            payload = dict(data)
            if event == "text_delta":
                payload["content"] = user_visible_stream_text(
                    str(payload.get("content") or "")
                )
                if not payload["content"].strip():
                    return
            if event in {"text_delta", "todo_delta"}:
                active_tool_group_id = None
            send_event(event, payload)

        def sse_callback(
            callback_thread_id: str,
            key: str,
            kind: str,
            title: str,
            status: str,
            detail: str | None,
        ) -> None:
            nonlocal active_tool_group_id, tool_group_index
            # 根据 kind 字段推断 toolKind，供前端创建 ToolExecutionChunk
            # 映射关系：backend kind -> AcpToolKind
            kind_to_tool_kind = {
                "think": "think",
                "execute": "execute",
                "fetch": "fetch",
                "search": "search",
                "read": "read",
                "edit": "edit",
                "delete": "delete",
                "move": "move",
            }
            tool_kind = kind_to_tool_kind.get(kind, "other")
            process_key = key.split(":", 1)[0]
            is_process = process_key in {
                "created",
                "intent",
                "repo",
                "workspace",
                "sync",
                "agent",
                "model",
                "plan",
                "done",
                "failed",
            }

            tool_group_id: str | None = None
            if key.startswith("tool:"):
                if active_tool_group_id is None:
                    tool_group_index += 1
                    active_tool_group_id = f"{run_id}:tools:{tool_group_index}"
                tool_group_id = active_tool_group_id
            elif is_process:
                active_tool_group_id = None

            send_event("tool_update", {
                "thread_id": callback_thread_id,
                "run_id": run_id,
                "event_id": f"{callback_thread_id}:{key}",
                "event_key": key,
                "is_tool": key.startswith("tool:"),
                "is_process": is_process,
                "tool_group_id": tool_group_id,
                "kind": kind,
                "title": title,
                "status": status,
                "detail": detail,
                "tool_kind": tool_kind,
            })

        register_sse_callback(thread_id, sse_callback)

        def worker() -> None:
            try:
                if approval_decision is None:
                    result = run_agent_task(
                        repo_url=repo_url,
                        prompt=content,
                        thread_id=thread_id,
                        event_sink=event_sink,
                        run_id=run_id,
                        checkpoint_id=checkpoint_id,
                        model_id=model_id,
                        backend_type=backend_type,
                    )
                else:
                    result = resume_agent_task(
                        thread_id=thread_id,
                        decision=approval_decision,
                        approval_id=str(approval_id or ""),
                        event_sink=event_sink,
                    )
                if result.get("status") == "awaiting_approval":
                    send_event("approval_required", result.get("approval") or {})
                latest = get_task(thread_id) or initial_task
                send_event("thread_done", thread_meta_payload(latest))
                send_event("done", {})
            except Exception as exc:
                try:
                    latest = get_task(thread_id) or initial_task
                except Exception:
                    latest = initial_task
                send_event("error", {"message": str(exc), "detail": str(exc)})
                send_event("thread_done", thread_meta_payload(latest))
                send_event("done", {})
            finally:
                clear_sse_callback(thread_id)

        if approval_decision is None:
            yield sse_part("thread_snapshot", thread_meta_payload(initial_task))
        else:
            yield sse_part("approval_resumed", {"approval_id": approval_id})
        if rollback_message_id:
            yield sse_part(
                "rollback",
                {
                    "message_id": rollback_message_id,
                    "message_content": rollback_message_content,
                },
            )
        if approval_decision is None:
            yield sse_part(
                "user_message",
                {
                    "thread_id": thread_id,
                    "message_id": f"{thread_id}-user-{run_id}",
                    "author": "user",
                    "content": content,
                },
            )

        if use_redis:
            ensure_consumer_group(thread_id)

        thread = threading.Thread(
            target=worker,
            name=f"xihe-agent-stream-{thread_id}",
            daemon=True,
        )
        thread.start()

        try:
            if use_redis:
                while True:
                    events = await asyncio.to_thread(
                        read_events, thread_id, block_ms=5000
                    )
                    for event, data in events:
                        yield sse_part(event, data)
                        if event == "done":
                            return
            else:
                while True:
                    event, data = await queue.get()
                    yield sse_part(event, data)
                    if event == "done":
                        break
        finally:
            if use_redis:
                cleanup_stream(thread_id)

    return StreamingResponse(
        event_iter(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )
