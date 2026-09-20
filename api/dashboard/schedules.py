"""自动化任务（定时调度）相关接口。"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, BackgroundTasks, HTTPException

from agent.graph.graph import get_store
from agent.runtime import delete_task
from agent.runtime.task_router import initialize_task_record
from config.automation_config import (
    create_automation,
    delete_automation,
    get_automation,
    list_automations,
    mark_automation_run,
    mark_automation_triggered,
    update_automation,
)
from config.automation_state import (
    clear_stopped,
    clear_waiting_approval,
    delete_state,
    load_state,
    render_state,
    reset_state,
    set_budget,
    stop_decision,
)
from services.automation_guard import task_guard
from services.automation_loop import run_cycle
from services.automation_runner import (
    loop_scope_refusal,
    make_agent_round_runner,
    resolve_backend_type,
)
from services.automation_verification import parse_checks
from utils.platform_utils import UnsupportedPlatformError, normalize_repo_url

router = APIRouter(prefix="/schedules")
logger = logging.getLogger(__name__)


def _validate_automation_payload(body: dict[str, Any], partial: bool = False) -> None:
    """校验自动化任务请求体的必填字段和格式。

    Args:
        body: 请求体字典。
        partial: 是否为部分更新；为 ``True`` 时只校验已提供的字段。

    Raises:
        HTTPException: 当必填字段为空或格式不正确时。
    """
    for field, label in (("name", "名称"), ("prompt", "任务指令"), ("schedule", "执行计划")):
        if (not partial or field in body) and not str(body.get(field) or "").strip():
            raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if "schedule" in body and len(str(body["schedule"]).split()) != 5:
        raise HTTPException(status_code=400, detail="执行计划必须是 5 段 Cron 表达式")
    if body.get("checks") is not None and not isinstance(body["checks"], list):
        raise HTTPException(status_code=400, detail="检查项必须是列表")
    if body.get("autoApprove") is not None and not isinstance(body["autoApprove"], bool):
        raise HTTPException(status_code=400, detail="审批表决开关必须是布尔值")


def _normalize_schedule_repo(body: dict[str, Any]) -> dict[str, Any]:
    """规范化自动化任务仓库字段，兼容 ``owner/repo`` 简写。

    Args:
        body: 包含 ``repo`` 字段的请求体。

    Returns:
        仓库地址已规范化的请求体副本。

    Raises:
        HTTPException: 仓库地址格式非法时。
    """
    normalized = dict(body)
    raw = normalized.get("repo")
    if not raw:
        return normalized
    text = str(raw).strip()
    try:
        normalized["repo"] = normalize_repo_url(text)
    except ValueError:
        if not text.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="仓库地址格式不正确，请输入完整仓库 URL 或 owner/repo。",
            )
        normalized["repo"] = text
    return normalized


def _schedule_state_payload(schedule_id: str) -> dict[str, Any]:
    """拼出循环状态的响应体。

    Args:
        schedule_id: 自动化任务 ID。

    Returns:
        包含 state、text、stopped、stopReason、stopSource 的响应字典。
    """
    source, reason = stop_decision(schedule_id)
    return {
        "state": load_state(schedule_id),
        "text": render_state(schedule_id),
        "stopped": source is not None,
        "stopReason": reason,
        "stopSource": source,
    }


@router.get("")
def dashboard_schedules() -> list[dict[str, Any]]:
    """返回所有已持久化的自动化任务定义。

    Returns:
        自动化任务定义列表。
    """
    return list_automations()


@router.post("", status_code=201)
def dashboard_create_schedule(body: dict[str, Any]) -> dict[str, Any]:
    """创建一条自动化任务定义。

    Args:
        body: 包含任务名称、指令、执行计划等字段的请求体。

    Returns:
        新创建的自动化任务定义字典。

    Raises:
        HTTPException: 必填字段缺失或格式有误时。
    """
    _validate_automation_payload(body)
    return create_automation(_normalize_schedule_repo(body))


@router.patch("/{schedule_id}")
def dashboard_update_schedule(
    schedule_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """更新一条自动化任务定义。

    Args:
        schedule_id: 自动化任务 ID。
        body: 需要更新的字段字典，仅校验已提供的字段。

    Returns:
        更新后的自动化任务定义字典。

    Raises:
        HTTPException: 任务不存在或字段格式有误时。
    """
    _validate_automation_payload(body, partial=True)
    result = update_automation(schedule_id, _normalize_schedule_repo(body))
    if result is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return result


@router.delete("/{schedule_id}", status_code=204)
def dashboard_delete_schedule(schedule_id: str) -> None:
    """删除一个自动化任务，连同它的循环状态文件。

    同时清理该任务关联的所有会话线程。

    Args:
        schedule_id: 自动化任务 ID。

    Raises:
        HTTPException: 任务不存在时。
    """
    automation = get_automation(schedule_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    thread_ids = set()
    if automation.get("lastThreadId"):
        thread_ids.add(str(automation["lastThreadId"]))
    thread_ids.update(
        get_store().list_thread_ids_by_source(
            "schedule",
            repo_url=str(automation.get("repo") or "") or None,
            user_prompt=str(automation.get("prompt") or "") or None,
        )
    )
    if not delete_automation(schedule_id):
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    try:
        delete_state(schedule_id)
    except OSError:
        logger.warning("删除自动化任务的状态文件失败：%s", schedule_id, exc_info=True)
    for thread_id in thread_ids:
        try:
            delete_task(thread_id)
        except Exception:
            logger.warning("删除自动化任务关联会话失败：%s", thread_id, exc_info=True)


@router.get("/{schedule_id}/state")
def dashboard_schedule_state(schedule_id: str) -> dict[str, Any]:
    """返回某个自动化任务的循环状态。

    Args:
        schedule_id: 自动化任务 ID。

    Returns:
        包含 state、text、stopped、stopReason、stopSource 的响应字典。

    Raises:
        HTTPException: 任务不存在时。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return _schedule_state_payload(schedule_id)


@router.post("/{schedule_id}/resume")
def dashboard_resume_schedule(schedule_id: str) -> dict[str, Any]:
    """让一个被停下的循环重新可以触发。

    支持清除手动停止标记和等待审批状态；若循环因预算超限而停止，
    需调整预算上限后方可恢复。

    Args:
        schedule_id: 自动化任务 ID。

    Returns:
        更新后的循环状态字典。

    Raises:
        HTTPException: 任务不存在或无法直接恢复时。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")

    source, reason = stop_decision(schedule_id)
    if source == "marked":
        clear_stopped(schedule_id)
    elif source == "awaiting_approval":
        clear_waiting_approval(schedule_id)
    elif source is not None:
        raise HTTPException(
            status_code=409,
            detail=(
                f"这个循环不是被手动停的，清掉标记也不会恢复：{reason}。"
                "请调高对应的预算上限。"
            ),
        )

    return _schedule_state_payload(schedule_id)


@router.post("/{schedule_id}/reset")
def dashboard_reset_schedule(schedule_id: str) -> dict[str, Any]:
    """清空一个自动化任务的循环历史，保留任务本身。

    Args:
        schedule_id: 自动化任务 ID。

    Returns:
        重置后的循环状态字典。

    Raises:
        HTTPException: 任务不存在时。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    reset_state(schedule_id)
    return _schedule_state_payload(schedule_id)


@router.patch("/{schedule_id}/budget")
def dashboard_update_schedule_budget(
    schedule_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """更新自动化任务的预算上限。

    至少需要提供 ``maxRuns``、``maxTokens``、``maxConsecutiveFailures``
    中的一个字段。

    Args:
        schedule_id: 自动化任务 ID。
        body: 包含预算上限字段的请求体。

    Returns:
        更新后的循环状态字典。

    Raises:
        HTTPException: 任务不存在、参数缺失或值非法时。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")

    fields = {
        "maxRuns": "max_runs",
        "maxTokens": "max_tokens",
        "maxConsecutiveFailures": "max_consecutive_failures",
    }
    provided = [key for key in fields if key in body]
    if not provided:
        raise HTTPException(
            status_code=400,
            detail="至少要给一个上限：maxRuns / maxTokens / maxConsecutiveFailures",
        )

    values: dict[str, int] = {}
    for key in provided:
        value = body[key]
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise HTTPException(status_code=400, detail=f"{key} 必须是整数")
        try:
            number = int(value)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"{key} 必须是整数") from exc
        if number < 0:
            raise HTTPException(status_code=400, detail=f"{key} 不能为负数")
        values[fields[key]] = number

    set_budget(schedule_id, **values)
    return _schedule_state_payload(schedule_id)


@router.post("/{schedule_id}/run", status_code=202)
def dashboard_run_schedule(
    schedule_id: str, background: BackgroundTasks
) -> dict[str, Any]:
    """手动触发一轮循环执行。

    在后台线程中异步执行一轮自动化循环，立即返回接受确认。

    Args:
        schedule_id: 自动化任务 ID。
        background: FastAPI 后台任务管理器（未实际使用，改用手动线程）。

    Returns:
        包含 ``automationId``、``accepted``、``threadId``、``roundNumber`` 的响应字典。

    Raises:
        HTTPException: 任务不存在、已停止、缺少仓库配置或已有运行中的轮次时。
    """
    automation = get_automation(schedule_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")

    stop_source, reason = stop_decision(schedule_id)
    if stop_source is not None:
        raise HTTPException(status_code=409, detail=f"这个循环已经停了：{reason}")

    try:
        checks = parse_checks(automation.get("checks"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"检查项配置有误：{exc}") from exc

    refusal = loop_scope_refusal(
        str(automation.get("prompt") or ""),
        has_checks=bool(checks),
        auto_approve=automation.get("autoApprove") is True,
    )
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)
    if not automation.get("repo"):
        raise HTTPException(status_code=400, detail="自动化任务没有配置代码仓库")

    guard = task_guard()
    run_token = guard.acquire(schedule_id)
    if run_token is None:
        raise HTTPException(
            status_code=409,
            detail="这个任务已经有一轮在跑（也可能是上一次运行没能正常收尾），等它结束再触发",
        )

    environment = resolve_backend_type()
    try:
        thread_id = str(uuid.uuid4())
        mark_automation_triggered(schedule_id, thread_id=thread_id)
        repo_url = str(automation["repo"])
        try:
            normalized_repo_url = normalize_repo_url(repo_url)
        except ValueError:
            normalized_repo_url = repo_url
        if normalized_repo_url != repo_url:
            repo_url = normalized_repo_url
            automation = {**automation, "repo": repo_url}
            update_automation(schedule_id, {"repo": repo_url})
        prompt = str(automation.get("prompt") or "")
        try:
            initialize_task_record(
                repo_url=repo_url,
                prompt=prompt,
                thread_id=thread_id,
                backend_type=environment,
                source="schedule",
            )
        except UnsupportedPlatformError:
            parts = [part for part in urlsplit(repo_url).path.split("/") if part]
            owner = parts[-2] if len(parts) >= 2 else ""
            repo_name = parts[-1].removesuffix(".git") if parts else ""
            get_store().upsert_thread(
                thread_id=thread_id,
                title=prompt[:80] or repo_name,
                user_prompt=prompt,
                repo_url=repo_url,
                repo_owner=owner,
                repo_name=repo_name,
                latest_run_status="running",
                backend_type=environment,
                source="schedule",
            )
        round_number = int(load_state(schedule_id)["budget"]["runsUsed"]) + 1
    except Exception:
        guard.release(schedule_id, run_token)
        raise

    def run_once() -> None:
        """在后台线程中执行单次循环并记录结果。

        异常时记录日志，无论成败均释放运行锁。
        """
        try:
            result = run_cycle(
                automation,
                make_agent_round_runner(thread_id=thread_id, backend_type=environment),
                backend_type=environment,
            )
        except Exception:
            logger.exception("自动化任务 %s 手动触发失败", schedule_id)
        else:
            mark_automation_run(schedule_id, run_id=result.run_id)
        finally:
            guard.release(schedule_id, run_token)

    threading.Thread(
        target=run_once,
        name=f"automation-{schedule_id}",
        daemon=True,
    ).start()
    return {
        "automationId": schedule_id,
        "accepted": True,
        "threadId": thread_id,
        "roundNumber": round_number,
    }
