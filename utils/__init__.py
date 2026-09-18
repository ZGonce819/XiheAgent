"""通用工具函数包。

提供 HTTP 安全请求、运行时上下文提取、Diff 解析、ID 生成、时间戳转换、
意图识别、消息内容处理、SCM 平台路由、Gitee/GitHub/Codeup API 封装等基础能力。

再导出策略（重要）：
本包作为基础层，被 ``agent`` / ``services`` / ``api`` 等上层包广泛依赖。若在包初始化时
急切 import 所有子模块，任一子模块的上层依赖都会被放大成整包循环导入
（历史故障：``utils.context_utils`` → ``agent.runtime`` → ``agent.memory`` → ``utils``）。

因此这里改为**惰性再导出**：``from utils import X`` 与 ``utils.X`` 的公开用法完全不变，
但子模块只在真正访问某个名字时才通过 :func:`__getattr__`（PEP 562）按需加载。
``TYPE_CHECKING`` 分支保留显式 import，仅供 IDE / mypy 做静态解析，运行时不执行。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
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
    from utils.redis_utils import (
        cleanup_stream,
        ensure_consumer_group,
        publish_event,
        read_events,
    )
    from utils.reviewer_utils import (
        DiffFile,
        DiffSummary,
        get_local_diff,
        get_local_diff_summary,
        parse_unified_diff,
        validate_finding_location,
    )
    from utils.thread_utils import (
        pr_payload,
        repo_full_name,
        status_for_frontend,
        user_visible_stream_text,
    )
    from utils.time_utils import iso_to_millis

# 子模块 → 该子模块对外公开的符号。惰性加载时按此表反查名字归属哪个子模块。
_SUBMODULE_EXPORTS: dict[str, tuple[str, ...]] = {
    "utils.context_utils": (
        "get_runtime_configurable",
        "get_runtime_task_kind",
        "get_runtime_thread_id",
        "runtime_is_read_only_task",
    ),
    "utils.gitee_utils": (
        "GiteeRepo",
        "get_gitee_token",
        "mask_token",
        "normalize_gitee_repo_url",
        "parse_gitee_repo_url",
    ),
    "utils.github_utils": (
        "GitHubRepo",
        "get_github_token",
        "normalize_github_repo_url",
        "parse_github_repo_url",
    ),
    "utils.http_utils": ("blocked_response", "request_with_safe_redirects"),
    "utils.id_utils": ("new_finding_id", "new_message_id", "new_run_id", "new_thread_id"),
    "utils.intent_utils": (
        "TaskKind",
        "classify_task_kind",
        "is_pull_only_task",
        "is_read_only_task",
        "is_workspace_listing_task",
    ),
    "utils.message_utils": (
        "extract_best_plan_text",
        "extract_final_assistant_text",
        "message_content_to_text",
    ),
    "utils.platform_utils": (
        "PLATFORM_DISPLAY_NAMES",
        "PLATFORM_TOOL_MAP",
        "SUPPORTED_PLATFORMS",
        "ScmRepo",
        "UnsupportedPlatformError",
        "detect_platform",
        "get_platform_display_name",
        "get_platform_tools",
        "normalize_repo_url",
        "parse_repo_url",
    ),
    "utils.reviewer_utils": (
        "DiffFile",
        "DiffSummary",
        "get_local_diff",
        "get_local_diff_summary",
        "parse_unified_diff",
        "validate_finding_location",
    ),
    "utils.redis_utils": (
        "cleanup_stream",
        "ensure_consumer_group",
        "publish_event",
        "read_events",
    ),
    "utils.thread_utils": (
        "pr_payload",
        "repo_full_name",
        "status_for_frontend",
        "user_visible_stream_text",
    ),
    "utils.time_utils": ("iso_to_millis",),
}

# 名字 → 子模块 的反向索引，供 __getattr__ 惰性解析。
_NAME_TO_MODULE: dict[str, str] = {
    name: module for module, names in _SUBMODULE_EXPORTS.items() for name in names
}

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


def __getattr__(name: str) -> Any:
    """按名字惰性加载并缓存对应子模块的符号（PEP 562）。

    ``from utils import X`` 与 ``utils.X`` 都会触发本函数。首次访问某个名字时，
    才 import 它所属的子模块，取出该符号并写回本模块 globals 作为缓存，
    后续访问不再经过这里。

    Args:
        name: 被访问的属性名。

    Returns:
        对应子模块中名为 ``name`` 的对象。

    Raises:
        AttributeError: ``name`` 不在再导出表中，或子模块确实没有该符号。
    """

    module_name = _NAME_TO_MODULE.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(module_name), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """让 ``dir(utils)`` 与自动补全能看到全部惰性符号。"""

    return sorted(set(globals()) | set(_NAME_TO_MODULE))
