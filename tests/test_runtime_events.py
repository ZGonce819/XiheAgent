"""运行事件状态收尾的回归测试。"""

from __future__ import annotations

from typing import Any

import pytest

from agent.runtime import events


class FakeEventStore:
    def __init__(self) -> None:
        self.updated: list[dict[str, Any]] = []

    def list_run_events(self, thread_id: str) -> list[dict[str, Any]]:
        return [
            {
                "id": f"{thread_id}:tool:command-1",
                "kind": "execute",
                "title": "终端(pytest)",
                "status": "in_progress",
                "detail": '{"tool":"execute","args":{"command":"pytest"}}',
            },
            {
                "id": f"{thread_id}:repo",
                "kind": "think",
                "title": "解析仓库",
                "status": "in_progress",
                "detail": None,
            },
        ]

    def add_run_event(self, **event: Any) -> None:
        self.updated.append(event)


def test_reject_marks_waiting_tool_event_and_keeps_process_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """拒绝审批只更新等待执行的工具，并使用仓储实际返回的 ``id`` 字段。"""
    store = FakeEventStore()
    monkeypatch.setattr(events, "_get_event_store", lambda: store)

    events.mark_tool_events_rejected("thread-1")

    assert len(store.updated) == 1
    assert store.updated[0]["event_id"] == "thread-1:tool:command-1"
    assert store.updated[0]["status"] == "rejected"
