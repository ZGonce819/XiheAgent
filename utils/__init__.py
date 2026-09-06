"""通用工具函数包。

提供 HTTP 安全请求、运行时上下文提取、Diff 解析、ID 生成、时间戳转换、
意图识别和消息内容处理等基础能力。
"""

from utils.context_utils import (
    get_runtime_configurable,
    get_runtime_task_kind,
    get_runtime_thread_id,
    runtime_is_read_only_task,
)
from utils.http_utils import blocked_response, request_with_safe_redirects
from utils.id_utils import new_finding_id, new_message_id, new_run_id, new_thread_id
from utils.intent_utils import (
    TaskKind,
    classify_task_kind,
    is_pull_only_task,
    is_read_only_task,
    is_workspace_listing_task,
)
from utils.message_utils import (
    extract_best_plan_text,
    extract_final_assistant_text,
    message_content_to_text,
)
from utils.reviewer_utils import (
    DiffFile,
    DiffSummary,
    get_local_diff,
    get_local_diff_summary,
    parse_unified_diff,
    validate_finding_location,
)
from utils.time_utils import iso_to_millis

__all__ = [
    "DiffFile",
    "DiffSummary",
    "TaskKind",
    "blocked_response",
    "classify_task_kind",
    "extract_best_plan_text",
    "extract_final_assistant_text",
    "get_local_diff",
    "get_local_diff_summary",
    "get_runtime_configurable",
    "get_runtime_task_kind",
    "get_runtime_thread_id",
    "iso_to_millis",
    "is_pull_only_task",
    "is_read_only_task",
    "is_workspace_listing_task",
    "message_content_to_text",
    "new_finding_id",
    "new_message_id",
    "new_run_id",
    "new_thread_id",
    "parse_unified_diff",
    "request_with_safe_redirects",
    "runtime_is_read_only_task",
    "validate_finding_location",
]
