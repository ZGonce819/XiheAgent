"""运行步骤事件记录。

独立于 runtime 的轻量写入模块，避免 tools 导入 runtime 时形成循环依赖。
tools 在执行过程中需要向前端写入步骤状态，但不能反向依赖 runtime 的完整调用链。
"""

from __future__ import annotations

import logging

from store import LocalMySQLStore

_event_store: LocalMySQLStore | None = None
logger = logging.getLogger(__name__)


def _get_event_store() -> LocalMySQLStore:
    """获取专用于事件写入的 Store 实例。

    避免从这里导入 ``graph.get_store``，否则会形成 runtime/graph/tools 的循环导入。
    """

    global _event_store
    if _event_store is None:
        _event_store = LocalMySQLStore()
    return _event_store


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
    except Exception:
        logger.exception("记录运行步骤失败：thread_id=%s key=%s title=%s", thread_id, key, title)
