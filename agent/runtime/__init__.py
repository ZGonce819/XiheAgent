"""agent.runtime — 任务编排与运行时。

将原 LX_AICoding 的单体 ``agent/core/runtime.py`` 拆分为职责清晰的子模块：

- task_router: 主任务入口，负责创建、执行、查询和删除 Agent 任务。
- planning_router: 方案生成路由，输出技术方案并等待用户确认。
- sync_router: 同步操作路由，处理 git pull 和工作区列表等轻量任务。
- task_intent: 意图分类，根据用户输入判断任务类型（现再导出自 ``utils.intent_utils``）。
- streaming_runtime: 事件流消费，对接 DeepAgents V3 的原始事件协议。
- event_handlers: 事件处理器，安全包装任务执行入口。
- events: 运行步骤事件记录，供前端展示任务进度。
- repo_mapping: 仓库目录映射，管理仓库到本地工作区的路径关系。
- _helpers: 内部辅助函数，包含 checkpoint 读取、消息处理等共享逻辑。

再导出策略（重要）：
本包在初始化时若急切 import 全部子模块，会让任何一个子模块的依赖被放大成整包循环导入
（历史故障链：``utils.context_utils`` → ``agent.runtime`` 包初始化 → ``event_handlers``
→ ``task_router`` → ``agent.memory.repo_update`` → ``agent.memory.repo`` → ``utils``）。

因此改为**惰性再导出**：``from agent.runtime import X`` 与 ``agent.runtime.X`` 用法不变，
但子模块只在真正访问某个名字时才通过 :func:`__getattr__`（PEP 562）按需加载。
只 import 单个子模块（如 ``agent.runtime.task_intent``）时也不会再触发兄弟子模块的加载。
``TYPE_CHECKING`` 分支保留显式 import，仅供 IDE / mypy 静态解析，运行时不执行。
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agent.runtime.event_handlers import run_task_safely
    from agent.runtime.events import record_event
    from agent.runtime.planning_router import run_plan_response_task
    from agent.runtime.repo_mapping import (
        RepoMappingResult,
        discover_repo_mapping,
        save_clone_mapping,
    )
    from agent.runtime.streaming_runtime import run_agent_with_event_stream
    from agent.runtime.sync_router import run_pull_only_task, run_workspace_listing_task
    from agent.runtime.task_intent import (
        classify_task_kind,
        is_pull_only_task,
        is_read_only_task,
        is_workspace_listing_task,
    )
    from agent.runtime.task_router import (
        delete_task,
        get_task,
        initialize_task_record,
        list_tasks,
        resume_agent_task,
        run_agent_task,
    )

# 子模块 → 该子模块对外公开的符号。惰性加载时按此表反查名字归属哪个子模块。
_SUBMODULE_EXPORTS: dict[str, tuple[str, ...]] = {
    "agent.runtime.event_handlers": ("run_task_safely",),
    "agent.runtime.events": ("record_event",),
    "agent.runtime.planning_router": ("run_plan_response_task",),
    "agent.runtime.repo_mapping": (
        "RepoMappingResult",
        "discover_repo_mapping",
        "save_clone_mapping",
    ),
    "agent.runtime.streaming_runtime": ("run_agent_with_event_stream",),
    "agent.runtime.sync_router": ("run_pull_only_task", "run_workspace_listing_task"),
    "agent.runtime.task_intent": (
        "classify_task_kind",
        "is_pull_only_task",
        "is_read_only_task",
        "is_workspace_listing_task",
    ),
    "agent.runtime.task_router": (
        "delete_task",
        "get_task",
        "initialize_task_record",
        "list_tasks",
        "run_agent_task",
        "resume_agent_task",
    ),
}

# 名字 → 子模块 的反向索引，供 __getattr__ 惰性解析。
_NAME_TO_MODULE: dict[str, str] = {
    name: module for module, names in _SUBMODULE_EXPORTS.items() for name in names
}

__all__ = [
    "RepoMappingResult",
    "classify_task_kind",
    "delete_task",
    "discover_repo_mapping",
    "get_task",
    "initialize_task_record",
    "is_pull_only_task",
    "is_read_only_task",
    "is_workspace_listing_task",
    "list_tasks",
    "record_event",
    "run_agent_task",
    "resume_agent_task",
    "run_agent_with_event_stream",
    "run_plan_response_task",
    "run_pull_only_task",
    "run_task_safely",
    "run_workspace_listing_task",
    "save_clone_mapping",
]


def __getattr__(name: str) -> Any:
    """按名字惰性加载并缓存对应子模块的符号（PEP 562）。

    ``from agent.runtime import X`` 与 ``agent.runtime.X`` 都会触发本函数。
    首次访问某个名字时才 import 它所属的子模块，取出符号并写回 globals 缓存。

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
    """让 ``dir(agent.runtime)`` 与自动补全能看到全部惰性符号。"""

    return sorted(set(globals()) | set(_NAME_TO_MODULE))
