"""``run_agent_task`` 的路由判定看哪份文本。

背景：自动化任务的循环会在用户写的任务指令后面拼上循环状态与上一轮验证反馈，
那份拼好的文本才是发给模型的。如果**路由判定也看这份拼好的文本**，附言里出现
「提交」「push」这类词就会把只读任务判成开发实现，于是走进等用户确认的分支停住，
而循环还会把这一轮记成「没做完但没报错」—— 卡住且看不出来。

所以判定只看 ``routing_prompt``（不传时才退回 ``prompt``）；调用方已经判过类型
就传 ``task_kind``，省掉一次模型调用，也保证两边结论一致。

用例不打桩模型：``classify_task_kind`` 本身就是被打桩的那一侧。
"""

from __future__ import annotations

from typing import Any

import pytest

from agent.runtime.task_router import run_agent_task

REPO = "https://github.com/owner/repo.git"
ASSEMBLED = (
    "分析这个仓库的目录结构\n\n---\n这是这个自动任务的第 3 轮。\n"
    "上一轮的验证没有通过，反馈如下：\n需要修改代码后重新提交"
)
INSTRUCTION = "分析这个仓库的目录结构"


class FakeStore:
    """只够 :func:`run_agent_task` 走到分派那一步的最小替身。"""

    def __init__(self, thread: dict[str, Any] | None = None) -> None:
        self.thread = thread

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        return self.thread


@pytest.fixture
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """关掉事件记录与关键词快速分类，只留下要观测的那几个决定。"""
    monkeypatch.setattr("agent.runtime.task_router.record_event", lambda *a, **k: None)
    monkeypatch.setattr(
        "agent.runtime.task_router.is_workspace_listing_task", lambda text: False
    )
    monkeypatch.setattr("agent.runtime.task_router.is_pull_only_task", lambda text: False)
    monkeypatch.setattr("agent.runtime.task_router.get_store", lambda: FakeStore())


def test_without_a_given_kind_it_classifies_the_routing_text(
    monkeypatch: pytest.MonkeyPatch, quiet: None
) -> None:
    """没传 task_kind 时自己判，判的是 routing_prompt 而不是拼好的那份。"""
    classified: list[str] = []
    monkeypatch.setattr(
        "agent.runtime.task_router.classify_task_kind",
        lambda text: classified.append(text) or "coding",
    )
    monkeypatch.setattr(
        "agent.runtime.task_router.run_plan_response_task",
        lambda **kwargs: {"status": "planning"},
    )

    run_agent_task(
        repo_url=REPO,
        prompt=ASSEMBLED,
        routing_prompt=INSTRUCTION,
        thread_id="t1",
    )

    assert classified == [INSTRUCTION]


def test_a_given_kind_is_used_as_is(
    monkeypatch: pytest.MonkeyPatch, quiet: None
) -> None:
    """传了 task_kind 就不再判一次 —— 省一次模型调用，也保证两边结论一致。"""
    classified: list[str] = []

    def should_not_run(text: str) -> str:
        classified.append(text)
        return "coding"

    monkeypatch.setattr("agent.runtime.task_router.classify_task_kind", should_not_run)
    monkeypatch.setattr(
        "agent.runtime.task_router.run_plan_response_task",
        lambda **kwargs: {"status": "planning"},
    )

    result = run_agent_task(
        repo_url=REPO,
        prompt=ASSEMBLED,
        routing_prompt=INSTRUCTION,
        task_kind="coding",
        thread_id="t1",
    )

    assert classified == []
    assert result == {"status": "planning"}


def test_the_routing_text_does_not_change_what_the_model_gets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由看原始指令，但真正交给模型处理的仍然是拼好的那份。"""
    seen: dict[str, Any] = {}
    monkeypatch.setattr("agent.runtime.task_router.record_event", lambda *a, **k: None)
    monkeypatch.setattr(
        "agent.runtime.task_router.is_workspace_listing_task", lambda text: True
    )
    monkeypatch.setattr(
        "agent.runtime.task_router.run_workspace_listing_task",
        lambda **kwargs: seen.update(kwargs) or {"status": "listed"},
    )

    run_agent_task(
        repo_url=REPO,
        prompt=ASSEMBLED,
        routing_prompt=INSTRUCTION,
        thread_id="t1",
    )

    assert seen["prompt"] == ASSEMBLED


def test_without_a_routing_prompt_the_prompt_itself_decides(
    monkeypatch: pytest.MonkeyPatch, quiet: None
) -> None:
    """不传 routing_prompt 时行为与改动前一致：判定看 prompt。"""
    classified: list[str] = []
    monkeypatch.setattr(
        "agent.runtime.task_router.classify_task_kind",
        lambda text: classified.append(text) or "coding",
    )
    monkeypatch.setattr(
        "agent.runtime.task_router.run_plan_response_task",
        lambda **kwargs: {"status": "planning"},
    )

    run_agent_task(repo_url=REPO, prompt=ASSEMBLED, thread_id="t1")

    assert classified == [ASSEMBLED]
