from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from langgraph.checkpoint.redis.jsonplus_redis import JsonPlusRedisSerializer
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import MessagesState, StateGraph
from langgraph.types import Interrupt, interrupt

from agent.graph.redis_checkpointer import ReliableRedisSaver
from agent.runtime import streaming_runtime


def test_interrupt_survives_stale_checkpoint_flag() -> None:
    """An existing interrupt must load even when a later put cleared the hint."""
    saver = object.__new__(ReliableRedisSaver)
    saver.serde = JsonPlusRedisSerializer()
    pending = Interrupt(value={"action_requests": [{"name": "execute"}]}, id="approval-1")
    kind, blob = saver.serde.dumps_typed([pending])
    saver._key_registry = SimpleNamespace(
        get_write_count=lambda *args: 1,
        get_write_keys=lambda *args: ["interrupt-write"],
    )
    saver._redis = MagicMock()
    pipeline = saver._redis.pipeline.return_value.__enter__.return_value
    pipeline.execute.return_value = [{
        "task_id": "gate", "idx": -3, "channel": "__interrupt__",
        "type": kind, "blob": saver._encode_blob(blob),
    }]

    writes = saver._load_pending_writes_with_registry_check(
        "thread", "", "checkpoint", checkpoint_has_writes=False, registry_has_writes=False,
    )

    assert writes == [("gate", "__interrupt__", [pending])]
    pipeline.json.return_value.get.assert_called_once_with("interrupt-write")


def test_live_stream_reports_approval_and_can_resume_despite_stale_snapshot(monkeypatch) -> None:
    monkeypatch.setattr(streaming_runtime, "record_event", lambda *args, **kwargs: None)
    decisions = []
    approval = {"action_requests": [{"name": "execute", "args": {"command": "test-only"}}]}

    def gate(state):
        decision = interrupt(approval)
        decisions.append(decision)
        return {"messages": [{"role": "assistant", "content": "已收到决定，继续回复。"}]}

    builder = StateGraph(MessagesState)
    builder.add_node("gate", gate)
    builder.set_entry_point("gate")
    builder.set_finish_point("gate")
    graph = builder.compile(checkpointer=InMemorySaver())
    # Mirrors a Redis checkpoint whose pending writes are omitted. Reading it
    # must not turn the just-interrupted stream into a completed run.
    agent = SimpleNamespace(
        stream_events=graph.stream_events,
        get_state=lambda config: SimpleNamespace(values={"messages": []}, interrupts=()),
    )
    events = []
    kwargs = dict(agent=agent, thread_id="approval-thread", run_id="run", event_sink=lambda *event: events.append(event))

    paused = streaming_runtime.run_agent_with_event_stream(content="测试", **kwargs)

    assert paused["interrupted"] is True
    assert paused["interrupt_id"]
    assert paused["approval"] == approval
    assert decisions == []
    assert not any(event == "text_delta" for event, _ in events)

    resumed = streaming_runtime.run_agent_with_event_stream(
        content="", resume_decisions=[{"type": "reject"}], **kwargs,
    )

    assert not resumed.get("interrupted")
    assert decisions == [{"decisions": [{"type": "reject"}]}]
    assert resumed["messages"][-1]["content"] == "已收到决定，继续回复。"
    assert any(event == "text_delta" and data["content"] == "已收到决定，继续回复。" for event, data in events)
