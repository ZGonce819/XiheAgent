"""服务层统一导出。

把各子模块的公开接口汇总到 ``services`` 命名空间，外部只需::

    from services import thread_payload, build_streaming_response

即可使用，无需关心具体子模块路径。
"""

from __future__ import annotations

from services.streaming_service import build_streaming_response, sse_part
from services.thread_service import (
    message_payload,
    thread_meta_payload,
    thread_payload,
)
from utils.thread_utils import (
    pr_payload,
    repo_full_name,
    status_for_frontend,
    user_visible_stream_text,
)

__all__ = [
    # thread_service（涉及 I/O）
    "message_payload",
    "thread_meta_payload",
    "thread_payload",
    # thread_utils（纯数据转换）
    "pr_payload",
    "repo_full_name",
    "status_for_frontend",
    "user_visible_stream_text",
    # streaming_service
    "build_streaming_response",
    "sse_part",
]
