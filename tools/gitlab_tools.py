"""GitLab 相关 LangChain 工具。

本模块把 `utils/gitlab_utils.py` 中的低层 API 调用包装成模型可调用的工具。
这里负责处理运行期上下文、只读任务拦截、任务事件记录和业务 Store 状态更新。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool

from agent.graph.graph import get_store
from agent.runtime.events import record_event
from utils.gitlab_utils import (
    create_merge_request,
    get_merge_request,
    list_merge_request_comments,
    list_merge_request_commits,
    list_merge_request_files,
    post_mr_comment,
)
from tools.runtime_context import get_runtime_thread_id, runtime_is_read_only_task

logger = logging.getLogger("agent.run.gitlab")


@tool
def open_gitlab_merge_request(
    owner: str,
    repo: str,
    head: str,
    base: str = "main",
    title: str = "Xihe generated changes",
    body: str = "由 Xihe 自动生成。",
) -> dict[str, Any]:
    """为已经推送到 GitLab 的分支创建或复用 Merge Request。

    Args:
        owner: GitLab 仓库 owner。
        repo: GitLab 仓库名称。
        head: 已推送的源分支。
        base: 目标分支，默认 main。
        title: MR 标题。
        body: MR 描述。

    Returns:
        成功时返回 `ok=True`、MR URL 和原始 API 数据；只读任务或 API 失败时返回错误信息。
    """

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能创建 Merge Request。请先向用户输出方案或分析结论，等待确认实施。",
        }

    thread_id = get_runtime_thread_id()
    logger.info(
        "准备创建 GitLab MR：owner=%s repo=%s head=%s base=%s title=%s",
        owner,
        repo,
        head,
        base,
        title,
    )
    if thread_id:
        record_event(thread_id, "gitlab:mr", "创建或复用 Merge Request", kind="fetch", status="in_progress")

    mr = create_merge_request(owner=owner, repo=repo, head=head, base=base, title=title, body=body)
    mr_url = mr.get("web_url") or mr.get("url") or ""

    if thread_id:
        get_store().update_thread_status(thread_id, "pr_created", pr_url=mr_url, branch_name=head)
        record_event(
            thread_id,
            "gitlab:mr",
            "创建或复用 Merge Request",
            kind="fetch",
            status="completed",
            detail=mr_url,
        )

    if mr.get("reused"):
        logger.info("GitLab MR 已存在，复用已有 MR：thread_id=%s mr_url=%s", thread_id, mr_url)
    else:
        logger.info("GitLab MR 创建成功：thread_id=%s mr_url=%s", thread_id, mr_url)
    return {"ok": True, "mr_url": mr_url, "raw": mr}


@tool
def publish_gitlab_mr_comment(owner: str, repo: str, iid: int, body: str) -> dict[str, Any]:
    """向指定 GitLab Merge Request 发布普通评论。

    Args:
        owner: GitLab 仓库 owner。
        repo: GitLab 仓库名称。
        iid: MR 的项目内部 ID。
        body: 评论正文。

    Returns:
        GitLab API 返回的评论 JSON。
    """

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能发布 MR 评论。请先向用户输出方案或分析结论，等待确认实施。",
        }

    logger.info("准备发布 GitLab MR 评论：owner=%s repo=%s iid=%s", owner, repo, iid)
    return post_mr_comment(owner=owner, repo=repo, iid=iid, body=body)


@tool
def get_gitlab_merge_request_context(owner: str, repo: str, iid: int) -> dict[str, Any]:
    """读取 GitLab Merge Request 审查上下文。

    Reviewer 子 Agent 使用该工具获取 MR 标题、描述、变更文件、提交列表和已有评论。
    该工具只读，不会修改 GitLab 仓库。
    """

    logger.info("读取 GitLab MR 上下文：owner=%s repo=%s iid=%s", owner, repo, iid)
    mr = get_merge_request(owner=owner, repo=repo, iid=iid)
    commits = list_merge_request_commits(owner=owner, repo=repo, iid=iid)
    files = list_merge_request_files(owner=owner, repo=repo, iid=iid)
    comments = list_merge_request_comments(owner=owner, repo=repo, iid=iid)
    return {
        "merge_request": mr,
        "commits": commits,
        "files": files,
        "comments": comments,
        "summary": {
            "title": mr.get("title") if isinstance(mr, dict) else None,
            "state": mr.get("state") if isinstance(mr, dict) else None,
            "files_count": len(files),
            "commits_count": len(commits),
            "comments_count": len(comments),
        },
    }
