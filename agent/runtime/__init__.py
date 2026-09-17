"""agent.runtime — 任务编排与运行时。

将原 LX_AICoding 的单体 ``agent/core/runtime.py`` 拆分为职责清晰的子模块：

- task_router: 主任务入口，负责创建、执行、查询和删除 Agent 任务。
- planning_router: 方案生成路由，输出技术方案并等待用户确认。
- sync_router: 同步操作路由，处理 git pull 和工作区列表等轻量任务。
- task_intent: 意图分类，根据用户输入判断任务类型。
- streaming_runtime: 事件流消费，对接 DeepAgents V3 的原始事件协议。
- event_handlers: 事件处理器，安全包装任务执行入口。
- events: 运行步骤事件记录，供前端展示任务进度。
- repo_mapping: 仓库目录映射，管理仓库到本地工作区的路径关系。
- _helpers: 内部辅助函数，包含 checkpoint 读取、消息处理等共享逻辑。
"""

from agent.runtime.event_handlers import run_task_safely
from agent.runtime.events import record_event
from agent.runtime.planning_router import run_plan_response_task
from agent.runtime.repo_mapping import RepoMappingResult, discover_repo_mapping, save_clone_mapping
from agent.runtime.streaming_runtime import run_agent_with_event_stream
from agent.runtime.sync_router import run_pull_only_task, run_workspace_listing_task
from agent.runtime.task_intent import classify_task_kind, is_pull_only_task, is_read_only_task, is_workspace_listing_task
from agent.runtime.task_router import (
    delete_task,
    get_task,
    initialize_task_record,
    list_tasks,
    run_agent_task,
    resume_agent_task,
)

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
