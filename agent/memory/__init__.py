"""长期记忆子包。

提供工作区级和仓库级两层记忆能力：

- ``workspace``: 从本地 ``workspace.md`` 读取工作区事实描述。
- ``repo``: 仓库级记忆的 namespace 管理、初始化与读取。
- ``repo_update``: 任务结束后从 Agent 输出中提取稳定事实，结构化写回仓库记忆。
"""

from __future__ import annotations

from agent.memory.repo import (
    build_initial_repo_memory,
    build_repo_memory_namespace,
    ensure_repo_memory_initialized,
    get_repo_memory_item,
    repo_memory_store_key,
    repo_memory_virtual_path,
    repo_project_dir,
)
from agent.memory.repo_update import (
    RepoMemoryUpdate,
    build_updated_repo_memory,
    update_repo_memory_from_text,
)
from agent.memory.workspace import load_workspace_memory

__all__ = [
    "RepoMemoryUpdate",
    "build_initial_repo_memory",
    "build_repo_memory_namespace",
    "build_updated_repo_memory",
    "ensure_repo_memory_initialized",
    "get_repo_memory_item",
    "load_workspace_memory",
    "repo_memory_store_key",
    "repo_memory_virtual_path",
    "repo_project_dir",
    "update_repo_memory_from_text",
]
