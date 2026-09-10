"""主任务路由。

负责 Agent 任务的创建、执行、查询和删除。这是 runtime 的核心入口，
根据用户意图决定走同步操作、方案生成还是完整 Agent 执行流程。

核心流程：
1. 工作区查询和 git pull 等轻量任务直接执行，不调用模型。
2. 首轮 coding 需求先转成 planning，输出技术方案并等待用户确认。
3. 用户确认后，从 checkpoint 找到上一轮方案，再切换为 coding。
4. 普通 qa/analysis/review 等只读任务直接构建对应 task_kind 的 DeepAgent。
5. 任务结束后更新 Store 状态和仓库级长期记忆。
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any

from agent.graph.graph import get_checkpointer, get_store
from agent.memory.repo_update import RepoMemoryUpdate, update_repo_memory_from_text
from agent.runtime._helpers import (
    build_agent_for_runtime,
    extract_final_assistant_text,
    is_approval_prompt,
    is_plan_revision_prompt,
    latest_confirmable_plan_message,
    latest_non_approval_user_prompt,
    message_metadata,
    plan_source_prompt,
    revision_source_prompt,
)
from agent.runtime.events import record_event
from agent.runtime.planning_router import run_plan_response_task
from agent.runtime.streaming_runtime import run_agent_with_event_stream
from agent.runtime.sync_router import run_pull_only_task, run_workspace_listing_task
from agent.runtime.task_intent import classify_task_kind, is_pull_only_task, is_workspace_listing_task
from urllib.parse import urlparse

from utils.codeup_utils import parse_codeup_repo_url
from utils.gitee_utils import mask_token, parse_gitee_repo_url
from utils.github_utils import parse_github_repo_url

logger = logging.getLogger(__name__)

RuntimeEventSink = Callable[[str, dict[str, Any]], None]


def _parse_repo_url(repo_url: str):
    """根据 URL 域名自动识别 SCM 平台并解析仓库信息。"""
    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname in {"codeup.aliyun.com", "www.codeup.aliyun.com"}:
        return parse_codeup_repo_url(repo_url)
    if hostname in {"github.com", "www.github.com"}:
        return parse_github_repo_url(repo_url)
    return parse_gitee_repo_url(repo_url)


def initialize_task_record(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    record_user_message: bool = True,
) -> str:
    """先创建 dashboard 可见的 thread 记录。

    前端创建任务时需要马上拿到 thread_id 跳转页面，所以这里先写入业务 Store，
    后台任务再继续执行真正的 Agent 或 git pull。

    Store 中的 thread 记录是前端任务列表和任务状态的业务索引，不等同于
    LangGraph checkpoint。真实对话消息仍由 checkpoint 保存。
    """

    thread_id = thread_id or str(uuid.uuid4())
    repo = _parse_repo_url(repo_url)
    get_store().upsert_thread(
        thread_id=thread_id,
        title=prompt[:80] or f"{repo.owner}/{repo.repo}",
        user_prompt=prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    record_event(thread_id, "created", "任务已创建", status="completed")
    return thread_id


def run_agent_task(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> dict[str, Any]:
    """运行一次普通 Agent 任务的总入口。

    这是 runtime 最重要的函数。FastAPI 后台任务最终会调用它完成一次用户输入。
    它的职责不是直接解决问题，而是决定本轮应该走哪条流程：

    1. 工作区查询和 git pull 这类简单任务直接执行，不调用模型。
    2. 首轮 coding 需求先转成 planning，输出技术方案并等待用户确认。
    3. 用户确认后，从 checkpoint 找到上一轮方案，再切换为 coding。
    4. 普通 qa/analysis/review 等只读任务直接构建对应 task_kind 的 DeepAgent。
    5. 任务结束后更新 Store 状态和仓库级长期记忆。
    """

    if is_workspace_listing_task(prompt):
        return run_workspace_listing_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    if is_pull_only_task(prompt):
        return run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    task_kind = classify_task_kind(prompt)
    thread_id = thread_id or str(uuid.uuid4())
    store = get_store()
    existing_thread = store.get_thread(thread_id)
    approved_plan_text: str | None = None
    display_prompt = prompt
    coding_prompt = prompt

    if existing_thread and is_approval_prompt(prompt):
        plan_message = latest_confirmable_plan_message(thread_id)
        if plan_message is not None:
            metadata = message_metadata(plan_message)
            approved_plan_text = str(plan_message.get("content") or "")
            coding_prompt = str(
                metadata.get("source_prompt")
                or latest_non_approval_user_prompt(thread_id, existing_thread.get("user_prompt") or prompt)
            )
            task_kind = "coding"
    elif existing_thread:
        plan_message = latest_confirmable_plan_message(thread_id) if is_plan_revision_prompt(prompt) else None
        if plan_message is not None and prompt.strip():
            return run_plan_response_task(
                repo_url=repo_url,
                prompt=plan_source_prompt(plan_message, latest_non_approval_user_prompt(thread_id, prompt)),
                thread_id=thread_id,
                previous_plan_message=plan_message,
                revision_prompt=prompt,
                event_sink=event_sink,
            )
    if existing_thread and approved_plan_text is None and is_approval_prompt(prompt):
        task_kind = classify_task_kind(prompt)

    if approved_plan_text is not None:
        task_kind = "coding"

    if task_kind == "coding" and approved_plan_text is None:
        return run_plan_response_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id, event_sink=event_sink)

    store.clear_run_events(thread_id)
    if approved_plan_text is not None:
        record_event(thread_id, "plan:approved", "用户已确认技术方案", kind="other", status="completed")
    logger.info("任务开始：thread_id=%s repo_url=%s", thread_id, repo_url)
    record_event(thread_id, "created", "任务已创建", status="completed")
    record_event(thread_id, "repo", "解析仓库", status="in_progress")
    repo = _parse_repo_url(repo_url)
    logger.info("仓库解析成功：owner=%s repo=%s", repo.owner, repo.repo)
    record_event(thread_id, "repo", "解析仓库", status="completed")
    store.upsert_thread(
        thread_id=thread_id,
        title=coding_prompt[:80] or f"{repo.owner}/{repo.repo}",
        user_prompt=display_prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
    )
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    logger.info("业务 Store 已记录运行：thread_id=%s run_id=%s", thread_id, run_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="in_progress")
    agent = build_agent_for_runtime(thread_id=thread_id, task_kind=task_kind, repo_url=repo.clone_url)
    logger.info("Agent 图已构建：thread_id=%s", thread_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="completed")
    try:
        logger.info("开始通过官方事件流调用 Agent：thread_id=%s", thread_id)
        from agent.runtime._helpers import build_agent_user_content

        result = run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            run_id=run_id,
            content=build_agent_user_content(
                repo_url=repo.clone_url,
                task_kind=task_kind,
                prompt=coding_prompt,
                display_prompt=display_prompt,
                approved_plan=approved_plan_text,
            ),
            task_kind=task_kind,
            event_sink=event_sink,
        )
        store.finish_open_run_events(thread_id, status="completed")
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "done", "任务完成", status="completed")
        messages = result.get("messages", [])
        final_answer = extract_final_assistant_text(messages)
        if final_answer:
            from agent.graph.graph import get_langgraph_store

            latest_thread = store.get_thread(thread_id) or {}
            update_repo_memory_from_text(
                store=get_langgraph_store(),
                repo=repo,
                update=RepoMemoryUpdate(
                    task_kind=task_kind,
                    final_text=final_answer,
                    branch_name=latest_thread.get("branch_name"),
                    pr_url=latest_thread.get("pr_url"),
                ),
            )
        logger.info("任务完成：thread_id=%s run_id=%s messages=%s", thread_id, run_id, len(messages))
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed", "messages": messages}
    except Exception as exc:
        store.finish_open_run_events(thread_id, status="error")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "model", "调用 deepseek-v4-pro", status="error")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("任务失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def get_task(thread_id: str) -> dict[str, Any] | None:
    """读取单个任务摘要，并附带 reviewer findings。

    主要服务 Dashboard API。读取的是业务摘要，不是完整聊天历史。
    """

    store = get_store()
    thread = store.get_thread(thread_id)
    if thread is None:
        return None
    thread["findings"] = store.list_findings(thread_id)
    thread["latest_run"] = store.get_latest_run(thread_id)
    thread["run_events"] = store.list_run_events(thread_id)
    return thread


def list_tasks(limit: int = 50) -> list[dict[str, Any]]:
    """读取最近任务列表，供页面展示历史运行记录。

    返回值包含最新 run 和 run_events，方便列表页展示任务状态。
    """

    store = get_store()
    threads = store.list_threads(limit=limit)
    for thread in threads:
        tid = thread["thread_id"]
        thread["latest_run"] = store.get_latest_run(tid)
        thread["run_events"] = store.list_run_events(tid)
    return threads


def delete_task(thread_id: str) -> bool:
    """删除一个 dashboard 会话。

    Store 负责删除业务索引和运行记录；checkpointer 负责删除 LangGraph thread state。
    两者都清理后，页面历史和 Agent 上下文才会真正消失。
    """

    deleted = get_store().delete_thread(thread_id)
    if not deleted:
        return False
    try:
        get_checkpointer().delete_thread(thread_id)
    except Exception:
        logger.exception("删除 checkpoint 失败：thread_id=%s", thread_id)
    return True
