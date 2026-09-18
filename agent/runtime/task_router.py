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
    is_approval_prompt,
    is_plan_revision_prompt,
    latest_confirmable_plan_message,
    latest_non_approval_user_prompt,
    message_metadata,
    plan_source_prompt,
)
from agent.runtime.events import record_event
from agent.runtime.planning_router import run_plan_response_task
from agent.runtime.streaming_runtime import run_agent_with_event_stream
from agent.runtime.sync_router import run_pull_only_task, run_workspace_listing_task
from agent.runtime.task_intent import (
    classify_task_kind,
    is_pull_only_task,
    is_workspace_listing_task,
)
from config.approval_runtime import (
    claim_pending_approval,
    clear_pending_approval,
    release_approval_claim,
    save_pending_approval,
)
from utils import extract_final_assistant_text
from utils.gitee_utils import mask_token
from utils.platform_utils import parse_repo_url

logger = logging.getLogger(__name__)

RuntimeEventSink = Callable[[str, dict[str, Any]], None]

TASK_KIND_LABELS = {
    "coding": "开发实现",
    "planning": "方案规划",
    "analysis": "项目分析",
    "qa": "问题解答",
    "sync": "仓库同步",
    "inspect": "工作区查看",
    "review": "代码审查",
}


def initialize_task_record(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    record_user_message: bool = True,
    backend_type: str | None = None,
    project_name: str | None = None,
) -> str:
    """先创建 dashboard 可见的 thread 记录。

    前端创建任务时需要马上拿到 thread_id 跳转页面，所以这里先写入业务 Store，
    后台任务再继续执行真正的 Agent 或 git pull。

    Store 中的 thread 记录是前端任务列表和任务状态的业务索引，不等同于
    LangGraph checkpoint。真实对话消息仍由 checkpoint 保存。
    """

    thread_id = thread_id or str(uuid.uuid4())
    repo = parse_repo_url(repo_url)
    if backend_type is None:
        from config.app_config import get_settings
        backend_type = "sandbox" if get_settings().use_sandbox else "local"
    get_store().upsert_thread(
        thread_id=thread_id,
        title=prompt[:80] or f"{repo.owner}/{repo.repo}",
        user_prompt=prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
        backend_type=backend_type,
        project_name=project_name,
    )
    record_event(thread_id, "created", "任务已创建", status="completed")
    return thread_id


def run_agent_task(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    event_sink: RuntimeEventSink | None = None,
    run_id: str | None = None,
    checkpoint_id: str | None = None,
    model_id: str | None = None,
    backend_type: str | None = None,
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

    thread_id = thread_id or str(uuid.uuid4())

    if is_workspace_listing_task(prompt):
        record_event(thread_id, "intent", "已识别任务类型：工作区查看", status="completed")
        return run_workspace_listing_task(
            repo_url=repo_url,
            prompt=prompt,
            thread_id=thread_id,
            backend_type=backend_type,
        )

    if is_pull_only_task(prompt):
        record_event(thread_id, "intent", "已识别任务类型：仓库同步", status="completed")
        return run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    record_event(thread_id, "intent", "正在识别任务类型", status="in_progress")
    task_kind = classify_task_kind(prompt)
    task_kind_label = TASK_KIND_LABELS.get(task_kind, task_kind)
    record_event(
        thread_id,
        "intent",
        f"已识别任务类型：{task_kind_label}",
        status="completed",
    )
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
                run_id=run_id,
                checkpoint_id=checkpoint_id,
                model_id=model_id,
                backend_type=backend_type,
                project_name=existing_thread.get("project_name"),
            )
    if existing_thread and approved_plan_text is None and is_approval_prompt(prompt):
        logger.warning("用户确认实施但未找到待确认方案：thread_id=%s", thread_id)
        record_event(thread_id, "plan:not_found", "未找到待确认的技术方案", kind="other", status="error")
        store.update_thread_status(thread_id, "failed")
        raise ValueError("未找到待确认的技术方案，请重新描述您的需求")

    if approved_plan_text is not None:
        task_kind = "coding"

    if task_kind == "coding" and approved_plan_text is None:
        return run_plan_response_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id, event_sink=event_sink, run_id=run_id, checkpoint_id=checkpoint_id, model_id=model_id, backend_type=backend_type, project_name=existing_thread.get("project_name") if existing_thread else None)

    store.clear_run_events(thread_id)
    if approved_plan_text is not None:
        record_event(thread_id, "plan:approved", "用户已确认技术方案", kind="other", status="completed")
    logger.info("任务开始：thread_id=%s repo_url=%s", thread_id, repo_url)
    record_event(thread_id, "created", "任务已创建", status="completed")
    record_event(thread_id, "repo", "解析仓库", status="in_progress")
    repo = parse_repo_url(repo_url)
    logger.info("仓库解析成功：owner=%s repo=%s", repo.owner, repo.repo)
    record_event(thread_id, "repo", "解析仓库", status="completed")
    if backend_type is None:
        from config.app_config import get_settings
        backend_type = "sandbox" if get_settings().use_sandbox else "local"
    store.upsert_thread(
        thread_id=thread_id,
        title=coding_prompt[:80] or f"{repo.owner}/{repo.repo}",
        user_prompt=display_prompt,
        repo_url=repo.clone_url,
        repo_owner=repo.owner,
        repo_name=repo.repo,
        latest_run_status="running",
        backend_type=backend_type,
        project_name=existing_thread.get("project_name") if existing_thread else None,
    )
    run_id = run_id or str(uuid.uuid4())
    # 延迟导入，避免 services 包的兼容导出在 runtime 初始化期间形成循环依赖。
    from services.repo_rollback import git_head, git_parent
    base_commit = git_head(repo.clone_url, store)
    store.record_run(run_id=run_id, thread_id=thread_id, status="running", base_commit=base_commit)
    logger.info("业务 Store 已记录运行：thread_id=%s run_id=%s", thread_id, run_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="in_progress")
    agent = build_agent_for_runtime(thread_id=thread_id, task_kind=task_kind, repo_url=repo.clone_url, model_id=model_id, backend_type=backend_type)
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
            checkpoint_id=checkpoint_id,
        )
        if result.get("interrupted"):
            pending = save_pending_approval(
                thread_id=thread_id,
                run_id=run_id,
                interrupt_id=str(result.get("interrupt_id") or ""),
                approval=result.get("approval") or {},
                repo_url=repo.clone_url,
                backend_type=backend_type,
                task_kind=task_kind,
                model_id=model_id,
            )
            store.update_thread_status(thread_id, "awaiting_approval")
            store.record_run(
                run_id=run_id,
                thread_id=thread_id,
                status="awaiting_approval",
                base_commit=base_commit,
            )
            return {
                "thread_id": thread_id,
                "run_id": run_id,
                "status": "awaiting_approval",
                "approval": pending,
                "messages": result.get("messages", []),
            }
        store.finish_open_run_events(thread_id, status="completed")
        store.update_thread_status(thread_id, "completed")
        head_commit = git_head(repo.clone_url, store)
        # 首次任务可能由 Agent 自己完成 clone，启动前尚无 HEAD。此时第一
        # 个提交的父提交就是可回退的仓库基线。
        if base_commit is None and head_commit:
            base_commit = git_parent(repo.clone_url, store)
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="completed",
            base_commit=base_commit,
            head_commit=head_commit,
            finished=True,
        )
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
        record_event(thread_id, "model", "调用大模型", status="error")
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


def resume_agent_task(
    *,
    thread_id: str,
    decision: str,
    approval_id: str,
    event_sink: RuntimeEventSink | None = None,
) -> dict[str, Any]:
    """Resume the exact LangGraph checkpoint paused for command approval."""
    if decision not in {"approve", "reject"}:
        raise ValueError("无效的审批决定")
    pending = claim_pending_approval(thread_id, approval_id)
    try:
        actions = pending.get("actions") or []
        if not actions:
            raise ValueError("待审批操作已失效，请重新发起任务")

        store = get_store()
        thread = store.get_thread(thread_id)
        if thread is None:
            clear_pending_approval(thread_id)
            raise ValueError("会话不存在")

        repo_url = str(pending.get("repoUrl") or thread.get("repo_url") or "")
        task_kind = str(pending.get("taskKind") or "coding")
        backend_type = str(
            pending.get("backendType") or thread.get("backend_type") or "local"
        )
        model_id = pending.get("modelId")
        run_id = str(pending.get("runId") or uuid.uuid4())
        repo = parse_repo_url(repo_url)
        latest_run = store.get_latest_run(thread_id) or {}
        base_commit = latest_run.get("base_commit")

        store.update_thread_status(thread_id, "running")
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="running",
            base_commit=base_commit,
        )
        agent = build_agent_for_runtime(
            thread_id=thread_id,
            task_kind=task_kind,
            repo_url=repo.clone_url,
            model_id=model_id,
            backend_type=backend_type,
        )
        decisions = [
            {"type": "approve"}
            if decision == "approve"
            else {"type": "reject", "message": "用户拒绝了这次命令执行。"}
            for _action in actions
        ]
    except Exception:
        release_approval_claim(thread_id)
        raise

    try:
        result = run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            run_id=run_id,
            content="",
            task_kind=task_kind,
            event_sink=event_sink,
            resume_decisions=decisions,
        )
        if result.get("interrupted"):
            next_pending = save_pending_approval(
                thread_id=thread_id,
                run_id=run_id,
                interrupt_id=str(result.get("interrupt_id") or ""),
                approval=result.get("approval") or {},
                repo_url=repo.clone_url,
                backend_type=backend_type,
                task_kind=task_kind,
                model_id=model_id,
            )
            store.update_thread_status(thread_id, "awaiting_approval")
            store.record_run(
                run_id=run_id,
                thread_id=thread_id,
                status="awaiting_approval",
                base_commit=base_commit,
            )
            return {
                "thread_id": thread_id,
                "run_id": run_id,
                "status": "awaiting_approval",
                "approval": next_pending,
                "messages": result.get("messages", []),
            }

        clear_pending_approval(thread_id)
        store.finish_open_run_events(thread_id, status="completed")
        store.update_thread_status(thread_id, "completed")
        if task_kind == "planning":
            from utils.message_utils import extract_best_plan_text

            if not extract_best_plan_text(result.get("messages", [])):
                raise RuntimeError("技术方案生成失败：模型没有返回可用方案")
            record_event(
                thread_id,
                "plan",
                "技术方案已输出，等待确认",
                kind="other",
                status="completed",
            )
        from services.repo_rollback import git_head

        head_commit = git_head(repo.clone_url, store)
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="completed",
            base_commit=base_commit,
            head_commit=head_commit,
            finished=True,
        )
        if task_kind != "planning":
            record_event(thread_id, "done", "任务完成", status="completed")
        return {
            "thread_id": thread_id,
            "run_id": run_id,
            "status": "completed",
            "messages": result.get("messages", []),
        }
    except Exception as exc:
        release_approval_claim(thread_id)
        store.finish_open_run_events(thread_id, status="error")
        store.update_thread_status(thread_id, "failed")
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
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
    沙盒模式下远程沙箱也会一并销毁。
    """

    # 先销毁远程沙箱（在删除业务记录前执行，避免丢失沙箱引用）
    _destroy_thread_sandbox(thread_id)

    deleted = get_store().delete_thread(thread_id)
    if not deleted:
        return False
    clear_pending_approval(thread_id)
    try:
        get_checkpointer().delete_thread(thread_id)
    except Exception as exc:
        # 索引尚未创建（thread 从未产生过 checkpoint）属于正常情况，降级处理。
        if "No such index" in str(exc):
            logger.debug("删除 checkpoint 跳过（索引不存在）：thread_id=%s", thread_id)
        else:
            logger.exception("删除 checkpoint 失败：thread_id=%s", thread_id)
    return True


def _destroy_thread_sandbox(thread_id: str) -> None:
    """销毁线程关联的远程沙箱。

    先尝试从内存缓存中销毁 backend 实例，再清理注册表中的残留记录。
    任何环节失败均不阻断删除流程。
    """
    try:
        from config.app_config import get_settings

        if not get_settings().use_sandbox:
            return

        # 从 server.py 的内存缓存中销毁 backend
        from backends.opensandbox import OpenSandboxBackend
        from server import _BACKENDS

        backend = _BACKENDS.pop(thread_id, None)
        if isinstance(backend, OpenSandboxBackend):
            logger.info("删除线程时销毁沙箱：thread_id=%s sandbox_id=%s", thread_id, backend.sandbox_id)
            backend.destroy()

        # 清理注册表中的残留记录
        from store.sandbox_repo import SandboxRepo

        repo = SandboxRepo()
        removed = repo.delete_by_thread(thread_id)
        if removed:
            logger.info("已清理沙箱注册表：%d 条（thread_id=%s）", removed, thread_id)

    except Exception:
        logger.warning("销毁线程沙箱失败：thread_id=%s", thread_id, exc_info=True)
