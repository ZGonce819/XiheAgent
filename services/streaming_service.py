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

from agent.runtime.events import record_event
from agent.runtime.task_router import get_task, initialize_task_record, run_agent_task
from config.app_config import get_settings
from services.redis_stream import (
    cleanup_stream,
    ensure_consumer_group,
    publish_event,
    read_events,
)
from services.thread_service import thread_meta_payload, user_visible_stream_text

logger = logging.getLogger(__name__)


def sse_part(event: str, data: dict[str, Any]) -> str:
    """格式化单个 SSE 事件片段。

    采用 ``event: xxx`` + ``data: json`` 标准格式，与前端 SSE 解析器对齐。
    """

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False, default=str)}\n\n"


def build_streaming_response(
    *,
    thread_id: str,
    repo_url: str,
    content: str,
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

    initialize_task_record(repo_url=repo_url, prompt=content, thread_id=thread_id)
    initial_task = get_task(thread_id)
    if initial_task is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=500, detail="task was not persisted")

    settings = get_settings()
    use_redis = settings.use_redis_stream

    async def event_iter():
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        loop = asyncio.get_running_loop()

        def enqueue(event: str, data: dict[str, Any]) -> None:
            payload = dict(data)
            payload.setdefault("thread_id", thread_id)
            loop.call_soon_threadsafe(queue.put_nowait, (event, payload))

        def event_sink(event: str, data: dict[str, Any]) -> None:
            payload = dict(data)
            if event == "text_delta":
                payload["content"] = user_visible_stream_text(
                    str(payload.get("content") or "")
                )
                if not payload["content"].strip():
                    return
            if use_redis:
                publish_event(thread_id, event, payload)
            else:
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
                enqueue("thread_done", thread_meta_payload(latest))
                enqueue("done", {})
            except Exception as exc:
                latest = get_task(thread_id) or initial_task
                enqueue("error", {"message": str(exc), "detail": str(exc)})
                enqueue("thread_done", thread_meta_payload(latest))
                enqueue("done", {})

        yield sse_part("thread_snapshot", thread_meta_payload(initial_task))
        yield sse_part(
            "user_message",
            {
                "thread_id": thread_id,
                "message_id": f"{thread_id}-user-{uuid.uuid4()}",
                "author": "user",
                "content": content,
            },
        )
        startup_message_id = f"{thread_id}-assistant-startup-{uuid.uuid4()}"
        yield sse_part(
            "message_start",
            {
                "thread_id": thread_id,
                "message_id": startup_message_id,
                "author": "agent",
            },
        )
        yield sse_part(
            "text_delta",
            {
                "thread_id": thread_id,
                "message_id": startup_message_id,
                "content": "正在理解需求并准备仓库上下文...\n\n",
                "mode": "append",
            },
        )

        if use_redis:
            ensure_consumer_group(thread_id)

        thread = threading.Thread(
            target=worker,
            name=f"xihe-stream-{thread_id}",
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
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
