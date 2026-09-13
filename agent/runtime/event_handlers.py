"""后台任务入口。

FastAPI 的 BackgroundTasks 在响应返回后调用这里，由它分发到真正的 runtime 函数。
异常必须在这里吞掉，因为真正的失败状态已经由 runtime 写入 Store；
如果异常继续冒泡，只会污染 Uvicorn 后台任务日志，前端也拿不到更有用的信息。
"""

from __future__ import annotations

import logging

from agent.runtime.task_intent import is_pull_only_task, is_workspace_listing_task
from agent.runtime.task_router import run_agent_task, run_pull_only_task, run_workspace_listing_task

logger = logging.getLogger(__name__)


def run_task_safely(*, repo_url: str, prompt: str, thread_id: str) -> None:
    """后台执行 dashboard 触发的任务。

    根据用户意图分发到对应的 runtime 分支：
    - 工作区查询：直接列出本地项目，不调用模型。
    - git pull：只拉取远程代码，不修改、不提交。
    - 其它任务：走完整的 Agent 执行流程。

    异常在这里统一捕获并记录日志，不向上冒泡，因为真正的失败状态
    已经由 runtime 写入 Store。

    Args:
        repo_url: 仓库完整地址。
        prompt: 用户输入的任務描述。
        thread_id: 当前线程 ID。
    """

    try:
        if is_workspace_listing_task(prompt):
            run_workspace_listing_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
        elif is_pull_only_task(prompt):
            run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
        else:
            run_agent_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)
    except Exception:
        logger.exception("后台任务执行失败：thread_id=%s", thread_id)
