"""通用工具函数包。

提供 HTTP 安全请求、运行时上下文提取、Diff 解析、ID 生成、时间戳转换、
意图识别、消息内容处理、SCM 平台路由、Gitee/GitHub/Codeup API 封装等基础能力。
"""

from utils.context_utils import (
    get_runtime_configurable,
    get_runtime_task_kind,
    get_runtime_thread_id,
    runtime_is_read_only_task,
)
from utils.gitee_utils import (
    GiteeRepo,
    get_gitee_token,
    mask_token,
    normalize_gitee_repo_url,
    parse_gitee_repo_url,
)
from utils.github_utils import (
    GitHubRepo,
    get_github_token,
    normalize_github_repo_url,
    parse_github_repo_url,
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
from utils.platform_utils import (
    PLATFORM_DISPLAY_NAMES,
    PLATFORM_TOOL_MAP,
    SUPPORTED_PLATFORMS,
    ScmRepo,
    UnsupportedPlatformError,
    detect_platform,
    get_platform_display_name,
    get_platform_tools,
    normalize_repo_url,
    parse_repo_url,
)
from utils.reviewer_utils import (
    DiffFile,
    DiffSummary,
    get_local_diff,
    get_local_diff_summary,
    parse_unified_diff,
    validate_finding_location,
)
from utils.redis_utils import (
    cleanup_stream,
    ensure_consumer_group,
    publish_event,
    read_events,
)
from utils.thread_utils import (
    pr_payload,
    repo_full_name,
    status_for_frontend,
    user_visible_stream_text,
)
from utils.time_utils import iso_to_millis

__all__ = [
    "DiffFile",
    "DiffSummary",
    "GiteeRepo",
    "GitHubRepo",
    "PLATFORM_DISPLAY_NAMES",
    "PLATFORM_TOOL_MAP",
    "SUPPORTED_PLATFORMS",
    "ScmRepo",
    "TaskKind",
    "UnsupportedPlatformError",
    "blocked_response",
    "classify_task_kind",
    "cleanup_stream",
    "detect_platform",
    "ensure_consumer_group",
    "extract_best_plan_text",
    "extract_final_assistant_text",
    "get_gitee_token",
    "get_github_token",
    "get_local_diff",
    "get_local_diff_summary",
    "get_platform_display_name",
    "get_platform_tools",
    "get_runtime_configurable",
    "get_runtime_task_kind",
    "get_runtime_thread_id",
    "is_pull_only_task",
    "is_read_only_task",
    "is_workspace_listing_task",
    "iso_to_millis",
    "mask_token",
    "message_content_to_text",
    "new_finding_id",
    "new_message_id",
    "new_run_id",
    "new_thread_id",
    "normalize_gitee_repo_url",
    "normalize_github_repo_url",
    "normalize_repo_url",
    "parse_gitee_repo_url",
    "parse_github_repo_url",
    "parse_repo_url",
    "parse_unified_diff",
    "pr_payload",
    "publish_event",
    "read_events",
    "repo_full_name",
    "request_with_safe_redirects",
    "runtime_is_read_only_task",
    "status_for_frontend",
    "user_visible_stream_text",
    "validate_finding_location",
]
