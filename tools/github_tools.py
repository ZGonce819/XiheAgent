"""GitHub 相关 LangChain 工具。

本模块把 `utils/github_utils.py` 中的低层 API 调用包装成模型可调用的工具。
这里负责处理运行期上下文、只读任务拦截、任务事件记录和业务 Store 状态更新。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.graph.graph import get_store
from agent.runtime.events import record_event
from utils.context_utils import get_runtime_thread_id, runtime_is_read_only_task
from utils.github_utils import (
    create_pull_request,
    get_pull_request,
    list_pull_request_comments,
    list_pull_request_commits,
    list_pull_request_files,
    post_pr_comment,
)

logger = logging.getLogger("agent.run.github")


class OpenGithubPullRequestSchema(BaseModel):
    owner: str = Field(description="GitHub 仓库 owner。")
    repo: str = Field(description="GitHub 仓库名称。")
    head: str = Field(description="已推送的源分支。")
    base: str = Field(default="main", description="目标分支，默认 main。")
    title: str = Field(default="XiheAgent generated changes", description="PR 标题。")
    body: str = Field(default="由 XiheAgent 自动生成。", description="PR 描述。")


@tool(args_schema=OpenGithubPullRequestSchema)
def open_github_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str = "main",
    title: str = "XiheAgent generated changes",
    body: str = "由 XiheAgent 自动生成。",
) -> dict[str, Any]:
    """为已经推送到 GitHub 的分支创建或复用 Pull Request。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        head: 已推送的源分支。
        base: 目标分支，默认 main。
        title: PR 标题。
        body: PR 描述。

    Returns:
        成功时返回 `ok=True`、PR URL 和原始 API 数据；只读任务或 API 失败时返回错误信息。
    """

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能创建 Pull Request。请先向用户输出方案或分析结论，等待确认实施。",
        }

    thread_id = get_runtime_thread_id()
    logger.info(
        "准备创建 GitHub PR：owner=%s repo=%s head=%s base=%s title=%s",
        owner,
        repo,
        head,
        base,
        title,
    )
    if thread_id:
        record_event(thread_id, "github:pr", "创建或复用 Pull Request", kind="fetch", status="in_progress")

    pr = create_pull_request(owner=owner, repo=repo, head=head, base=base, title=title, body=body)
    pr_url = pr.get("html_url") or pr.get("url") or ""

    if thread_id:
        get_store().update_thread_status(thread_id, "pr_created", pr_url=pr_url, branch_name=head)
        record_event(
            thread_id,
            "github:pr",
            "创建或复用 Pull Request",
            kind="fetch",
            status="completed",
            detail=pr_url,
        )

    if pr.get("reused"):
        logger.info("GitHub PR 已存在，复用已有 PR：thread_id=%s pr_url=%s", thread_id, pr_url)
    else:
        logger.info("GitHub PR 创建成功：thread_id=%s pr_url=%s", thread_id, pr_url)
    return {"ok": True, "pr_url": pr_url, "raw": pr}


class PublishGithubPrCommentSchema(BaseModel):
    owner: str = Field(description="GitHub 仓库 owner。")
    repo: str = Field(description="GitHub 仓库名称。")
    number: int = Field(description="PR 编号。")
    body: str = Field(description="评论正文。")


@tool(args_schema=PublishGithubPrCommentSchema)
def publish_github_pr_comment(owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
    """向指定 GitHub Pull Request 发布普通评论。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        number: PR 编号。
        body: 评论正文。

    Returns:
        GitHub API 返回的评论 JSON。
    """

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能发布 PR 评论。请先向用户输出方案或分析结论，等待确认实施。",
        }

    logger.info("准备发布 GitHub PR 评论：owner=%s repo=%s number=%s", owner, repo, number)
    return post_pr_comment(owner=owner, repo=repo, number=number, body=body)


class GetGithubPullRequestContextSchema(BaseModel):
    owner: str = Field(description="GitHub 仓库 owner。")
    repo: str = Field(description="GitHub 仓库名称。")
    number: int = Field(description="PR 编号。")


@tool(args_schema=GetGithubPullRequestContextSchema)
def get_github_pull_request_context(owner: str, repo: str, number: int) -> dict[str, Any]:
    """读取 GitHub Pull Request 审查上下文。

    Reviewer 子 Agent 使用该工具获取 PR 标题、描述、变更文件、提交列表和已有评论。
    该工具只读，不会修改 GitHub 仓库。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        number: PR 编号。

    Returns:
        包含 PR 信息、提交列表、变更文件、评论和摘要统计的字典。
    """

    logger.info("读取 GitHub PR 上下文：owner=%s repo=%s number=%s", owner, repo, number)
    pr = get_pull_request(owner=owner, repo=repo, number=number)
    commits = list_pull_request_commits(owner=owner, repo=repo, number=number)
    files = list_pull_request_files(owner=owner, repo=repo, number=number)
    comments = list_pull_request_comments(owner=owner, repo=repo, number=number)
    return {
        "pull_request": pr,
        "commits": commits,
        "files": files,
        "comments": comments,
        "summary": {
            "title": pr.get("title") if isinstance(pr, dict) else None,
            "state": pr.get("state") if isinstance(pr, dict) else None,
            "files_count": len(files),
            "commits_count": len(commits),
            "comments_count": len(comments),
        },
    }
