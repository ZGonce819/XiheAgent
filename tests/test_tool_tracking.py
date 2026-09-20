"""Tool execution persistence regression tests."""

from __future__ import annotations

import json
from typing import Any

import pytest
from langchain_core.messages import ToolMessage

from agent.middleware import tool_tracking


def test_nonzero_command_exit_is_persisted_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(
        tool_tracking,
        "record_event",
        lambda thread_id, key, title, **kwargs: recorded.append(
            {"thread_id": thread_id, "key": key, "title": title, **kwargs}
        ),
    )
    middleware = tool_tracking.ToolTrackingMiddleware(backend=object())  # type: ignore[arg-type]
    result = ToolMessage(
        content="fatal: not a git repository\n[Command failed with exit code 128]",
        tool_call_id="command-1",
        artifact={"exit_code": 128},
        status="success",
    )

    middleware._record_tool_end(
        "thread-1",
        key="tool:command-1",
        tool_name="execute",
        args={"command": "git status"},
        elapsed_ms=25,
        success=tool_tracking._tool_result_succeeded(result, "execute"),
        output=tool_tracking._tool_result_output(result),
    )

    assert recorded[0]["status"] == "error"
    detail = json.loads(recorded[0]["detail"])
    assert detail["args"] == {"command": "git status"}
    assert "fatal: not a git repository" in detail["output"]


def test_error_tool_message_is_not_marked_completed() -> None:
    result = ToolMessage(
        content="文件不存在",
        tool_call_id="read-1",
        status="error",
    )

    assert tool_tracking._tool_result_succeeded(result, "read_file") is False


def test_sync_wrapper_records_nonzero_command_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []
    monkeypatch.setattr(tool_tracking, "_get_thread_id", lambda: "thread-1")
    monkeypatch.setattr(
        tool_tracking,
        "record_event",
        lambda thread_id, key, title, **kwargs: recorded.append(
            {"thread_id": thread_id, "key": key, "title": title, **kwargs}
        ),
    )
    middleware = tool_tracking.ToolTrackingMiddleware(backend=object())  # type: ignore[arg-type]
    request = type("Request", (), {
        "tool_call": {
            "id": "command-1",
            "name": "execute",
            "args": {"command": "git status"},
        },
    })()

    returned = middleware.wrap_tool_call(
        request,  # type: ignore[arg-type]
        lambda _request: ToolMessage(
            content="fatal: not a git repository\n[Command failed with exit code 128]",
            tool_call_id="command-1",
            artifact={"exit_code": 128},
            status="success",
        ),
    )

    assert isinstance(returned, ToolMessage)
    assert [event["status"] for event in recorded] == ["in_progress", "error"]
    assert "fatal: not a git repository" in json.loads(recorded[-1]["detail"])["output"]
