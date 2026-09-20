"""运行中任务的协作式停止。

分两层测：

1. **登记表**（``agent.runtime.cancellation``）——「谁在跑、有没有被要求停」。
   这层全是纯函数，不打桩。
2. **停止收尾与接口**——``mark_run_cancelled`` 有没有把 thread 与 run 两处都落成终态；
   ``POST /threads/{id}/cancel`` 在「没有在跑」时会不会拒绝。

不测的部分：真的把一轮 Agent 跑起来再停。那需要真模型与真容器，属于端到端范畴，
放在这里会让整个测试套件从 45 秒变成不可接受的长度。停止点在运行循环里只有一行
``raise_if_cancelled(thread_id)``，由 ``test_raise_if_cancelled_*`` 覆盖其行为。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from agent.runtime import cancellation
from agent.runtime.cancellation import (
    AgentRunCancelledError,
    begin_run,
    end_run,
    is_cancel_requested,
    mark_run_cancelled,
    raise_if_cancelled,
    request_cancel,
)


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """每个用例都从干净的登记表开始。

    登记表是模块级全局状态，不隔离的话「上一轮留下的停止标志」会串到下一个用例里，
    而那种串味恰恰是本文件要防的问题之一。
    """

    monkeypatch.setattr(cancellation, "_ACTIVE", {})
    monkeypatch.setattr(cancellation, "_CANCELLED", set())


def test_cancel_without_a_running_round_is_refused() -> None:
    """没有在跑的时候点停止，要被明确拒绝，而不是记下一个悬空标志。

    记下悬空标志的后果是：用户这次点了没反应，下一次真正开跑时却在第一个事件
    就被停掉 —— 看起来像「任务自己莫名其妙停了」。
    """

    assert request_cancel("t-idle") is False
    assert is_cancel_requested("t-idle") is False


def test_cancel_during_a_round_is_recorded_and_raises() -> None:
    """在跑的时候点停止：登记成功，运行循环下一次检查就抛。"""

    begin_run("t-run")
    assert request_cancel("t-run") is True
    assert is_cancel_requested("t-run") is True

    with pytest.raises(AgentRunCancelledError):
        raise_if_cancelled("t-run")


def test_end_run_clears_both_active_and_requested() -> None:
    """一轮结束后既不算「在跑」，也不该留下停止标志。"""

    begin_run("t-run")
    request_cancel("t-run")
    end_run("t-run")

    assert request_cancel("t-run") is False
    assert is_cancel_requested("t-run") is False


def test_nested_begin_run_does_not_clear_a_fresh_request() -> None:
    """嵌套登记不能把外层刚收到的停止请求清掉。

    这是真实的嵌套：SSE worker 一进线程就登记（覆盖准备仓库那段），Agent 运行层
    再登记一次。如果内层也清标志，用户在准备阶段点的停止就会被悄悄抹掉。
    """

    begin_run("t-nested")
    request_cancel("t-nested")
    begin_run("t-nested")

    assert is_cancel_requested("t-nested") is True

    end_run("t-nested")
    # 内层退出时还欠着外层一次，不能就此把「在跑」抹掉。
    assert request_cancel("t-nested") is True

    end_run("t-nested")
    assert request_cancel("t-nested") is False


def test_a_new_round_starts_clean() -> None:
    """上一轮遗留的停止标志不能影响下一轮。"""

    begin_run("t-round")
    request_cancel("t-round")
    end_run("t-round")

    begin_run("t-round")
    assert is_cancel_requested("t-round") is False
    raise_if_cancelled("t-round")


def test_raise_if_cancelled_is_a_noop_when_not_requested() -> None:
    """没人要求停的时候，检查函数什么也不做。"""

    begin_run("t-quiet")
    raise_if_cancelled("t-quiet")


class _Store:
    """记录收尾调用的假 Store。"""

    def __init__(self) -> None:
        self.finished: list[tuple[str, str]] = []
        self.thread_status: list[tuple[str, str]] = []
        self.runs: list[dict[str, Any]] = []

    def finish_open_run_events(self, thread_id: str, *, status: str = "completed") -> None:
        self.finished.append((thread_id, status))

    def update_thread_status(self, thread_id: str, status: str) -> None:
        self.thread_status.append((thread_id, status))

    def record_run(self, **kwargs: Any) -> None:
        self.runs.append(kwargs)


@pytest.fixture
def cancel_store(monkeypatch: pytest.MonkeyPatch) -> _Store:
    """把 ``mark_run_cancelled`` 里延迟导入的两个依赖换成假的。

    Returns:
        记录收尾调用的假 Store。
    """

    import agent.graph.graph as graph_module
    import agent.runtime.events as events_module

    store = _Store()
    monkeypatch.setattr(graph_module, "get_store", lambda: store)
    monkeypatch.setattr(events_module, "record_event", lambda *a, **k: None)
    return store


def test_mark_run_cancelled_writes_thread_and_run(cancel_store: _Store) -> None:
    """thread 与 run 两处都要落终态。

    只写一处的话，刷新页面会从另一处读回 ``running``，界面上就留下一个永远在跑的会话。
    """

    mark_run_cancelled(thread_id="t-1", run_id="r-1")

    assert cancel_store.finished == [("t-1", "cancelled")]
    assert cancel_store.thread_status == [("t-1", "cancelled")]
    assert len(cancel_store.runs) == 1
    assert cancel_store.runs[0]["run_id"] == "r-1"
    assert cancel_store.runs[0]["status"] == "cancelled"
    assert cancel_store.runs[0]["finished"] is True


def test_mark_run_cancelled_without_run_id_skips_the_run_table(cancel_store: _Store) -> None:
    """拿不到 run_id 时不能凭空造一条运行记录。"""

    mark_run_cancelled(thread_id="t-2", run_id=None)

    assert cancel_store.runs == []
    assert cancel_store.thread_status == [("t-2", "cancelled")]


def test_mark_run_cancelled_survives_a_broken_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """收尾失败只记日志，不能让「停止」这个动作本身再抛一次错。

    抛出去的话，用户点了停止反而看到一个报错，比停不掉更糟。
    """

    import agent.graph.graph as graph_module

    def boom() -> Any:
        raise RuntimeError("Store 不可用")

    monkeypatch.setattr(graph_module, "get_store", boom)

    mark_run_cancelled(thread_id="t-3", run_id="r-3")


def _call_cancel(monkeypatch: pytest.MonkeyPatch, *, task: dict[str, Any] | None) -> Any:
    """以给定会话状态调用取消接口。

    Args:
        monkeypatch: pytest 的打桩夹具。
        task: ``get_task`` 的返回值；``None`` 表示会话不存在。

    Returns:
        接口的返回值。

    Raises:
        HTTPException: 接口按预期拒绝时抛出。
    """

    from api import dashboard

    monkeypatch.setattr(dashboard, "get_task", lambda thread_id: task)
    monkeypatch.setattr(
        dashboard, "thread_payload", lambda value: {"thread_id": "t-1", "status": "running"}
    )
    return dashboard.dashboard_cancel_thread("t-1")


def test_cancel_endpoint_rejects_an_unknown_thread(monkeypatch: pytest.MonkeyPatch) -> None:
    """会话不存在时按 404 处理。"""

    with pytest.raises(HTTPException) as excinfo:
        _call_cancel(monkeypatch, task=None)

    assert excinfo.value.status_code == 404


def test_cancel_endpoint_rejects_when_nothing_is_running(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有在跑的轮次时按 409 处理，而不是假装受理。"""

    with pytest.raises(HTTPException) as excinfo:
        _call_cancel(monkeypatch, task={"thread_id": "t-1"})

    assert excinfo.value.status_code == 409


def test_cancel_endpoint_accepts_a_running_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """有轮次在跑时受理，并回会话摘要给前端刷新按钮状态。"""

    begin_run("t-1")

    result = _call_cancel(monkeypatch, task={"thread_id": "t-1"})

    assert result["thread_id"] == "t-1"
    assert is_cancel_requested("t-1") is True


def test_cancel_endpoint_is_wired_to_a_real_path() -> None:
    """路由确实挂在 ``POST /threads/{thread_id}/cancel`` 上。

    前端 ``lib/agents/api.ts`` 写死了 ``/threads/{id}/cancel``，再由 ``request()``
    拼上前缀 ``/dashboard/api``。改了后端路径而没改前端，就是这次要修的那个 404，
    所以把拼完之后的完整路径钉住。
    """

    from api import dashboard

    paths = {
        (route.path, method)
        for route in dashboard.dashboard_router.routes
        for method in getattr(route, "methods", set())
    }

    assert ("/dashboard/api/threads/{thread_id}/cancel", "POST") in paths


def test_streaming_worker_registers_the_run_before_starting() -> None:
    """SSE worker 必须在真正跑之前登记运行态。

    准备仓库、连 checkpoint 这些步骤也可能很久，期间用户点「停止」不能收到
    「没有正在运行的任务」。这里只检查代码里的调用顺序，不真起线程。
    """

    import inspect

    from services import streaming_service

    source = inspect.getsource(streaming_service.build_streaming_response)

    assert source.index("begin_run(thread_id)") < source.index("run_agent_task(")
    assert "end_run(thread_id)" in source


def test_cancelled_status_is_not_treated_as_a_failure() -> None:
    """被停止的一轮在自动化里不能算失败。

    算失败的话，连续失败计数会把一次主动停止也算进去，任务会被莫名停掉。
    """

    from services.automation_runner import _outcome_from_status

    assert _outcome_from_status("cancelled") != "failed"


def test_plain_exception_is_still_a_failure() -> None:
    """确认上一条不是因为把失败判定整个改宽了。"""

    from services.automation_runner import _outcome_from_status

    assert _outcome_from_status("failed") == "failed"


def test_thread_status_mapping_keeps_cancelled_out_of_running() -> None:
    """``cancelled`` 映射到前端后不能还是 ``running``。

    还是 ``running`` 的话，前端 ``hasActiveRun`` 会一直为真，停止按钮不消失。
    """

    from utils.thread_utils import status_for_frontend

    assert status_for_frontend("cancelled") not in {"running", "interrupted"}
