from __future__ import annotations

import json
from typing import Any

from agent.runtime import events
from agent.runtime.streaming_runtime import _text_delta_from_event
from agent.runtime.streaming_runtime import _message_dict
from services.thread_service import _messages_from_run_events
from agent.graph.checkpoint_history import visible_checkpoint_messages


class _EventStore:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def add_run_event(self, **event: Any) -> None:
        self.events.append(event)


def test_record_event_routes_callbacks_by_thread(monkeypatch) -> None:
    store = _EventStore()
    received_a: list[tuple[Any, ...]] = []
    received_b: list[tuple[Any, ...]] = []
    monkeypatch.setattr(events, "_get_event_store", lambda: store)

    events.register_sse_callback("thread-a", lambda *args: received_a.append(args))
    events.register_sse_callback("thread-b", lambda *args: received_b.append(args))
    try:
        events.record_event(
            "thread-a",
            "tool:call-1",
            "读取文件(test.py)",
            kind="read",
            detail='{"tool":"read_file"}',
        )
    finally:
        events.clear_sse_callback("thread-a")
        events.clear_sse_callback("thread-b")

    assert len(store.events) == 1
    assert received_a == [
        (
            "thread-a",
            "tool:call-1",
            "read",
            "读取文件(test.py)",
            "in_progress",
            '{"tool":"read_file"}',
        )
    ]
    assert received_b == []


def test_history_uses_latest_todo_update_for_run() -> None:
    run_events = [
        {
            "id": "thread-1:todos:run-1:call-1",
            "kind": "todo",
            "detail": json.dumps(
                {"todos": [{"content": "检查代码", "status": "in_progress"}]},
                ensure_ascii=False,
            ),
            "created_at": "2026-09-11T10:00:00Z",
        },
        {
            "id": "thread-1:todos:run-1:call-2",
            "kind": "todo",
            "detail": json.dumps(
                {"todos": [{"content": "检查代码", "status": "completed"}]},
                ensure_ascii=False,
            ),
            "created_at": "2026-09-11T10:01:00Z",
        },
    ]

    messages = _messages_from_run_events("thread-1", run_events)

    assert len(messages) == 1
    assert messages[0]["chunks"][1]["todos"][0]["status"] == "completed"


def test_text_delta_supports_nested_block_delta() -> None:
    event = {
        "method": "messages",
        "params": {
            "data": {
                "event": "content-block-delta",
                "delta": {
                    "type": "block-delta",
                    "fields": {"type": "text_delta", "text": "继续输出正文"},
                },
            }
        },
    }

    assert _text_delta_from_event(event) == "继续输出正文"


def test_checkpoint_history_keeps_serialized_short_assistant_reply() -> None:
    messages = visible_checkpoint_messages({
        "channel_values": {
            "messages": [
                {"type": "human", "content": "你好"},
                {"type": "ai", "content": "你好！"},
            ]
        }
    })
    assert [item["content"] for item in messages] == ["你好", "你好！"]


def test_message_dict_preserves_serialized_model_message() -> None:
    assert _message_dict({"type": "ai", "content": "你好！"}) == {
        "type": "ai", "role": None, "content": "你好！"
    }
