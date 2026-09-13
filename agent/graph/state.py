"""Xihe Agent 状态定义模块。

定义 LangGraph 图执行过程中流转的状态结构 ``AgentTaskState``，
包括消息列表、任务类型、仓库标识等核心字段。
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentTaskState(TypedDict, total=False):
    """LangGraph 图执行过程中流转的状态结构。

    定义消息列表、任务类型、仓库标识等核心字段，
    供各节点在图执行期间读写。使用 ``total=False`` 声明所有字段均为可选，
    节点可按需只写入自己负责的字段。

    Attributes:
        thread_id: 当前任务所属线程 ID，用于关联消息、事件和运行记录。
        repo_url: 代码仓库完整 URL。
        repo_owner: 仓库 owner（命名空间或组织名）。
        repo_name: 仓库名称。
        branch_name: 当前操作的目标分支。
        pr_url: 已创建的 Pull Request / Merge Request 地址。
        latest_run_status: 最近一次运行的状态，例如 running、completed、error。
        messages: LangChain 消息列表，承载对话和工具调用的完整上下文。
    """
    thread_id: str
    repo_url: str
    repo_owner: str
    repo_name: str
    branch_name: str
    pr_url: str
    latest_run_status: str
    messages: list[dict[str, Any]]
