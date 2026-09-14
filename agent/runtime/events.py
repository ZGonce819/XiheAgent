"""运行步骤事件记录。

独立于 runtime 的轻量写入模块，避免 tools 导入 runtime 时形成循环依赖。
tools 在执行过程中需要向前端写入步骤状态，但不能反向依赖 runtime 的完整调用链。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from store import LocalMySQLStore

_event_store: LocalMySQLStore | None = None
_sse_callbacks: dict[
    str,
    Callable[[str, str, str, str, str, str | None], None],
] = {}
logger = logging.getLogger(__name__)


def _get_event_store() -> LocalMySQLStore:
    """获取专用于事件写入的 Store 实例。

    避免从这里导入 ``graph.get_store``，否则会形成 runtime/graph/tools 的循环导入。

    Returns:
        事件写入专用的 LocalMySQLStore 单例。
    """

    global _event_store
    if _event_store is None:
        _event_store = LocalMySQLStore()
    return _event_store


def register_sse_callback(
    thread_id: str,
    callback: Callable[[str, str, str, str, str, str | None], None],
) -> None:
    """为指定会话注册 SSE 推送回调。

    Args:
        thread_id: 回调所属会话 ID。
        callback: 接收 ``(thread_id, key, kind, title, status, detail)`` 的回调函数。
    """

    _sse_callbacks[thread_id] = callback


def clear_sse_callback(thread_id: str) -> None:
    """清除指定会话的 SSE 推送回调。"""

    _sse_callbacks.pop(thread_id, None)


def record_event(
    thread_id: str,
    key: str,
    title: str,
    *,
    kind: str = "think",
    status: str = "in_progress",
    detail: str | None = None,
) -> None:
    """写入一个可展示在前端的运行步骤。

    这个模块独立于 runtime，避免 tools 导入 runtime 时形成循环依赖。
    写入失败时只记录日志，不抛出异常，避免影响主流程。

    Args:
        thread_id: 当前线程 ID。
        key: 事件唯一标识键，与 thread_id 拼接为 event_id。
        title: 事件标题，前端展示用。
        kind: 事件类型，如 ``"think"``、``"execute"``、``"search"`` 等。
        status: 事件状态，如 ``"in_progress"``、``"completed"``、``"error"``。
        detail: 事件详情 JSON 字符串；为 ``None`` 时不写入详情。
    """

    try:
        _get_event_store().add_run_event(
            event_id=f"{thread_id}:{key}",
            thread_id=thread_id,
            kind=kind,
            title=title,
            status=status,
            detail=detail,
        )
        callback = _sse_callbacks.get(thread_id)
        if callback is not None:
            try:
                callback(thread_id, key, kind, title, status, detail)
            except Exception:
                logger.debug("SSE 推送回调失败：thread_id=%s key=%s", thread_id, key)
    except Exception:
        logger.exception(
            "记录运行步骤失败：thread_id=%s key=%s title=%s",
            thread_id,
            key,
            title,
        )
