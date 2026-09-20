from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from agent.middleware.final_response import FinalResponseMiddleware
from agent.runtime import streaming_runtime as streaming


def test_finalizes_tool_ending_without_tools():
    seen = []
    model = SimpleNamespace(invoke=lambda messages: seen.append(messages) or AIMessage(content="命令失败，未完成"))
    middleware = FinalResponseMiddleware(model)
    result = middleware.after_agent({"messages": [HumanMessage(content="执行"), ToolMessage(content="failed", tool_call_id="a")]}, None)
    assert isinstance(result["messages"][-1], AIMessage)
    assert len(seen) == 1
    assert middleware.after_agent({"messages": [AIMessage(content="完成")]}, None) is None
    pending = AIMessage(content="", tool_calls=[{"name": "execute", "args": {"command": "test"}, "id": "a"}])
    assert middleware.after_agent({"messages": [pending]}, None) is None


def test_approval_records_real_call_id_before_execution(monkeypatch):
    recorded = []
    monkeypatch.setattr(streaming, "record_event", lambda *args, **kwargs: recorded.append((args, kwargs)))
    call = {"name": "execute", "args": {"command": "test"}, "id": "cmd-1"}
    output = {"messages": [AIMessage(content="", tool_calls=[call])]}
    streaming._record_pending_commands("thread", output, {"action_requests": [call]})
    assert recorded[0][0][1] == "tool:cmd-1"
    assert recorded[0][1]["status"] == "pending"


def test_resume_text_ids_are_unique(monkeypatch):
    recorded = []
    monkeypatch.setattr(streaming, "record_event", lambda *args, **kwargs: recorded.append(args[1]))
    monkeypatch.setattr(streaming, "_text_block_marker_from_event", lambda _: None)
    monkeypatch.setattr(streaming, "_text_delta_from_event", lambda _: "正文")
    for _ in range(2):
        streaming._consume_raw_event_stream(stream=[{}], thread_id="thread", run_id="same-run")
    assert len(recorded) == 2
    assert len(set(recorded)) == 2
