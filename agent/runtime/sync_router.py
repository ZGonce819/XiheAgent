"""同步操作路由。

处理不需要调用大模型的轻量级同步任务：
1. 列出本地工作区项目目录。
2. 只拉取远程仓库代码（git fetch + pull），不修改、不提交、不创建 PR。

这些任务走独立的直达分支，不构建 DeepAgent，不消耗模型调用次数。
但仍然会写入 thread/run/run_events，保证前端展示方式和普通任务一致。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from agent.graph.graph import get_store
from agent.memory.repo import repo_project_dir
from agent.runtime._helpers import ensure_repo_memory_for_repo
from agent.runtime.events import record_event
from backends.local_shell import LocalShellBackend
from backends.workspace import Workspace
from config.constants import PROJECTS_DIR, WORKSPACE_ROOT
from utils.gitee_utils import mask_token
from utils.platform_utils import parse_repo_url

logger = logging.getLogger(__name__)


def run_workspace_listing_task(
    *,
    repo_url: str,
    prompt: str,
    thread_id: str | None = None,
    backend_type: str | None = None,
) -> dict[str, Any]:
    """直接列出本地工作区项目，不调用模型。

    这是一个"直达分支"：用户只是问本地工作区有哪些项目时，没有必要构建 Agent。
    函数仍然会写 thread/run/run_events，是为了前端展示方式和普通任务保持一致。

    Args:
        repo_url: 仓库完整地址。
        prompt: 用户输入文本。
        thread_id: 线程 ID；为 ``None`` 时自动创建。
        backend_type: 当前会话的执行环境，透传到线程记录。

    Returns:
        包含 thread_id、run_id、status、projects 的结果字典。
    """

    should_record_user_message = thread_id is None
    from agent.runtime.task_router import initialize_task_record

    thread_id = initialize_task_record(
        repo_url=repo_url,
        prompt=prompt,
        thread_id=thread_id,
        record_user_message=should_record_user_message,
        backend_type=backend_type,
    )
    store = get_store()
    store.clear_run_events(thread_id)
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    workspace = Workspace(WORKSPACE_ROOT)
    backend = LocalShellBackend(workspace)
    try:
        # 所有可见步骤都通过 record_event 写入 run_events，前端可以复用同一套事件展示组件。
        record_event(thread_id, "workspace", "定位本地工作区", status="completed", detail=str(WORKSPACE_ROOT))
        record_event(
            thread_id, "list:projects", "查看项目目录",
            kind="search", status="in_progress", detail="projects",
        )
        projects = backend.list_files("projects")
        detail = "\n".join(projects) if projects else "projects 目录暂无项目"
        record_event(
            thread_id, "list:projects", "查看项目目录",
            kind="search", status="completed", detail=detail,
        )
        store.finish_open_run_events(thread_id)
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "done", "任务完成", status="completed")
        logger.info("工作区项目查询完成：thread_id=%s projects=%s", thread_id, len(projects))
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed", "projects": projects}
    except Exception as exc:
        # 即使是不调用模型的轻量任务，也要统一更新 Store 状态和 run 结束时间，
        # 否则前端会一直显示 running。
        store.finish_open_run_events(thread_id, status="failed")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("工作区项目查询失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise


def _run_git_with_fetch_head_retry(
    backend: LocalShellBackend,
    command: str,
    *,
    cwd: str,
    timeout: int,
) -> Any:
    """执行 Git 命令，并处理 Windows 下偶发的 FETCH_HEAD 权限异常。

    FETCH_HEAD 是 git fetch/pull 写入的临时状态文件，不是仓库源码。
    如果上一次任务异常中断或 IDE 短暂占用导致 Git 无法打开它，可以删除后重试。

    这个兼容逻辑主要服务 Windows 教学环境。PyCharm、杀毒软件或文件索引服务
    偶尔会短暂占用 `.git/FETCH_HEAD`，直接失败会让"同步代码"体验很差。

    Args:
        backend: 本地 Shell 后端。
        command: 要执行的 Git 命令。
        cwd: 工作目录。
        timeout: 命令超时秒数。

    Returns:
        Git 命令执行结果。
    """

    result = backend.run(command, cwd=cwd, timeout=timeout)
    combined_output = f"{result.stdout}\n{result.stderr}".lower()
    if result.exit_code == 0 or "cannot open .git/fetch_head" not in combined_output:
        return result

    fetch_head = backend.workspace.resolve(Path(cwd) / ".git" / "FETCH_HEAD")
    logger.warning("检测到 FETCH_HEAD 权限异常，准备删除后重试：%s", fetch_head)
    try:
        fetch_head.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("删除 FETCH_HEAD 失败：%s", exc)
        return result
    return backend.run(command, cwd=cwd, timeout=timeout)


def run_pull_only_task(
    *, repo_url: str, prompt: str, thread_id: str | None = None,
) -> dict[str, Any]:
    """执行只拉取远程代码的轻量任务。

    这个分支不调用大模型，也不创建 PR。它只确保仓库在本地工作区存在，
    然后执行 fetch 和 pull，适合用户在前端输入"先把远程代码 pull 一下"的场景。

    由于它不需要推理，所以不走 DeepAgent，不消耗模型调用次数。它仍然会初始化
    仓库映射和仓库记忆，保证后续 planning/coding 任务能复用同一个本地目录。

    Args:
        repo_url: 仓库完整地址。
        prompt: 用户输入文本。
        thread_id: 线程 ID；为 ``None`` 时自动创建。

    Returns:
        包含 thread_id、run_id、status 的结果字典。
    """

    should_record_user_message = thread_id is None
    from agent.runtime.task_router import initialize_task_record

    thread_id = initialize_task_record(
        repo_url=repo_url,
        prompt=prompt,
        thread_id=thread_id,
        record_user_message=should_record_user_message,
    )
    store = get_store()
    store.clear_run_events(thread_id)
    run_id = str(uuid.uuid4())
    store.record_run(run_id=run_id, thread_id=thread_id, status="running")
    repo = parse_repo_url(repo_url)
    from agent.runtime._helpers import resolve_project_local_folder

    local_folder = resolve_project_local_folder(thread_id)
    local_folder_mode = bool(local_folder)
    if local_folder:
        # 本地文件夹模式：文件夹即项目根，仓库目录为 "."，全局工作区只读提供基础设施。
        workspace = Workspace(local_folder)
        backend = LocalShellBackend(
            workspace, local_folder_mode=True, global_workspace_root=WORKSPACE_ROOT,
        )
        project_dir = "."
    else:
        workspace = Workspace(WORKSPACE_ROOT)
        backend = LocalShellBackend(workspace)
        project_dir = repo_project_dir(repo)
    ensure_repo_memory_for_repo(repo, project_dir)
    relative_dir = Path(project_dir)
    target = workspace.resolve(relative_dir)
    clone_url = repo.clone_url

    try:
        logger.info("开始执行 pull-only 任务：thread_id=%s repo=%s/%s", thread_id, repo.owner, repo.repo)
        record_event(thread_id, "workspace", "准备本地工作区", status="in_progress")
        if local_folder_mode:
            target.mkdir(parents=True, exist_ok=True)
        else:
            PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        if target.exists() and (target / ".git").exists():
            # 本地已经存在 Git 仓库时，只更新远程地址并快进拉取 master。
            # 这里使用 --ff-only，避免自动产生 merge commit，保持教学项目历史清晰。
            record_event(thread_id, "workspace", "准备本地工作区", status="completed")
            record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="in_progress")
            remote_result = backend.run(
                f"git remote set-url origin {clone_url}", cwd=str(relative_dir), timeout=60,
            )
            checkout_result = backend.run("git checkout master", cwd=str(relative_dir), timeout=300)
            fetch_result = _run_git_with_fetch_head_retry(
                backend, "git fetch --all", cwd=str(relative_dir), timeout=300,
            )
            pull_result = _run_git_with_fetch_head_retry(
                backend, "git pull origin master --ff-only", cwd=str(relative_dir), timeout=300,
            )
            outputs = [remote_result, checkout_result, fetch_result, pull_result]
        else:
            # 本地还没有对应目录时，直接 clone。
            # 全局模式 clone 到 workspace/projects 下；本地文件夹模式 clone 到文件夹根 "."。
            record_event(thread_id, "workspace", "准备本地工作区", status="completed")
            record_event(thread_id, "sync", "克隆仓库", kind="execute", status="in_progress")
            if local_folder_mode:
                clone_result = backend.run(f"git clone {clone_url} .", cwd=".", timeout=600)
            else:
                clone_result = backend.run(
                    f"git clone {clone_url} {target.name}", cwd="projects", timeout=600,
                )
            outputs = [clone_result]

        failed = next((result for result in outputs if result.exit_code != 0), None)
        if failed is not None:
            # Git 命令的 stdout/stderr 可能包含 token，所以对外展示和日志都要脱敏。
            record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="error")
            raise RuntimeError(f"git pull 失败: {mask_token(failed.stderr or failed.stdout)}")

        record_event(thread_id, "sync", "同步远程仓库", kind="execute", status="completed")
        store.finish_open_run_events(thread_id)
        store.update_thread_status(thread_id, "completed")
        store.record_run(run_id=run_id, thread_id=thread_id, status="completed", finished=True)
        record_event(thread_id, "done", "任务完成", status="completed")
        logger.info("pull-only 任务完成：thread_id=%s repo_dir=%s", thread_id, relative_dir)
        return {"thread_id": thread_id, "run_id": run_id, "status": "completed"}
    except Exception as exc:
        store.finish_open_run_events(thread_id, status="failed")
        store.update_thread_status(thread_id, "failed")
        record_event(thread_id, "failed", "任务失败", status="error", detail=mask_token(str(exc)))
        store.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status="failed",
            error=mask_token(str(exc)),
            finished=True,
        )
        logger.exception("pull-only 任务失败：thread_id=%s run_id=%s error=%s", thread_id, run_id, mask_token(str(exc)))
        raise
