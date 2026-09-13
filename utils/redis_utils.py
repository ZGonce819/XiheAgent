"""Redis Stream 事件总线工具。

可选启用的 SSE 事件分发方案，用于多进程或多实例部署场景。

当 ``USE_REDIS_STREAM=true`` 时，Agent 运行产生的事件会写入 Redis Stream，
前端 SSE 连接从对应的 consumer group 中读取事件。这样即使 Agent 运行在
进程 A，前端连接在进程 B，事件也能正确推送。

不启用时（默认），SSE 事件通过 asyncio.Queue 在单进程内直接推送。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from config.constants import CONSUMER_GROUP, STREAM_KEY_PREFIX
from database.redis import get_redis_stream_connection

logger = logging.getLogger(__name__)


def _stream_key(thread_id: str) -> str:
    """生成 thread 对应的 Redis Stream key。

    Args:
        thread_id: 会话 ID。

    Returns:
        带全局前缀的完整 Stream key。
    """

    return f"{STREAM_KEY_PREFIX}{thread_id}"


def ensure_consumer_group(thread_id: str) -> None:
    """确保 Redis Stream 的 consumer group 已创建。

    首次为某个 thread 推送事件前调用。如果 group 已存在则忽略。

    Args:
        thread_id: 目标会话 ID。

    Returns:
        无返回值。
    """

    r = get_redis_stream_connection()
    try:
        r.xgroup_create(_stream_key(thread_id), CONSUMER_GROUP, id="0", mkstream=True)
    except Exception as exc:
        if "BUSYGROUP" not in str(exc):
            raise
        logger.debug("Consumer group 已存在：thread_id=%s", thread_id)


def publish_event(thread_id: str, event: str, data: dict[str, Any]) -> None:
    """把 SSE 事件写入 Redis Stream。

    Args:
        thread_id: 目标会话 ID。
        event: SSE 事件名称，例如 ``text_delta``、``thread_done``。
        data: 事件负载，会被 JSON 序列化后写入。

    Returns:
        无返回值。
    """

    r = get_redis_stream_connection()
    payload = json.dumps(data, ensure_ascii=False, default=str)
    r.xadd(_stream_key(thread_id), {"event": event, "data": payload})


def read_events(
    thread_id: str,
    *,
    consumer_name: str = "dashboard",
    count: int = 50,
    block_ms: int = 5000,
) -> list[tuple[str, dict[str, Any]]]:
    """从 Redis Stream 中读取未消费的 SSE 事件。

    Args:
        thread_id: 目标会话 ID。
        consumer_name: 当前消费者名称，用于 consumer group 内的负载均衡。
        count: 单次最多读取的事件数。
        block_ms: 无事件时的阻塞等待毫秒数，0 表示不阻塞。

    Returns:
        事件列表，每项为 ``(event_name, data_dict)`` 元组。
    """

    ensure_consumer_group(thread_id)
    r = get_redis_stream_connection()
    results = r.xreadgroup(
        groupname=CONSUMER_GROUP,
        consumername=consumer_name,
        streams={_stream_key(thread_id): ">"},
        count=count,
        block=block_ms,
    )
    events: list[tuple[str, dict[str, Any]]] = []
    for _stream_name, messages in results:
        for message_id, fields in messages:
            event_name = fields.get("event", "")
            try:
                data = json.loads(fields.get("data", "{}"))
            except (json.JSONDecodeError, TypeError):
                data = {}
            events.append((event_name, data))
            r.xack(_stream_key(thread_id), CONSUMER_GROUP, message_id)
    return events


def cleanup_stream(thread_id: str) -> None:
    """删除 thread 对应的 Redis Stream，释放内存。

    在 thread 被删除或确认不再需要实时推送时调用。

    Args:
        thread_id: 目标会话 ID。

    Returns:
        无返回值。
    """

    r = get_redis_stream_connection()
    r.delete(_stream_key(thread_id))
