"""Dashboard 前端接口。

提供会话管理、SSE 流式输出等 Dashboard 所需的 HTTP 接口。
前端通过 POST SSE 实时接收 Agent 运行事件和流式正文。
"""

from __future__ import annotations

import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from fastapi.responses import StreamingResponse

from agent.graph.graph import get_store
from agent.runtime import delete_task, get_task, list_tasks
from config.app_config import get_settings
from config.approval_config import (
    get_command_approval_preferences,
    update_command_approval_preferences,
)
from config.approval_runtime import get_pending_approval, public_approval
from config.automation_config import (
    create_automation,
    delete_automation,
    list_automations,
    update_automation,
)
from config.env_config import get_env
from config.model_config import (
    create_custom_model,
    delete_custom_model,
    get_builtin_model,
    get_custom_model,
    list_custom_models,
    update_builtin_model,
    update_custom_model,
)
from schemas.request_schema import (
    DashboardEditPreviewRequest,
    DashboardThreadMessageRequest,
    DashboardThreadTitleUpdateRequest,
    DashboardViewSnapshotRequest,
)
from services.repo_rollback import rollback_run
from services.streaming_service import build_streaming_response
from services.thread_service import (
    conversation_snapshot,
    fetch_visible_messages,
    thread_payload,
)
from utils.platform_utils import normalize_repo_url
from utils.redis_utils import cleanup_stream

dashboard_router = APIRouter(prefix="/dashboard/api")


def _validate_automation_payload(body: dict[str, Any], partial: bool = False) -> None:
    for field, label in (("name", "名称"), ("prompt", "任务指令"), ("schedule", "执行计划")):
        if (not partial or field in body) and not str(body.get(field) or "").strip():
            raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if "schedule" in body and len(str(body["schedule"]).split()) != 5:
        raise HTTPException(status_code=400, detail="执行计划必须是 5 段 Cron 表达式")


def _skill_summary(skill_file: Path) -> dict[str, Any]:
    text = skill_file.read_text(encoding="utf-8", errors="replace")
    name = skill_file.parent.name
    description = ""
    if text.startswith("---"):
        frontmatter = text.split("---", 2)[1]
        name_match = re.search(r"^name:\s*[\"']?(.+?)[\"']?\s*$", frontmatter, re.MULTILINE)
        description_match = re.search(
            r"^description:\s*[\"']?(.+?)[\"']?\s*$", frontmatter, re.MULTILINE
        )
        if name_match:
            name = name_match.group(1).strip()
        if description_match:
            description = description_match.group(1).strip()
    if not description:
        heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        description = heading.group(1).strip() if heading else "可由智能体按需加载的本地技能。"
    return {
        "id": skill_file.parent.name,
        "name": name,
        "description": description,
        "virtualPath": f"/skills/{skill_file.parent.name}/SKILL.md",
        "updatedAt": datetime.fromtimestamp(
            skill_file.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
    }


def _is_latest_user_message(message_id: str, thread_id: str, messages: list[dict[str, Any]], original_content: str | None = None) -> bool:
    """校验消息确实是当前线程最后一条用户消息。

    实时 SSE 消息使用 ``<thread>-user-<run>`` ID，历史 checkpoint 可能使用
    LangChain 原始 UUID。两种 ID 都只允许匹配最后一条用户消息；线程前缀
    只是兼容实时消息，不会放宽到任意旧消息。
    """
    user_messages = [message for message in messages if message.get("role") == "user"]
    if not user_messages:
        return False
    latest_id = str(user_messages[-1].get("id") or "")
    if latest_id == message_id:
        return True
    if message_id.startswith(f"{thread_id}-user-") and original_content is not None:
        return str(user_messages[-1].get("content") or "").strip() == original_content.strip()
    return False


@dashboard_router.get("/me")
def dashboard_me() -> dict[str, Any]:
    """返回当前用户信息。

    Returns:
        包含登录名、邮箱、头像和管理员状态的字典。
    """
    return {
        "login": "XiheAgent",
        "email": None,
        "avatar_url": None,
        "is_admin": True,
        "slack_oauth_enabled": False,
    }


@dashboard_router.get("/options")
def dashboard_options() -> dict[str, Any]:
    """返回页面选项（模型列表等）。

    合并 .env 默认模型和自定义模型列表。
    """
    custom_models = list_custom_models()
    builtin = get_builtin_model()

    models: list[dict[str, Any]] = [
        {
            "id": "__builtin__",
            "label": f"{builtin['name']} (内置)",
            "efforts": ["default"],
            "default_effort": "default",
            "supports_images": False,
        }
    ]
    for cm in custom_models:
        models.append({
            "id": cm["id"],
            "label": f"{cm['name']} ({cm['model_name']})",
            "efforts": ["default"],
            "default_effort": "default",
            "supports_images": False,
        })

    default_model = "__builtin__"
    if custom_models:
        default_custom = next((m for m in custom_models if m.get("is_default")), None)
        if default_custom:
            default_model = default_custom["id"]

    return {
        "models": models,
        "default_agent_model": default_model,
        "default_agent_reasoning_effort": "default",
        "default_agent_subagent_model": default_model,
        "default_agent_subagent_reasoning_effort": "default",
    }


@dashboard_router.get("/custom-models")
def dashboard_list_custom_models() -> list[dict[str, Any]]:
    """返回所有自定义模型配置列表。"""
    return list_custom_models()


@dashboard_router.post("/custom-models", status_code=201)
def dashboard_create_custom_model(body: dict[str, Any]) -> dict[str, Any]:
    """新增自定义模型。"""
    if not body.get("name") or not body.get("api_base") or not body.get("model_name"):
        raise HTTPException(status_code=400, detail="名称、API 地址和模型名不能为空")
    return create_custom_model(body)


@dashboard_router.put("/custom-models/{model_id}")
def dashboard_update_custom_model(model_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """更新自定义模型配置。"""
    existing = get_custom_model(model_id)
    if not existing:
        raise HTTPException(status_code=404, detail="模型不存在")
    result = update_custom_model(model_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    return result


@dashboard_router.delete("/custom-models/{model_id}", status_code=204)
def dashboard_delete_custom_model(model_id: str) -> None:
    """删除自定义模型。"""
    if not delete_custom_model(model_id):
        raise HTTPException(status_code=404, detail="模型不存在")


@dashboard_router.get("/builtin-model")
def dashboard_get_builtin_model() -> dict[str, Any]:
    """返回内置模型配置（API Key 脱敏）。"""
    return get_builtin_model()


@dashboard_router.put("/builtin-model")
def dashboard_update_builtin_model(body: dict[str, Any]) -> dict[str, Any]:
    """更新内置模型的用户覆盖配置。"""
    return update_builtin_model(body)


@dashboard_router.get("/profile")
def dashboard_profile() -> dict[str, Any]:
    """返回当前用户的默认工作台配置。

    配置存储尚未启用时返回稳定的环境默认值，确保前端首屏不会因
    可选配置缺失而失败。
    """
    model = get_env("MAIN_MODEL", "deepseek-v4-pro")
    return {
        "default_model": model,
        "reasoning_effort": "default",
        "default_subagent_model": model,
        "subagent_reasoning_effort": "default",
        "default_repo": None,
        "base_branch": "main",
        "branch_prefix": "codex/",
        "auto_fix_ci": False,
        "create_prs": True,
        "review_draft_prs": None,
        "backendType": "sandbox" if get_settings().use_sandbox else "local",
        "sandbox_available": get_settings().use_sandbox,
    }


@dashboard_router.get("/command-approval-settings")
def dashboard_command_approval_settings() -> dict[str, Any]:
    return get_command_approval_preferences()


@dashboard_router.put("/command-approval-settings")
def dashboard_update_command_approval_settings(body: dict[str, Any]) -> dict[str, Any]:
    try:
        return update_command_approval_preferences(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@dashboard_router.get("/repos")
def dashboard_repos() -> dict[str, Any]:
    """返回可用代码仓库列表。"""
    return {"installations": [], "repositories": []}


@dashboard_router.get("/my-mapping")
def dashboard_my_mapping() -> dict[str, Any]:
    """返回当前用户的外部账号映射；未配置时返回空对象。"""
    return {}


@dashboard_router.get("/schedules")
def dashboard_schedules() -> list[dict[str, Any]]:
    """Return persisted automation definitions."""
    return list_automations()


@dashboard_router.post("/schedules", status_code=201)
def dashboard_create_schedule(body: dict[str, Any]) -> dict[str, Any]:
    """Create an automation definition."""
    _validate_automation_payload(body)
    return create_automation(body)


@dashboard_router.patch("/schedules/{schedule_id}")
def dashboard_update_schedule(
    schedule_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Update an automation definition."""
    _validate_automation_payload(body, partial=True)
    result = update_automation(schedule_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return result


@dashboard_router.delete("/schedules/{schedule_id}", status_code=204)
def dashboard_delete_schedule(schedule_id: str) -> None:
    """Delete an automation definition."""
    if not delete_automation(schedule_id):
        raise HTTPException(status_code=404, detail="自动化任务不存在")


@dashboard_router.get("/skills")
def dashboard_skills() -> dict[str, Any]:
    """Discover readable DeepAgents skills from the configured workspace."""
    skills_dir = get_settings().skills_dir
    skills: list[dict[str, Any]] = []
    if skills_dir.is_dir():
        for skill_file in sorted(skills_dir.glob("*/SKILL.md")):
            try:
                skills.append(_skill_summary(skill_file))
            except OSError:
                continue
    return {
        "items": skills,
        "virtualRoot": "/skills",
        "available": skills_dir.is_dir(),
    }


@dashboard_router.get("/threads")
def dashboard_threads(limit: int = 50) -> list[dict[str, Any]]:
    """返回会话列表。

    Args:
        limit: 最大返回会话数，默认 50。

    Returns:
        会话摘要字典列表。
    """
    return [thread_payload(thread) for thread in list_tasks(limit=limit)]


@dashboard_router.get("/threads/{thread_id}")
def dashboard_thread_detail(thread_id: str) -> dict[str, Any]:
    """返回单个会话详情（含消息历史）。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        会话详情字典。

    Raises:
        HTTPException: 会话不存在时返回 404。
    """
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread_payload(task, include_versions=True)


@dashboard_router.get("/threads/{thread_id}/approval")
def dashboard_pending_approval(thread_id: str) -> dict[str, Any] | None:
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return public_approval(get_pending_approval(thread_id))


@dashboard_router.post("/threads/{thread_id}/approval")
async def dashboard_decide_approval(
    thread_id: str, body: dict[str, Any]
) -> StreamingResponse:
    task = get_task(thread_id)
    pending = get_pending_approval(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    if pending is None:
        raise HTTPException(status_code=409, detail="当前会话没有待审批的操作")
    decision = str(body.get("decision") or "")
    approval_id = str(body.get("approval_id") or "")
    if decision not in {"approve", "reject"}:
        raise HTTPException(status_code=400, detail="无效的审批决定")
    if approval_id != str(pending.get("id") or ""):
        raise HTTPException(status_code=409, detail="待审批操作已变化，请刷新页面后重试")
    return build_streaming_response(
        thread_id=thread_id,
        repo_url=str(task.get("repo_url") or ""),
        content="",
        backend_type=str(task.get("backend_type") or "local"),
        approval_decision=decision,
        approval_id=approval_id,
    )


@dashboard_router.post("/threads/stream-message")
async def dashboard_stream_new_message(body: DashboardThreadMessageRequest) -> StreamingResponse:
    """创建新会话并开始流式对话。

    Args:
        body: 请求体，包含仓库地址和首条消息内容。

    Returns:
        SSE 流式响应。

    Raises:
        HTTPException: 仓库地址解析失败时返回 400。
    """
    try:
        repo_url = normalize_repo_url(body.repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # 校验 backend_type：请求沙箱但全局未开启时拒绝
    requested_backend = body.backend_type or ("sandbox" if get_settings().use_sandbox else "local")
    if requested_backend == "sandbox" and not get_settings().use_sandbox:
        raise HTTPException(status_code=400, detail="您未开启沙盒配置，请开启后再尝试")
    thread_id = str(uuid.uuid4())
    return build_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content, model_id=body.model_id, backend_type=requested_backend)


@dashboard_router.post("/threads/{thread_id}/stream-message")
async def dashboard_stream_existing_message(
    thread_id: str,
    body: DashboardThreadMessageRequest,
) -> StreamingResponse:
    """向已有会话追加对话轮次。

    Args:
        thread_id: 目标会话唯一标识。
        body: 请求体，包含仓库地址和消息内容。

    Returns:
        SSE 流式响应。

    Raises:
        HTTPException: 仓库地址解析失败时返回 400。
    """
    task = get_task(thread_id)
    # 校验 backend_type：请求沙箱但全局未开启时拒绝
    requested_backend = body.backend_type or ("sandbox" if get_settings().use_sandbox else "local")
    if requested_backend == "sandbox" and not get_settings().use_sandbox:
        raise HTTPException(status_code=400, detail="您未开启沙盒配置，请开启后再尝试")
    # 检查环境匹配：不同 backend 创建的对话不能继续
    if task is not None:
        thread_backend = task.get("backend_type") or "local"
        if thread_backend != requested_backend:
            env_label = "沙箱" if thread_backend == "sandbox" else "本地"
            raise HTTPException(
                status_code=409,
                detail=f"此对话在{env_label}环境中创建，请切换到{env_label}模式后继续",
            )
    try:
        repo_url = normalize_repo_url(body.repo, fallback=(task or {}).get("repo_url"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if body.edit_message_id:
        if not body.rollback_confirmed:
            raise HTTPException(status_code=400, detail="请先确认回退影响")
        messages = fetch_visible_messages(thread_id)
        if not _is_latest_user_message(body.edit_message_id, thread_id, messages, body.edit_message_content):
            raise HTTPException(status_code=409, detail="只能编辑最近一条用户消息")
        latest_run = get_store().get_latest_run(thread_id)
        if latest_run:
            try:
                rollback_run(repo_url, latest_run, get_store())
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
        snapshot = conversation_snapshot(task, edited_message_id=body.edit_message_id)
        if body.edit_snapshot:
            snapshot["messages"] = body.edit_snapshot
        get_store().add_conversation_version(thread_id, snapshot)
    try:
        return build_streaming_response(
            thread_id=thread_id,
            repo_url=repo_url,
            content=body.content,
            rollback_message_id=body.edit_message_id,
            rollback_message_content=body.edit_message_content,
            model_id=body.model_id,
            backend_type=requested_backend,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@dashboard_router.post("/threads/{thread_id}/edit-preview")
def dashboard_edit_preview(thread_id: str, body: DashboardEditPreviewRequest) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    messages = fetch_visible_messages(thread_id)
    if not _is_latest_user_message(body.message_id, thread_id, messages, body.original_content):
        raise HTTPException(status_code=409, detail="只能编辑最近一条用户消息")
    index = next((i for i, message in enumerate(messages) if message.get("id") == body.message_id), len(messages) - 1)
    return {
        "message_id": body.message_id,
        "messages_to_replace": max(0, len(messages) - index),
        "runs_to_replace": 1 if task.get("latest_run") else 0,
        "code_changes_detected": any(
            event.get("kind") in {"edit", "delete", "move"}
            for event in (task.get("run_events") or [])
        ),
        "requires_rollback": bool(task.get("run_events") or len(messages) > index + 1),
    }


@dashboard_router.put("/threads/{thread_id}/view-snapshot", status_code=204)
def dashboard_save_view_snapshot(thread_id: str, body: DashboardViewSnapshotRequest) -> None:
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    get_store().save_conversation_view(thread_id, body.messages)
    return None


@dashboard_router.patch("/threads/{thread_id}/title")
def dashboard_update_thread_title(thread_id: str, body: DashboardThreadTitleUpdateRequest) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    updated = get_store().update_thread_title(thread_id, body.title)
    if not updated:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread_payload(get_task(thread_id))


@dashboard_router.delete("/threads/{thread_id}", status_code=204)
def dashboard_delete_thread(thread_id: str) -> None:
    """删除指定会话及其关联数据。

    Args:
        thread_id: 待删除会话的唯一标识。

    Raises:
        HTTPException: 会话不存在时返回 404。
    """
    deleted = delete_task(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    if get_settings().use_redis_stream:
        cleanup_stream(thread_id)
    return None


# ── 工作目录配置 ────────────────────────────────────────────────────────────


class _WorkspaceUpdateRequest(BaseModel):
    workspace_root: str | None = None
    projects_dir: str | None = None
    skills_dir: str | None = None


def _ensure_writable(path: Path, label: str) -> None:
    """确保目录存在且可写，不存在时自动创建。

    Args:
        path: 要验证的路径。
        label: 用于错误提示的目录名称。

    Raises:
        HTTPException: 目录无法创建或不可写时抛出 400。
    """
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法创建{label}：{e}")
    try:
        test = path / ".xihe_write_test"
        test.touch()
        test.unlink()
    except OSError:
        raise HTTPException(status_code=400, detail=f"{label}不可写：{path}")


@dashboard_router.get("/workspace")
def dashboard_get_workspace() -> dict[str, str]:
    """返回当前工作目录配置。"""
    settings = get_settings()
    return {
        "workspace_root": str(settings.workspace_root),
        "projects_dir": str(settings.projects_dir),
        "skills_dir": str(settings.skills_dir),
    }


@dashboard_router.put("/workspace")
def dashboard_update_workspace(body: _WorkspaceUpdateRequest) -> dict[str, str]:
    """更新工作目录配置。

    支持单独更新 workspace_root、projects_dir、skills_dir 中的任意组合。

    Raises:
        HTTPException: 路径无效或不可写时返回 400。
    """
    settings = get_settings()

    if body.workspace_root is not None:
        new_path = Path(body.workspace_root).resolve()
        _ensure_writable(new_path, "工作目录")
        settings.workspace_root = new_path

    if body.projects_dir is not None:
        new_projects = Path(body.projects_dir).resolve()
        _ensure_writable(new_projects, "项目目录")
        settings.projects_dir = new_projects

    if body.skills_dir is not None:
        new_skills = Path(body.skills_dir).resolve()
        _ensure_writable(new_skills, "技能目录")
        settings.skills_dir = new_skills

    return {
        "workspace_root": str(settings.workspace_root),
        "projects_dir": str(settings.projects_dir),
        "skills_dir": str(settings.skills_dir),
    }


@dashboard_router.get("/workspace/pick-folder")
def dashboard_pick_folder() -> dict[str, Any]:
    """打开系统原生文件夹选择对话框。

    使用 tkinter 的 filedialog 弹出目录选择窗口。由于 tkinter 必须在
    独立线程中创建自己的 Tk 根窗口，这里通过守护线程启动并等待结果。

    Returns:
        dict: 包含 ``path`` 字段（用户选择的路径），取消时返回 ``null``。

    Raises:
        HTTPException: 对话框不可用或超时时返回错误。
    """
    result: dict[str, str | None] = {"path": None}
    error: Exception | None = None
    done = threading.Event()

    def _pick() -> None:
        nonlocal error
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title="选择工作目录", parent=root)
            result["path"] = path if path else None
            root.destroy()
        except Exception as e:
            error = e
        finally:
            done.set()

    thread = threading.Thread(target=_pick, daemon=True)
    thread.start()
    done.wait(timeout=120)

    if error is not None:
        raise HTTPException(status_code=500, detail=f"文件夹对话框失败：{error}")
    if not done.is_set():
        raise HTTPException(status_code=504, detail="文件夹选择超时")

    return result
