"""方案原始需求跨轮累积的回归测试。

背景：方案正文是模型直接产出的，身上没有 metadata。于是"用户最早提出的完整需求"
在链路上断了三处 —— checkpoint 提取只留 ``id``/``role``/``content``、
``_get_visible_messages`` 再丢一次、``latest_confirmable_plan_message`` 自己合成的
metadata 里只有 ``task_kind``/``awaiting_confirmation``/``source``。

结果是 ``plan_source_prompt`` 永远走回退分支，回退到"最近一条非确认用户消息"。
第一轮修订时那条恰好是原始需求，看起来正常；从第二轮开始，回退到的是上一轮的
"再改一下，加上 CSV"，最早的需求就被覆盖掉了。

修法：原始需求写在 threads 表的 ``plan_source_prompt`` 列，读取时补回 metadata。
写入端在 :func:`agent.runtime.planning_router.run_plan_response_task`，
读取端在 :func:`agent.runtime._helpers.latest_confirmable_plan_message`。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from agent.runtime._helpers import latest_confirmable_plan_message, plan_source_prompt
from agent.runtime.planning_router import run_plan_response_task

REPO = "https://github.com/owner/repo.git"
ORIGINAL = "帮我加个导出功能"
REVISION = "再改一下，加上 CSV"

PLAN_TEXT = (
    "## 需求理解\n\n给列表页加导出。\n\n"
    "## 涉及模块和需要阅读的文件\n\n- frontend/src/routes/agents.tsx\n\n"
    "## 具体实施步骤\n\n1. 加按钮\n2. 接后端\n\n"
    "## 验证方案\n\n手动点一遍。\n\n"
    "## 风险点\n\n导出字段口径要确认。\n\n"
    "是否确认实施该方案？"
)


class _ThreadStore:
    """只回答 ``get_thread`` 的最小替身。"""

    def __init__(self, thread: dict[str, Any] | None) -> None:
        self.thread = thread

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        return self.thread


class _RecordingStore:
    """记录 :func:`run_plan_response_task` 往 thread 上写了什么。"""

    def __init__(self) -> None:
        self.thread_kwargs: dict[str, Any] = {}

    def clear_run_events(self, thread_id: str) -> None:
        pass

    def upsert_thread(self, **kwargs: Any) -> None:
        self.thread_kwargs = kwargs

    def record_run(self, **kwargs: Any) -> None:
        pass

    def update_thread_status(self, thread_id: str, status: str) -> None:
        pass

    def finish_open_run_events(self, thread_id: str, *, status: str) -> None:
        pass


class _Recorder:
    """把「写进 thread 的需求」和「发给模型的内容」一起收上来。"""

    def __init__(self) -> None:
        self.store = _RecordingStore()
        self.contents: list[str] = []


@pytest.fixture
def recorder(monkeypatch: pytest.MonkeyPatch) -> _Recorder:
    """替掉仓库准备与模型调用，只留下写 thread 和拼内容这两步。"""
    rec = _Recorder()
    monkeypatch.setattr("agent.runtime.planning_router.get_store", lambda: rec.store)
    monkeypatch.setattr("agent.runtime.planning_router.record_event", lambda *a, **k: None)
    monkeypatch.setattr(
        "agent.runtime.planning_router.parse_repo_url",
        lambda url: SimpleNamespace(clone_url=url, owner="owner", repo="repo"),
    )
    monkeypatch.setattr(
        "agent.runtime.task_router._ensure_repository", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "agent.runtime.planning_router.build_agent_for_runtime", lambda **kwargs: object()
    )

    def fake_stream(**kwargs: Any) -> dict[str, Any]:
        rec.contents.append(kwargs["content"])
        return {"messages": [{"content": PLAN_TEXT}], "interrupted": False}

    monkeypatch.setattr(
        "agent.runtime.planning_router.run_agent_with_event_stream", fake_stream
    )
    monkeypatch.setattr(
        "agent.runtime.planning_router.extract_best_plan_text", lambda messages: PLAN_TEXT
    )
    return rec


def test_the_first_plan_records_the_users_own_words(recorder: _Recorder) -> None:
    """首轮方案没有上一版可沿用，原始需求就是用户这一句。"""
    run_plan_response_task(
        repo_url=REPO, prompt=ORIGINAL, thread_id="t1", backend_type="local"
    )

    assert recorder.store.thread_kwargs["plan_source_prompt"] == ORIGINAL


def test_a_revision_keeps_the_original_requirement(recorder: _Recorder) -> None:
    """修订时沿用的是最早那版需求，不会退化成"再改一下"。"""
    previous_plan = {
        "content": PLAN_TEXT,
        "metadata": {
            "task_kind": "planning",
            "awaiting_confirmation": True,
            "source": "checkpoint",
            "source_prompt": ORIGINAL,
        },
    }

    run_plan_response_task(
        repo_url=REPO,
        prompt=ORIGINAL,
        thread_id="t1",
        previous_plan_message=previous_plan,
        revision_prompt=REVISION,
        backend_type="local",
    )

    assert recorder.store.thread_kwargs["plan_source_prompt"] == ORIGINAL
    # 拼给模型的内容里，"原始用户需求"这一段必须是完整需求而不是本轮附言。
    assert f"原始用户需求：\n{ORIGINAL}" in recorder.contents[0]
    assert f"用户新的修改要求：\n{REVISION}" in recorder.contents[0]


def test_the_read_path_fills_in_the_missing_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """checkpoint 里的方案没有 source_prompt，读的时候从 threads 表补上。"""
    monkeypatch.setattr(
        "agent.runtime._helpers.latest_confirmable_plan_from_checkpoint",
        lambda thread_id: {
            "message_id": "m1",
            "content": PLAN_TEXT,
            "metadata": {
                "task_kind": "planning",
                "awaiting_confirmation": True,
                "source": "checkpoint",
            },
        },
    )
    monkeypatch.setattr(
        "agent.runtime._helpers.get_store",
        lambda: _ThreadStore({"plan_source_prompt": ORIGINAL}),
    )

    message = latest_confirmable_plan_message("t1")

    assert plan_source_prompt(message, REVISION) == ORIGINAL


def test_without_a_recorded_source_it_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """老会话没有这一列，读不到就按调用方给的回退文本走。"""
    monkeypatch.setattr(
        "agent.runtime._helpers.latest_confirmable_plan_from_checkpoint",
        lambda thread_id: {
            "message_id": "m1",
            "content": PLAN_TEXT,
            "metadata": {"task_kind": "planning", "source": "checkpoint"},
        },
    )
    monkeypatch.setattr(
        "agent.runtime._helpers.get_store", lambda: _ThreadStore({"plan_source_prompt": None})
    )

    message = latest_confirmable_plan_message("t1")

    assert plan_source_prompt(message, REVISION) == REVISION


def test_reading_the_source_survives_a_store_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读 thread 失败不该把方案确认整条链路带崩。"""

    class _BrokenStore:
        def get_thread(self, thread_id: str) -> dict[str, Any] | None:
            raise RuntimeError("数据库不可用")

    monkeypatch.setattr(
        "agent.runtime._helpers.latest_confirmable_plan_from_checkpoint",
        lambda thread_id: {
            "message_id": "m1",
            "content": PLAN_TEXT,
            "metadata": {"task_kind": "planning", "source": "checkpoint"},
        },
    )
    monkeypatch.setattr("agent.runtime._helpers.get_store", lambda: _BrokenStore())

    message = latest_confirmable_plan_message("t1")

    assert message is not None
    assert plan_source_prompt(message, REVISION) == REVISION


def test_no_plan_message_means_no_source_to_read() -> None:
    """没有待确认方案时返回 ``None``，调用方照旧回退。"""
    assert plan_source_prompt(None, REVISION) == REVISION
