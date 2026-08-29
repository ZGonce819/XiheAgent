"""服务层统一导出。

把各子模块的公开接口汇总到 ``services`` 命名空间，外部只需::

    from services import normalize_repo_url, thread_payload

即可使用，无需关心具体子模块路径。
"""

from __future__ import annotations

from services.redis_stream import (
    cleanup_stream,
    ensure_consumer_group,
    publish_event,
    read_events,
)
from services.repo_service import DEFAULT_REPO_URL, normalize_repo_url
from services.streaming_service import build_streaming_response, sse_part
from services.thread_service import (
    message_payload,
    pr_payload,
    repo_full_name,
    status_for_frontend,
    thread_meta_payload,
    thread_payload,
    user_visible_stream_text,
)

__all__ = [
    # repo_service
    "DEFAULT_REPO_URL",
    "normalize_repo_url",
    # thread_service
    "message_payload",
    "pr_payload",
    "repo_full_name",
    "status_for_frontend",
    "thread_meta_payload",
    "thread_payload",
    "user_visible_stream_text",
    # streaming_service
    "build_streaming_response",
    "sse_part",
    # redis_stream
    "cleanup_stream",
    "ensure_consumer_group",
    "publish_event",
    "read_events",
]
