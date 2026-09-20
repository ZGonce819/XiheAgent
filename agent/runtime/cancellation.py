"""运行中任务的协作式停止。

前端「停止」按钮要能中断一轮正在跑的 Agent。这里做的是**协作式**停止，不是强杀：

- Agent 跑在 ``services/streaming_service`` 的 worker 线程里，Python 线程无法安全强杀；
- LangGraph 的流式迭代也必须在一个一致的位置退出，否则 checkpoint 与 runs 表会留下
  一个永远处于 ``running`` 的记录。

所以做法是：按会话 ID 记一个停止请求，运行循环在**每两个协议事件之间**检查它，
检查到就抛 :class:`AgentRunCancelledError`，由调用方把这一轮落成 ``cancelled`` 终态。

停止的粒度：当前正在生成的这一段模型输出会丢，已经写进 checkpoint 的内容保留。
也就是说「停止」= 不再继续下一步，而不是回滚。

本模块在 import 期**不依赖项目内任何模块**（只在 :func:`mark_run_cancelled` 内部
按需延迟导入），因为 ``api`` / ``services`` / ``agent`` 三层都要用它，模块级依赖会
形成环。
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)


class AgentRunCancelledError(Exception):
    """这一轮运行被用户主动停止。

    与普通异常区分开：它表示「用户不想继续了」，不是失败。调用方必须把它
    记成 ``cancelled``，不能记成 ``failed`` —— 否则自动化任务的连续失败计数
    会把一次主动停止也算进去。
    """

    def __init__(self, thread_id: str) -> None:
        """记录被停止的会话 ID。

        Args:
            thread_id: 被停止的会话 ID。
        """

        super().__init__(f"运行已被用户停止：thread_id={thread_id}")
        self.thread_id = thread_id


_LOCK = threading.Lock()
# thread_id → 嵌套深度。一次运行可能有多层（SSE worker 先登记，Agent 运行层再登记），
# 只有最外层登记时才清掉残留的停止请求，否则内层会把外层刚收到的停止请求清掉。
_ACTIVE: dict[str, int] = {}
_CANCELLED: set[str] = set()


def begin_run(thread_id: str) -> None:
    """标记会话进入运行态，并清掉可能残留的停止请求。

    清残留是必要的：用户可能在上一轮刚结束时点到「停止」，那个标志不能
    影响下一轮。可重入——嵌套调用只有最外层会清。

    Args:
        thread_id: 会话 ID。
    """

    with _LOCK:
        depth = _ACTIVE.get(thread_id, 0) + 1
        _ACTIVE[thread_id] = depth
        if depth == 1:
            _CANCELLED.discard(thread_id)


def end_run(thread_id: str) -> None:
    """退出运行态，最外层退出时清掉停止请求。

    Args:
        thread_id: 会话 ID。
    """

    with _LOCK:
        depth = _ACTIVE.get(thread_id, 0) - 1
        if depth > 0:
            _ACTIVE[thread_id] = depth
            return
        _ACTIVE.pop(thread_id, None)
        _CANCELLED.discard(thread_id)


def request_cancel(thread_id: str) -> bool:
    """请求停止该会话当前这一轮。

    Args:
        thread_id: 会话 ID。

    Returns:
        ``True`` 表示确实有一轮在跑、停止请求已记下；``False`` 表示当前没有
        运行中的轮次，调用方应当据此告诉用户「没有正在运行的任务」。
    """

    with _LOCK:
        if thread_id not in _ACTIVE:
            return False
        _CANCELLED.add(thread_id)
        return True


def is_cancel_requested(thread_id: str) -> bool:
    """该会话是否已被请求停止。

    Args:
        thread_id: 会话 ID。

    Returns:
        是否已请求停止。
    """

    with _LOCK:
        return thread_id in _CANCELLED


def raise_if_cancelled(thread_id: str) -> None:
    """已请求停止时抛出 :class:`AgentRunCancelledError`。

    运行循环在每两个协议事件之间调用它。

    Args:
        thread_id: 会话 ID。

    Raises:
        AgentRunCancelledError: 该会话已被请求停止。
    """

    if is_cancel_requested(thread_id):
        raise AgentRunCancelledError(thread_id)


def mark_run_cancelled(*, thread_id: str, run_id: str | None = None) -> None:
    """把被停止的这一轮落成 ``cancelled`` 终态。

    thread 与 run 两处都要写：只写一处的话，刷新页面会从另一处读回 ``running``，
    界面上就留下一个永远在跑的会话。

    这里整体容错：收尾失败只记日志，不能让「停止」这个动作本身再抛一次错。

    Args:
        thread_id: 会话 ID。
        run_id: 运行 ID；为 ``None`` 时只收尾 thread 与展示事件。
    """

    # 延迟导入：本模块被 api / services / agent 三层共用，模块级导入会形成环。
    from agent.graph.graph import get_store
    from agent.runtime.events import record_event

    try:
        store = get_store()
    except Exception:
        logger.exception("停止收尾时拿不到 Store：thread_id=%s", thread_id)
        return

    try:
        store.finish_open_run_events(thread_id, status="cancelled")
    except Exception:
        logger.exception("停止收尾时关闭展示事件失败：thread_id=%s", thread_id)

    try:
        store.update_thread_status(thread_id, "cancelled")
    except Exception:
        logger.exception("停止收尾时更新会话状态失败：thread_id=%s", thread_id)

    if run_id:
        try:
            store.record_run(
                run_id=run_id,
                thread_id=thread_id,
                status="cancelled",
                error="用户主动停止",
                finished=True,
            )
        except Exception:
            logger.exception("停止收尾时更新运行记录失败：thread_id=%s", thread_id)

    record_event(
        thread_id,
        "cancelled",
        "任务已停止",
        kind="other",
        status="rejected",
        detail="用户主动停止，未执行完的步骤不会继续。",
    )
