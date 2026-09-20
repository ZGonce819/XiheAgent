"""会话（线程）管理与 SSE 流式对话接口。"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from agent.graph.graph import get_store
from agent.runtime import delete_task, get_task, list_tasks
from agent.runtime.cancellation import request_cancel
from config.app_config import get_settings
from config.approval_config import (
    get_command_approval_preferences,
)
from config.approval_runtime import get_pending_approval, public_approval
from config.automation_config import list_automations
from schemas.request_schema import (
    DashboardEditPreviewRequest,
    DashboardThreadMessageRequest,
    DashboardThreadTitleUpdateRequest,
    DashboardViewSnapshotRequest,
)
from services.approval_explanation import explain_pending_approval
from services.repo_rollback import rollback_run
from services.streaming_service import build_streaming_response
from services.thread_service import (
    conversation_snapshot,
    fetch_visible_messages,
    thread_payload,
)
from utils.platform_utils import normalize_repo_url
from utils.redis_utils import cleanup_stream

router = APIRouter(prefix="/threads")
logger = logging.getLogger(__name__)


def _is_latest_user_message(message_id: str, thread_id: str, messages: list[dict[str, Any]], original_content: str | None = None) -> bool:
    """校验消息确实是当前线程最后一条用户消息。"""
    user_messages = [message for message in messages if message.get("role") == "user"]
    if not user_messages:
        return False
    latest_id = str(user_messages[-1].get("id") or "")
    if latest_id == message_id:
        return True
    if message_id.startswith(f"{thread_id}-user-") and original_content is not None:
        return str(user_messages[-1].get("content") or "").strip() == original_content.strip()
    return False


@router.get("")
def dashboard_threads(limit: int = 50) -> list[dict[str, Any]]:
    """返回会话列表。"""
    active_automation_defs = list_automations()
    active_automations = {
        (str(item.get("repo") or ""), str(item.get("prompt") or ""))
        for item in active_automation_defs
    }
    active_thread_ids = {
        str(item.get("lastThreadId"))
        for item in active_automation_defs
        if item.get("lastThreadId")
    }
    visible_threads = []
    for thread in list_tasks(limit=limit):
        if thread.get("source") == "schedule":
            signature = (
                str(thread.get("repo_url") or ""),
                str(thread.get("user_prompt") or ""),
            )
            if (
                str(thread.get("thread_id") or "") not in active_thread_ids
                and signature not in active_automations
            ):
                continue
        visible_threads.append(thread)
    return [thread_payload(thread) for thread in visible_threads]


@router.get("/{thread_id}")
def dashboard_thread_detail(thread_id: str) -> dict[str, Any]:
    """返回单个会话详情（含消息历史）。"""
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread_payload(task, include_versions=True)


@router.get("/{thread_id}/approval")
def dashboard_pending_approval(thread_id: str) -> dict[str, Any] | None:
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    return public_approval(get_pending_approval(thread_id))


@router.post("/{thread_id}/approval/explanation")
async def dashboard_explain_approval(
    thread_id: str, body: dict[str, Any]
) -> dict[str, str]:
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    pending = get_pending_approval(thread_id)
    if pending is None:
        raise HTTPException(status_code=409, detail="当前会话没有待审批的操作")
    approval_id = str(body.get("approval_id") or "")
    if approval_id != str(pending.get("id") or ""):
        raise HTTPException(status_code=409, detail="待审批操作已变化，请刷新页面后重试")
    try:
        explanation = await asyncio.to_thread(explain_pending_approval, pending)
    except Exception as exc:
        logger.warning(
            "Failed to explain pending approval for thread %s (%s)",
            thread_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=503, detail="暂时无法生成解释，请稍后重试"
        ) from exc
    return {"explanation": explanation}


@router.post("/{thread_id}/approval")
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


@router.post("/stream-message")
async def dashboard_stream_new_message(body: DashboardThreadMessageRequest) -> StreamingResponse:
    """创建新会话并开始流式对话。"""
    try:
        repo_url = normalize_repo_url(body.repo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    requested_backend = body.backend_type or ("sandbox" if get_settings().use_sandbox else "local")
    if requested_backend == "sandbox" and not get_settings().use_sandbox:
        raise HTTPException(status_code=400, detail="您未开启沙盒配置，请开启后再尝试")
    local_work_dir: str | None = None
    if body.local_work_dir and body.local_work_dir.strip():
        if requested_backend != "local":
            raise HTTPException(status_code=400, detail="沙箱对话不能使用本机工作目录")
        work_dir = Path(body.local_work_dir.strip()).resolve()
        if not work_dir.exists() or not work_dir.is_dir():
            raise HTTPException(
                status_code=400,
                detail=f"当前对话的工作目录不存在或不是文件夹：{work_dir}",
            )
        local_work_dir = str(work_dir)
    thread_id = str(uuid.uuid4())
    return build_streaming_response(
        thread_id=thread_id,
        repo_url=repo_url,
        content=body.content,
        model_id=body.model_id,
        backend_type=requested_backend,
        project_name=body.project_name,
        local_work_dir=local_work_dir,
    )


@router.post("/{thread_id}/stream-message")
async def dashboard_stream_existing_message(
    thread_id: str,
    body: DashboardThreadMessageRequest,
) -> StreamingResponse:
    """向已有会话追加对话轮次。"""
    task = get_task(thread_id)
    requested_backend = body.backend_type or ("sandbox" if get_settings().use_sandbox else "local")
    if requested_backend == "sandbox" and not get_settings().use_sandbox:
        raise HTTPException(status_code=400, detail="您未开启沙盒配置，请开启后再尝试")
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
                rolled_back = rollback_run(repo_url, latest_run, get_store())
            except RuntimeError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            base = latest_run.get("base_commit")
            head = latest_run.get("head_commit")
            if not rolled_back and base and head and base != head:
                raise HTTPException(
                    status_code=409,
                    detail="代码回退未生效：运行记录的版本信息不完整，请手动检查仓库状态",
                )
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
            project_name=body.project_name,
            local_work_dir=body.local_work_dir,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/{thread_id}/edit-preview")
def dashboard_edit_preview(thread_id: str, body: DashboardEditPreviewRequest) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    messages = fetch_visible_messages(thread_id)
    if not _is_latest_user_message(body.message_id, thread_id, messages, body.original_content):
        raise HTTPException(status_code=409, detail="只能编辑最近一条用户消息")
    index = next((i for i, message in enumerate(messages) if message.get("id") == body.message_id), len(messages) - 1)
    latest_run = get_store().get_latest_run(thread_id)
    committed_changes = bool(
        latest_run
        and latest_run.get("base_commit")
        and latest_run.get("head_commit")
        and latest_run["base_commit"] != latest_run["head_commit"]
    )
    uncommitted_changes = any(
        event.get("kind") in {"edit", "delete", "move"}
        for event in (task.get("run_events") or [])
    ) and not committed_changes
    return {
        "message_id": body.message_id,
        "messages_to_replace": max(0, len(messages) - index),
        "runs_to_replace": 1 if latest_run else 0,
        "code_changes_detected": committed_changes or uncommitted_changes,
        "requires_rollback": bool(task.get("run_events") or len(messages) > index + 1),
    }


@router.put("/{thread_id}/view-snapshot", status_code=204)
def dashboard_save_view_snapshot(thread_id: str, body: DashboardViewSnapshotRequest) -> None:
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    get_store().save_conversation_view(thread_id, body.messages)
    return None


@router.patch("/{thread_id}/title")
def dashboard_update_thread_title(thread_id: str, body: DashboardThreadTitleUpdateRequest) -> dict[str, Any]:
    task = get_task(thread_id)
    if task is None:
        raise HTTPException(status_code=404, detail="thread not found")
    updated = get_store().update_thread_title(thread_id, body.title)
    if not updated:
        raise HTTPException(status_code=404, detail="thread not found")
    return thread_payload(get_task(thread_id))


@router.post("/{thread_id}/cancel")
def dashboard_cancel_thread(thread_id: str) -> dict[str, Any]:
    """停止该会话当前正在运行的一轮。"""
    if get_task(thread_id) is None:
        raise HTTPException(status_code=404, detail="thread not found")
    if not request_cancel(thread_id):
        raise HTTPException(status_code=409, detail="当前没有正在运行的任务")
    return thread_payload(get_task(thread_id))


@router.delete("/{thread_id}", status_code=204)
def dashboard_delete_thread(thread_id: str) -> None:
    """删除指定会话及其关联数据。"""
    deleted = delete_task(thread_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="thread not found")
    if get_settings().use_redis_stream:
        cleanup_stream(thread_id)
    return None
