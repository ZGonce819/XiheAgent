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
import shlex
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
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
from agent.runtime.events import mark_tool_events_rejected, record_event
from agent.runtime.planning_router import run_plan_response_task
from agent.runtime.streaming_runtime import run_agent_with_event_stream
from agent.runtime.sync_router import run_pull_only_task, run_workspace_listing_task
from agent.runtime.task_intent import (
    classify_task_kind,
    is_pull_only_task,
    is_simple_coding_task,
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


def _ensure_local_repository(*, thread_id: str, repo: Any) -> Path | None:
    """准备本地 Git 仓库；远程不可用时返回 ``None`` 并继续任务。"""
    from agent.runtime._helpers import resolve_project_local_folder
    from agent.runtime.repo_mapping import save_clone_mapping
    from backends.local_shell import LocalShellBackend
    from backends.workspace import Workspace
    from config.app_config import get_settings

    settings = get_settings()
    local_folder = resolve_project_local_folder(thread_id)
    if local_folder:
        workspace = Workspace(Path(local_folder))
        backend = LocalShellBackend(
            workspace,
            local_folder_mode=True,
            global_workspace_root=settings.workspace_root,
        )
        target = workspace.resolve(".")
        cwd = "."
    else:
        workspace = Workspace(settings.workspace_root)
        backend = LocalShellBackend(workspace)
        settings.projects_dir.mkdir(parents=True, exist_ok=True)
        target = workspace.resolve(Path("projects") / repo.repo)
        cwd = f"projects/{repo.repo}"

    if (target / ".git").exists():
        return target

    record_event(thread_id, "workspace", "克隆远程仓库", kind="execute", status="in_progress")
    if local_folder:
        # 本地文件夹中已有 .xihe-agent，不能使用要求目标目录为空的 `git clone .`。
        required = []
        for command in (
            "git init",
            f"git remote add origin {repo.clone_url}",
            "git fetch origin",
        ):
            result = backend.run(command, cwd=cwd, timeout=600)
            required.append(result)
            if result.exit_code != 0:
                break
        failed = next((result for result in required if result.exit_code != 0), None)
        if failed is None:
            head_result = backend.run("git remote set-head origin --auto", cwd=cwd, timeout=60)
            if head_result.exit_code == 0:
                symbolic = backend.run(
                    "git symbolic-ref --short refs/remotes/origin/HEAD",
                    cwd=cwd,
                    timeout=60,
                )
                remote_branch = symbolic.stdout.strip()
                if symbolic.exit_code == 0 and remote_branch.startswith("origin/"):
                    branch = remote_branch.removeprefix("origin/")
                    checkout = backend.run(
                        f"git checkout -B {branch} --track {remote_branch}",
                        cwd=cwd,
                        timeout=300,
                    )
                    failed = checkout if checkout.exit_code != 0 else None
    else:
        result = backend.run(
            f"git clone {repo.clone_url} {repo.repo}", cwd="projects", timeout=600
        )
        failed = result if result.exit_code != 0 else None

    if failed is not None or not (target / ".git").exists():
        detail = mask_token(
            (failed.stderr or failed.stdout) if failed is not None else "克隆完成后未发现 .git"
        )
        # 本地文件夹模式会先执行 git init。后续 fetch 失败时移除本次新建的
        # 元数据，避免下一轮仅因 .git 存在就误判仓库已经准备完成。
        if local_folder and (target / ".git").exists():
            shutil.rmtree(target / ".git", ignore_errors=True)
        record_event(
            thread_id,
            "workspace",
            "远程仓库不可用，继续无仓库模式",
            kind="execute",
            status="completed",
            detail=detail,
        )
        logger.warning(
            "远程仓库不可用，继续无仓库模式：thread_id=%s detail=%s",
            thread_id,
            detail,
        )
        return None

    if not local_folder:
        save_clone_mapping(
            repo_url=repo.clone_url,
            project_dir=cwd,
            local_path=str(target),
            store=get_store(),
        )
    record_event(thread_id, "workspace", "克隆远程仓库", kind="execute", status="completed")
    return target


def _ensure_sandbox_repository(*, thread_id: str, repo: Any) -> str | None:
    """在远程沙盒中准备仓库；远程不可用时进入无仓库模式。"""
    from backends.opensandbox import OpenSandboxBackend
    from server import ensure_backend_for_thread

    backend = ensure_backend_for_thread(thread_id, backend_type="sandbox")
    if not isinstance(backend, OpenSandboxBackend):
        raise RuntimeError("沙盒后端不可用，无法准备远程仓库")

    target = f"/workspace/projects/{repo.repo}"
    quoted_target = shlex.quote(target)
    if backend.execute(f"test -d {quoted_target}/.git").exit_code == 0:
        return target

    record_event(thread_id, "workspace", "克隆远程仓库", kind="execute", status="in_progress")
    prepare = backend.execute("mkdir -p /workspace/projects")
    if prepare.exit_code != 0:
        detail = mask_token(prepare.output or "无法创建沙盒项目目录")
        raise RuntimeError(f"沙盒工作区准备失败：{detail}")

    clone = backend.execute(
        f"git clone {shlex.quote(repo.clone_url)} {quoted_target}",
        timeout=600,
    )
    verify = backend.execute(f"test -d {quoted_target}/.git")
    if clone.exit_code != 0 or verify.exit_code != 0:
        detail = mask_token(clone.output or "克隆完成后未发现 .git")
        record_event(
            thread_id,
            "workspace",
            "远程仓库不可用，继续无仓库模式",
            kind="execute",
            status="completed",
            detail=detail,
        )
        logger.warning(
            "沙盒远程仓库不可用，继续无仓库模式：thread_id=%s detail=%s",
            thread_id,
            detail,
        )
        return None

    record_event(thread_id, "workspace", "克隆远程仓库", kind="execute", status="completed")
    return target


def _ensure_repository(
    *, thread_id: str, repo: Any, backend_type: str
) -> Path | str | None:
    """根据运行环境准备仓库，并返回 Agent 可使用的仓库根目录。"""
    if backend_type == "sandbox":
        return _ensure_sandbox_repository(thread_id=thread_id, repo=repo)
    return _ensure_local_repository(thread_id=thread_id, repo=repo)


def initialize_task_record(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    record_user_message: bool = True,
    backend_type: str | None = None,
    project_name: str | None = None,
    local_work_dir: str | None = None,
    source: str | None = None,
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
        local_work_dir=local_work_dir,
        source=source,
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
    routing_prompt: str | None = None,
    task_kind: str | None = None,
    skip_plan_confirmation: bool = False,
) -> dict[str, Any]:
    """运行一次普通 Agent 任务的总入口。

    这是 runtime 最重要的函数。FastAPI 后台任务最终会调用它完成一次用户输入。
    它的职责不是直接解决问题，而是决定本轮应该走哪条流程：

    1. 工作区查询和 git pull 这类简单任务直接执行，不调用模型。
    2. 首轮 coding 需求先转成 planning，输出技术方案并等待用户确认。
    3. 用户确认后，从 checkpoint 找到上一轮方案，再切换为 coding。
    4. 普通 qa/analysis/review 等只读任务直接构建对应 task_kind 的 DeepAgent。
    5. 任务结束后更新 Store 状态和仓库级长期记忆。

    Args:
        prompt: 真正发给模型的内容。
        routing_prompt: 决定走哪条流程时看的文本；不传则看 ``prompt``。
            **两者必须分开的场合**：自动化任务的循环会在用户写的任务指令后面拼上
            循环状态与上一轮验证反馈，那两段附言里出现「提交」「push」这类词时，
            拿拼好的文本判类型会把只读任务判成开发实现，于是走进等确认的分支停住。
            判定只能看用户写的那一句，不能看附言。
        task_kind: 已经判好的任务类型；不传则自己判。调用方已经判过一次时传进来，
            既保证两边结论一致，也省掉一次模型调用。
        skip_plan_confirmation: 首轮开发需求是否跳过「先出方案、等用户确认」这一步，
            直接进入实现。**只给无人值守的循环用**：那里没有人能点确认，走原路径
            等于每轮都停在同一步，还要被记成「没做完但没报错」。交互路径必须保持
            默认值 ``False`` —— 用户看着的时候，方案确认是一道有价值的人工闸门。
    """

    thread_id = thread_id or str(uuid.uuid4())
    routing_text = prompt if routing_prompt is None else routing_prompt

    if is_workspace_listing_task(routing_text):
        record_event(thread_id, "intent", "已识别任务类型：工作区查看", status="completed")
        return run_workspace_listing_task(
            repo_url=repo_url,
            prompt=prompt,
            thread_id=thread_id,
            backend_type=backend_type,
        )

    if is_pull_only_task(routing_text):
        record_event(thread_id, "intent", "已识别任务类型：仓库同步", status="completed")
        return run_pull_only_task(repo_url=repo_url, prompt=prompt, thread_id=thread_id)

    if task_kind is None:
        record_event(thread_id, "intent", "正在识别任务类型", status="in_progress")
        task_kind = classify_task_kind(routing_text)
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

    if existing_thread and is_approval_prompt(routing_text):
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
        plan_message = latest_confirmable_plan_message(thread_id) if is_plan_revision_prompt(routing_text) else None
        if plan_message is not None and routing_text.strip():
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
    if existing_thread and approved_plan_text is None and is_approval_prompt(routing_text):
        logger.warning("用户确认实施但未找到待确认方案：thread_id=%s", thread_id)
        record_event(thread_id, "plan:not_found", "未找到待确认的技术方案", kind="other", status="error")
        store.update_thread_status(thread_id, "failed")
        raise ValueError("未找到待确认的技术方案，请重新描述您的需求")

    if approved_plan_text is not None:
        task_kind = "coding"

    # 简单编码任务跳过方案规划，直接执行
    skip_planning_for_simple_task = (
        task_kind == "coding"
        and approved_plan_text is None
        and not skip_plan_confirmation
        and is_simple_coding_task(routing_text)
    )

    if task_kind == "coding" and approved_plan_text is None and not skip_plan_confirmation and not skip_planning_for_simple_task:
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
    repository_path = _ensure_repository(
        thread_id=thread_id,
        repo=repo,
        backend_type=backend_type,
    )
    repository_available = repository_path is not None
    repository_agent_path = (
        str(repository_path) if backend_type == "sandbox" and repository_path else None
    )
    # 延迟导入，避免 services 包的兼容导出在 runtime 初始化期间形成循环依赖。
    from services.repo_rollback import git_head, git_parent
    base_commit = (
        git_head(repo.clone_url, store)
        if repository_available and backend_type != "sandbox"
        else None
    )
    store.record_run(run_id=run_id, thread_id=thread_id, status="running", base_commit=base_commit)
    logger.info("业务 Store 已记录运行：thread_id=%s run_id=%s", thread_id, run_id)
    record_event(thread_id, "agent", "构建 Agent 运行图", status="in_progress")
    agent = build_agent_for_runtime(
        thread_id=thread_id,
        task_kind=task_kind,
        repo_url=repo.clone_url if repository_available else None,
        model_id=model_id,
        backend_type=backend_type,
    )
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
                repository_available=repository_available,
                repository_path=repository_agent_path,
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
        head_commit = (
            git_head(repo.clone_url, store)
            if repository_available and backend_type != "sandbox"
            else None
        )
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
    reject_message: str | None = None,
    event_sink: RuntimeEventSink | None = None,
) -> dict[str, Any]:
    """Resume the exact LangGraph checkpoint paused for command approval.

    Args:
        thread_id: 会话 id。
        decision: ``approve`` 或 ``reject``。
        approval_id: 待审批记录的 id，用于确认恢复的是同一次中断。
        reject_message: 拒绝时交回给模型的话。不传则用「用户拒绝了这次命令执行。」——
            这是**人在界面点拒绝**时该说的话。代行的表决必须自己传一份：它得说清
            是机器投的票、以及否决理由，否则模型会以为有人在看着它，而且拿不到
            让它换做法的信息。
        event_sink: 运行时事件接收器。

    Raises:
        ValueError: 审批决定非法、待审批操作已失效，或会话不存在。
    """
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
            else {
                "type": "reject",
                "message": reject_message or "用户拒绝了这次命令执行。",
            }
            for _action in actions
        ]
        if decision == "reject":
            mark_tool_events_rejected(thread_id)
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

        head_commit = (
            git_head(repo.clone_url, store) if backend_type != "sandbox" else None
        )
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
