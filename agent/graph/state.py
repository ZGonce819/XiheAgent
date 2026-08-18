from __future__ import annotations

from typing import Any, TypedDict

"""Xihe Agent 状态定义模块。

定义 LangGraph 图执行过程中流转的状态结构 ``AgentTaskState``，
包括消息列表、任务类型、仓库标识等核心字段。
"""


class AgentTaskState(TypedDict, total=False):
    """LangGraph 图执行过程中流转的状态结构。

    定义消息列表、任务类型、仓库标识等核心字段，
    供各节点在图执行期间读写。
    """

    thread_id: str
    repo_url: str
    repo_owner: str
    repo_name: str
    branch_name: str
    pr_url: str
    latest_run_status: str
    messages: list[dict[str, Any]]
