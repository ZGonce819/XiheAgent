"""用两个真实连接验证事件轮询；只创建并清理本测试自己的事件。"""

import os
import json
import uuid
from urllib.request import urlopen

import pytest

from config.env_config import load_environment
from store.event_repo import RunEventRepo


@pytest.mark.skipif(os.getenv("XIHE_TEST_MYSQL") != "1", reason="需要本机 MySQL")
@pytest.mark.parametrize("read_many", [False, True])
def test_poll_sees_events_committed_by_another_connection(read_many):
    load_environment()
    writer = RunEventRepo()
    reader = RunEventRepo()
    thread_id = f"visibility-test-{uuid.uuid4()}"
    event_id = f"{thread_id}:agent"

    def read():
        if read_many:
            return reader.list_run_events(thread_id)[0]
        return reader.fetch_one("SELECT * FROM run_events WHERE id = %s", (event_id,))

    try:
        writer.add_run_event(event_id=event_id, thread_id=thread_id, kind="think",
                             title="构建 Agent 运行图", status="in_progress")
        assert read()["status"] == "in_progress"
        writer.add_run_event(event_id=event_id, thread_id=thread_id, kind="think",
                             title="构建 Agent 运行图", status="completed")
        assert read()["status"] == "completed"
    finally:
        reader.close()
        writer.clear_run_events(thread_id)
        writer.close()


@pytest.mark.skipif(os.getenv("XIHE_TEST_API") != "1", reason="需要本机 Dashboard 服务")
def test_live_detail_endpoint_refreshes_committed_events():
    """真实 HTTP 轮询必须看见独立写入连接提交的新事件。"""
    from store import LocalMySQLStore

    load_environment()
    writer = LocalMySQLStore()
    thread_id = f"visibility-test-{uuid.uuid4()}"
    event_id = f"{thread_id}:agent"

    def read():
        with urlopen(f"http://127.0.0.1:2024/dashboard/api/threads/{thread_id}", timeout=15) as response:
            return json.load(response)

    try:
        writer.upsert_thread(thread_id=thread_id, title="事件可见性回归测试",
                             latest_run_status="running", source="schedule", backend_type="local")
        writer.add_run_event(event_id=event_id, thread_id=thread_id, kind="think",
                             title="构建 Agent 运行图", status="in_progress")
        assert read()["runEvents"][0]["status"] == "in_progress"
        writer.add_run_event(event_id=event_id, thread_id=thread_id, kind="think",
                             title="构建 Agent 运行图", status="completed")
        writer.add_run_event(event_id=f"{thread_id}:model", thread_id=thread_id, kind="think",
                             title="调用大模型", status="in_progress")
        payload = read()
        assert payload["runEvents"][0]["status"] == "completed"
        assert payload["runEvents"][-1]["title"] == "调用大模型"
        writer.update_thread_status(thread_id, "completed")
        assert read()["status"] == "finished"
    finally:
        writer.delete_thread(thread_id)
        writer.close()
