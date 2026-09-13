"""Codeup（阿里云云效）相关 LangChain 工具。

本模块把 `utils/codeup_utils.py` 中的低层 API 调用包装成模型可调用的工具。
这里负责处理运行期上下文、只读任务拦截、任务事件记录和业务 Store 状态更新。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from agent.graph.graph import get_store
from agent.runtime.events import record_event
from utils.codeup_utils import (
    create_merge_request,
    get_merge_request,
    list_merge_request_comments,
    list_merge_request_commits,
    list_merge_request_files,
    post_mr_comment,
)
from utils.context_utils import get_runtime_thread_id, runtime_is_read_only_task

logger = logging.getLogger("agent.run.codeup")


class OpenCodeupMergeRequestSchema(BaseModel):
    owner: str = Field(description="Codeup 仓库 owner（命名空间）。")
    repo: str = Field(description="Codeup 仓库名称。")
    head: str = Field(description="已推送的源分支。")
    base: str = Field(default="master", description="目标分支，默认 master。")
    title: str = Field(default="Xihe generated changes", description="MR 标题。")
    body: str = Field(default="由 Xihe 自动生成。", description="MR 描述。")


@tool(args_schema=OpenCodeupMergeRequestSchema)
def open_codeup_merge_request(
    owner: str,
    repo: str,
    head: str,
    base: str = "master",
    title: str = "Xihe generated changes",
    body: str = "由 Xihe 自动生成。",
) -> dict[str, Any]:
    """为已经推送到 Codeup 的分支创建或复用 Merge Request。

    Args:
        owner: Codeup 仓库 owner（命名空间）。
        repo: Codeup 仓库名称。
        head: 已推送的源分支。
        base: 目标分支，默认 master。
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
        "准备创建 Codeup MR：owner=%s repo=%s head=%s base=%s title=%s",
        owner,
        repo,
        head,
        base,
        title,
    )
    if thread_id:
        record_event(thread_id, "codeup:mr", "创建或复用 Merge Request", kind="fetch", status="in_progress")

    mr = create_merge_request(
        owner=owner,
        repo=repo,
        source_branch=head,
        target_branch=base,
        title=title,
        description=body,
    )
    mr_url = mr.get("webUrl") or mr.get("detailUrl") or mr.get("html_url") or ""

    if thread_id:
        get_store().update_thread_status(thread_id, "pr_created", pr_url=mr_url, branch_name=head)
        record_event(
            thread_id,
            "codeup:mr",
            "创建或复用 Merge Request",
            kind="fetch",
            status="completed",
            detail=mr_url,
        )

    if mr.get("reused"):
        logger.info("Codeup MR 已存在，复用已有 MR：thread_id=%s mr_url=%s", thread_id, mr_url)
    else:
        logger.info("Codeup MR 创建成功：thread_id=%s mr_url=%s", thread_id, mr_url)
    return {"ok": True, "pr_url": mr_url, "raw": mr}


class PublishCodeupMrCommentSchema(BaseModel):
    owner: str = Field(description="Codeup 仓库 owner（命名空间）。")
    repo: str = Field(description="Codeup 仓库名称。")
    number: int = Field(description="MR 局部编号（localId）。")
    body: str = Field(description="评论正文。")


@tool(args_schema=PublishCodeupMrCommentSchema)
def publish_codeup_mr_comment(owner: str, repo: str, number: int, body: str) -> dict[str, Any]:
    """向指定 Codeup Merge Request 发布全局评论。

    Args:
        owner: Codeup 仓库 owner（命名空间）。
        repo: Codeup 仓库名称。
        number: MR 局部编号（localId）。
        body: 评论正文。

    Returns:
        Codeup API 返回的评论 JSON。
    """

    if runtime_is_read_only_task():
        return {
            "ok": False,
            "error": "当前任务是只读任务，不能发布 MR 评论。请先向用户输出方案或分析结论，等待确认实施。",
        }

    logger.info("准备发布 Codeup MR 评论：owner=%s repo=%s number=%s", owner, repo, number)
    return post_mr_comment(owner=owner, repo=repo, local_id=number, body=body)


class GetCodeupMergeRequestContextSchema(BaseModel):
    owner: str = Field(description="Codeup 仓库 owner（命名空间）。")
    repo: str = Field(description="Codeup 仓库名称。")
    number: int = Field(description="MR 局部编号（localId）。")


@tool(args_schema=GetCodeupMergeRequestContextSchema)
def get_codeup_merge_request_context(owner: str, repo: str, number: int) -> dict[str, Any]:
    """读取 Codeup Merge Request 审查上下文。

    Reviewer 子 Agent 使用该工具获取 MR 标题、描述、变更文件、提交列表和已有评论。
    该工具只读，不会修改 Codeup 仓库。

    Args:
        owner: Codeup 仓库 owner（命名空间）。
        repo: Codeup 仓库名称。
        number: MR 局部编号（localId）。

    Returns:
        包含 MR 信息、提交列表、变更文件、评论和摘要统计的字典。
    """

    logger.info("读取 Codeup MR 上下文：owner=%s repo=%s number=%s", owner, repo, number)
    mr = get_merge_request(owner=owner, repo=repo, local_id=number)
    commits = list_merge_request_commits(owner=owner, repo=repo, local_id=number)
    files = list_merge_request_files(owner=owner, repo=repo, local_id=number)
    comments = list_merge_request_comments(owner=owner, repo=repo, local_id=number)
    return {
        "merge_request": mr,
        "commits": commits,
        "files": files,
        "comments": comments,
        "summary": {
            "title": mr.get("title") if isinstance(mr, dict) else None,
            "status": mr.get("status") if isinstance(mr, dict) else None,
            "files_count": len(files),
            "commits_count": len(commits),
            "comments_count": len(comments),
        },
    }
