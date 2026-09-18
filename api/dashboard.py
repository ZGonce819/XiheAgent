"""Dashboard 前端接口。

提供会话管理、SSE 流式输出等 Dashboard 所需的 HTTP 接口。
前端通过 POST SSE 实时接收 Agent 运行事件和流式正文。
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

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
    get_automation,
    list_automations,
    mark_automation_triggered,
    update_automation,
)
from config.automation_state import (
    clear_stopped,
    clear_waiting_approval,
    load_state,
    render_state,
    set_budget,
    stop_decision,
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
from config.skill_retrieval_config import (
    get_skill_retrieval_preferences,
    update_skill_retrieval_preferences,
)
from schemas.request_schema import (
    DashboardEditPreviewRequest,
    DashboardProjectBindFolderRequest,
    DashboardProjectRenameRequest,
    DashboardSkillCreateRequest,
    DashboardSkillUpdateRequest,
    DashboardThreadMessageRequest,
    DashboardThreadTitleUpdateRequest,
    DashboardViewSnapshotRequest,
)
from services.approval_explanation import explain_pending_approval
from services.automation_loop import run_cycle
from services.automation_runner import (
    make_agent_round_runner,
    read_only_refusal,
    resolve_backend_type,
)
from services.automation_verification import parse_checks
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
logger = logging.getLogger(__name__)


def _validate_automation_payload(body: dict[str, Any], partial: bool = False) -> None:
    for field, label in (("name", "名称"), ("prompt", "任务指令"), ("schedule", "执行计划")):
        if (not partial or field in body) and not str(body.get(field) or "").strip():
            raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if "schedule" in body and len(str(body["schedule"]).split()) != 5:
        raise HTTPException(status_code=400, detail="执行计划必须是 5 段 Cron 表达式")
    # 检查项只做「是不是列表」这一层校验；命令本身合法与否由
    # services.automation_verification.parse_checks 在真正要跑的时候判定，
    # 因为 api 层不该替验证门决定它能接受什么。
    if body.get("checks") is not None and not isinstance(body["checks"], list):
        raise HTTPException(status_code=400, detail="检查项必须是列表")


def _skill_summary(skill_file: Path) -> dict[str, Any]:
    """读取单个 ``SKILL.md`` 并生成前端摘要。

    保留此兼容入口（历史调用方按文件路径传入），内部委托给
    :func:`services.skill_registry.read_skill`，确保摘要口径与技能加载器一致。

    Args:
        skill_file: ``SKILL.md`` 的路径。

    Returns:
        技能摘要字典；文件不可读时退回基础信息。
    """
    from services.skill_registry import GLOBAL_SCOPE, read_skill, scope_virtual_path

    record = read_skill(skill_file.parent, scope=GLOBAL_SCOPE)
    if record is not None:
        return record.to_payload()

    text = skill_file.read_text(encoding="utf-8", errors="replace")
    return {
        "id": skill_file.parent.name,
        "name": skill_file.parent.name,
        "description": "可由智能体按需加载的本地技能。",
        "virtualPath": f"{scope_virtual_path(GLOBAL_SCOPE)}{skill_file.parent.name}/SKILL.md",
        "updatedAt": datetime.fromtimestamp(
            skill_file.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "text": text[:0],
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
        "backendType": "local",
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


@dashboard_router.get("/skill-retrieval-settings")
def dashboard_skill_retrieval_settings() -> dict[str, Any]:
    """返回技能检索（RAG）开关的当前配置。"""
    return get_skill_retrieval_preferences()


@dashboard_router.put("/skill-retrieval-settings")
def dashboard_update_skill_retrieval_settings(body: dict[str, Any]) -> dict[str, Any]:
    """更新技能检索开关。改动在下一条消息处理时生效，无需重启服务。"""
    try:
        return update_skill_retrieval_preferences(body)
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


def _schedule_state_payload(schedule_id: str) -> dict[str, Any]:
    """拼出循环状态的响应体。

    带上 ``stopSource`` 是因为五个停止来源的恢复方式不同：显式标记与「停在等人工
    确认」能直接清掉，其余三条要调高预算上限。前端据此决定给「继续运行」按钮还是
    给一句提示。
    """
    source, reason = stop_decision(schedule_id)
    return {
        "state": load_state(schedule_id),
        "text": render_state(schedule_id),
        "stopped": source is not None,
        "stopReason": reason,
        "stopSource": source,
    }


@dashboard_router.get("/schedules/{schedule_id}/state")
def dashboard_schedule_state(schedule_id: str) -> dict[str, Any]:
    """返回某个自动化任务的循环状态。

    只读操作，不会把状态文件建出来。

    Args:
        schedule_id: 自动化任务 id。

    Returns:
        含原始 ``state``、给人看的 ``text``，以及停止状态与来源。

    Raises:
        HTTPException: 自动化任务不存在时返回 404。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")
    return _schedule_state_payload(schedule_id)


@dashboard_router.post("/schedules/{schedule_id}/resume")
def dashboard_resume_schedule(schedule_id: str) -> dict[str, Any]:
    """让一个被停下的循环重新可以触发。

    能救两种：被显式标记停止的（清停止标记），以及停在等人工确认的（清那一轮的
    等确认标记）。因轮数 / token / 连续未通过而停下的救不了 —— 那三条的恢复办法是
    调高对应的预算上限，所以这里直接返回 409 说明原因，而不是让用户点一个点了
    没反应的按钮。

    没停过的循环调用它是安全的，相当于什么都没做。

    Args:
        schedule_id: 自动化任务 id。

    Returns:
        与 GET ``/schedules/{id}/state`` 同样的状态结构，前端可直接替换。

    Raises:
        HTTPException: 自动化任务不存在返回 404；停止原因救不了返回 409。
    """
    if get_automation(schedule_id) is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")

    source, reason = stop_decision(schedule_id)
    if source == "marked":
        clear_stopped(schedule_id)
    elif source == "awaiting_approval":
        # 人工处理完待审批的命令之后点这里。清的是「最近一轮停在等确认」这个标记，
        # 不是停止标记 —— 这一条路本来就没往停止标记里写过东西。
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


@dashboard_router.patch("/schedules/{schedule_id}/budget")
def dashboard_update_schedule_budget(
    schedule_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """改一个自动化任务的预算上限。

    预算存在循环状态里、不随任务定义走 —— 它和「已经跑了多少轮 / 用掉多少 token」
    是一对，必须放在一起才不会算错。所以单独一个接口，不混进 ``PATCH /schedules/{id}``。

    上限写 ``0`` 表示不限制。调高上限也是「因为预算用完而停下的循环」唯一的恢复办法。

    Args:
        schedule_id: 自动化任务 id。
        body: 要改的上限，键取 ``maxRuns`` / ``maxTokens`` / ``maxConsecutiveFailures``；
            没给的键保持原值。

    Returns:
        与 GET ``/schedules/{id}/state`` 同样的状态结构，前端可直接替换。

    Raises:
        HTTPException: 任务不存在返回 404；一个上限都没给、或数值不是非负整数返回 400。
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
        # 布尔也是 int 的子类，`int(True)` 会静默变成 1；小数则会被静默截断。
        # 这类「看起来能用、其实悄悄改了数值」的输入当场拒掉。
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


@dashboard_router.post("/schedules/{schedule_id}/run", status_code=202)
def dashboard_run_schedule(
    schedule_id: str, background: BackgroundTasks
) -> dict[str, Any]:
    """手动跑一轮循环。

    「能不能跑」的判断全部同步做完：不能跑就带着理由直接返回，不留一个 202 让前端
    去猜。能跑则把整轮交给后台 —— 一轮可能要跑几分钟，不该占着这个请求。
    跑完之后的进展用 GET ``/schedules/{id}/state`` 读。

    Args:
        schedule_id: 自动化任务 id。
        background: FastAPI 的后台任务收集器。

    Returns:
        含 ``threadId`` 与第几轮的受理回执。

    Raises:
        HTTPException: 任务不存在返回 404；循环已停返回 409；
            超出只读范围、没配仓库或检查项配错返回 400。
    """
    automation = get_automation(schedule_id)
    if automation is None:
        raise HTTPException(status_code=404, detail="自动化任务不存在")

    stop_source, reason = stop_decision(schedule_id)
    if stop_source is not None:
        raise HTTPException(status_code=409, detail=f"这个循环已经停了：{reason}")

    refusal = read_only_refusal(str(automation.get("prompt") or ""))
    if refusal:
        raise HTTPException(status_code=400, detail=refusal)
    if not automation.get("repo"):
        raise HTTPException(status_code=400, detail="自动化任务没有配置代码仓库")
    try:
        parse_checks(automation.get("checks"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"检查项配置有误：{exc}") from exc

    # 会话 id 在这里就定下来：触发时间要连同它一起记进定义，前端也能立刻跳过去。
    thread_id = str(uuid.uuid4())
    mark_automation_triggered(schedule_id, thread_id=thread_id)
    round_number = int(load_state(schedule_id)["budget"]["runsUsed"]) + 1

    # 执行环境要显式定下来传下去：验证门的审批策略按它判断，缺省值 "local" 在
    # 开了沙箱的部署里会用错一套策略。
    environment = resolve_backend_type()

    def run_once() -> None:
        try:
            run_cycle(
                automation,
                make_agent_round_runner(thread_id=thread_id, backend_type=environment),
                backend_type=environment,
            )
        except Exception:
            # 跑一轮里的异常已经在 run_cycle 内部记成失败轮次了，能漏到这里的
            # 只剩编排本身的问题，记日志即可，不要让它冒到后台线程外面。
            logger.exception("自动化任务 %s 手动触发失败", schedule_id)

    background.add_task(run_once)
    return {
        "automationId": schedule_id,
        "accepted": True,
        "threadId": thread_id,
        "roundNumber": round_number,
    }


def _skill_scope_context(
    scope: str, project_name: str | None
) -> tuple[str, str | None]:
    """规范化技能作用域参数并做可用性校验。

    ``project`` 是 ``local`` 的历史旧称，会被折叠；``project_set`` 落在全局
    技能目录下，只要有项目名即可；``local`` 要求项目已绑定本地文件夹，否则
    落点不存在，写入会失败。

    Args:
        scope: 前端传入的作用域标识。
        project_name: 项目名。

    Returns:
        规范化后的 ``(scope, project_name)``。

    Raises:
        HTTPException: 作用域非法或缺少必需的项目名时返回 400。
    """
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        PROJECT_SET_SCOPE,
        SkillValidationError,
        normalize_scope,
        resolve_project_folder,
    )

    try:
        normalized = normalize_scope(scope)
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if normalized == GLOBAL_SCOPE:
        return normalized, None

    name = (project_name or "").strip()
    if not name:
        raise HTTPException(
            status_code=400,
            detail=f"{SCOPE_LABELS_CN.get(normalized, normalized)}技能必须指定项目名",
        )
    if normalized == LOCAL_SCOPE and resolve_project_folder(name) is None:
        raise HTTPException(
            status_code=400,
            detail=f"项目「{name}」尚未绑定本地文件夹，无法使用本地技能",
        )
    return normalized, name


# 用于拼接面向用户的错误文案。
SCOPE_LABELS_CN = {
    "global": "全局",
    "project_set": "项目",
    "local": "本地",
}


def _refresh_sandbox_skills(scope: str, project_name: str | None) -> None:
    """技能落盘后把最新技能集同步进活跃沙箱。

    技能在主机上是唯一副本，沙箱会话读的却是容器里的拷贝，因此网页或 Agent
    改动技能后必须主动推一次，否则沙箱里加载的仍是旧技能。会话正在执行时由
    :mod:`services.sandbox_skill_sync` 转为延迟同步，不会打断对话。

    Args:
        scope: 发生变更的作用域。
        project_name: 变更所属项目集合名（``global`` 时为 ``None``）。

    Note:
        同步失败只记录日志，绝不影响技能本身的写入结果——技能已经落盘成功，
        沙箱同步属于尽力而为的副本维护。
    """
    try:
        from services.sandbox_skill_sync import refresh_sandbox_skills

        refresh_sandbox_skills(scope=scope, project_name=project_name)
    except Exception:
        logger.warning(
            "技能已保存，但同步到沙箱失败：scope=%s project=%s",
            scope,
            project_name,
            exc_info=True,
        )


@dashboard_router.get("/skills")
def dashboard_skills(
    project_name: str | None = None, backend_type: str | None = None
) -> dict[str, Any]:
    """列出可见的 DeepAgents 技能，并标注作用域与来源。

    技能分四层：内置层（仅模板源）、全局层、项目集合层与本地层。返回值同时
    携带当前作用域配置信息，供前端渲染筛选与创建入口。

    沙箱会话（``backend_type=sandbox``）少一层：容器里没有项目本地文件夹，
    ``local`` 技能既加载不到也同步不进容器，因此这里把该层标为不可用、从列表
    和可写作用域中剔除，避免出现「看得见但用不上」的死选项。

    Args:
        project_name: 可选的项目名，提供时会一并列出该项目的项目集合技能与
            本地技能。
        backend_type: 可选的会话运行环境，``"sandbox"`` 时隐藏本地层。

    Returns:
        包含 ``items``、``scopes``、``virtualRoot`` 与 ``available`` 的字典。
    """
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        PROJECT_SET_SCOPE,
        list_skills,
        project_set_dir,
        resolve_project_folder,
        writable_scopes,
    )

    sandbox_session = (backend_type or "").strip().lower() == "sandbox"

    skill_items: list[dict[str, Any]] = []
    for record in list_skills(GLOBAL_SCOPE):
        skill_items.append(record.to_payload())

    has_project = bool(project_name and project_name.strip())
    project_dir = resolve_project_folder(project_name) if has_project else None
    local_available = project_dir is not None and not sandbox_session
    if has_project:
        for record in list_skills(PROJECT_SET_SCOPE, project_name):
            skill_items.append(record.to_payload())
        if local_available:
            for record in list_skills(LOCAL_SCOPE, project_name):
                skill_items.append(record.to_payload())

    scopes = [
        {
            "scope": GLOBAL_SCOPE,
            "label": "全局",
            "description": "跨项目共享，所有会话都可加载。",
            "projectName": None,
            "directory": str(get_settings().skills_dir),
            "available": True,
        }
    ]
    # 三个作用域始终全部返回：未指定项目时「项目」「本地」标为不可用而不是不返回。
    # 前端据此把三张卡片和筛选选项稳定地摆出来，避免列表随选择变化而跳一下。
    try:
        set_directory = str(project_set_dir(project_name)) if has_project else None
    except Exception:
        set_directory = None
    scopes.append(
        {
            "scope": PROJECT_SET_SCOPE,
            "label": "项目",
            "description": "仅该项目可见，同名时覆盖全局技能。",
            "projectName": project_name if has_project else None,
            # 项目集合落在全局技能目录下，只要有项目名就可用。
            "directory": set_directory,
            "available": has_project,
        }
    )
    scopes.append(
        {
            "scope": LOCAL_SCOPE,
            "label": "本地",
            "description": (
                "沙箱会话没有项目本地文件夹，该层不参与加载。"
                if sandbox_session
                else "仅该项目可见，存放在项目文件夹内，同名时覆盖项目技能。"
            ),
            "projectName": project_name if has_project else None,
            "directory": (
                str(project_dir / ".xihe-agent" / "skills") if project_dir else None
            ),
            "available": local_available,
        }
    )

    available_scopes = [
        scope
        for scope in writable_scopes(project_name)
        if not (sandbox_session and scope == LOCAL_SCOPE)
    ]

    return {
        "items": skill_items,
        "scopes": scopes,
        "writableScopes": available_scopes,
        "virtualRoot": "/skills",
        "available": any(item["available"] for item in scopes),
    }


@dashboard_router.get("/skills/{skill_id}")
def dashboard_skill_detail(
    skill_id: str, scope: str = "global", project_name: str | None = None
) -> dict[str, Any]:
    """读取单个技能的完整内容。

    Args:
        skill_id: 技能目录名。
        scope: 作用域，``global`` / ``project_set`` / ``local``。
        project_name: 项目集合名或项目名（``global`` 时忽略）。

    Returns:
        技能摘要加上 ``content``（SKILL.md 全文）。

    Raises:
        HTTPException: 作用域非法或技能不存在时返回 400 / 404。
    """
    from services.skill_registry import SKILL_FILE_NAME, scope_dir, read_skill

    normalized, name = _skill_scope_context(scope, project_name)
    directory = scope_dir(normalized, name) / skill_id
    record = read_skill(directory, scope=normalized, project_name=name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"技能「{skill_id}」不存在")
    try:
        content = (directory / SKILL_FILE_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取技能失败：{e}")
    payload = record.to_payload()
    payload["content"] = content
    return payload


@dashboard_router.post("/skills", status_code=201)
def dashboard_create_skill(body: DashboardSkillCreateRequest) -> dict[str, Any]:
    """创建技能。

    支持两种输入：提供 ``content``（完整 SKILL.md）时直接校验入库；
    否则用 ``name`` + ``description`` + ``body`` 由后端组装 frontmatter。
    校验口径与技能加载器完全一致，不合格会明确报错，不会产生加载不到的
    幽灵技能。

    Args:
        body: 创建请求体。

    Returns:
        新建技能的摘要。

    Raises:
        HTTPException: 作用域非法、必填缺失或内容不合规时返回 400。
    """
    from services.skill_registry import (
        SkillValidationError,
        create_skill,
        ensure_unique_skill_name,
        parse_frontmatter,
        scope_dir,
    )

    scope, project_name = _skill_scope_context(body.scope, body.project_name)

    content = (body.content or "").strip()
    if content:
        frontmatter = parse_frontmatter(content)
        raw_name = str(frontmatter.get("name") or body.name or "").strip()
        raw_description = str(frontmatter.get("description") or body.description or "").strip()
        markup = content
    else:
        raw_name = (body.name or "").strip()
        raw_description = (body.description or "").strip()
        markup = None

    if not markup and not raw_name:
        raise HTTPException(status_code=400, detail="技能名不能为空")
    if not markup and not raw_description:
        raise HTTPException(status_code=400, detail="技能说明不能为空")

    try:
        base_dir = scope_dir(scope, project_name)
        if markup:
            # 从完整文档中剥离 frontmatter，正文交由 registry 重新组装，
            # 避免出现两段 frontmatter。
            from services.skill_registry import split_frontmatter

            _, markup_body = split_frontmatter(markup)
            document_name = _slug_or_fallback(raw_name)
            document_name = ensure_unique_skill_name(base_dir, document_name)
            record = create_skill(
                name=raw_name or document_name,
                description=raw_description,
                body=markup_body,
                scope=scope,
                project_name=project_name,
                origin="user",
                overwrite=False,
                directory_name=document_name,
            )
        else:
            record = create_skill(
                name=raw_name,
                description=raw_description,
                body=body.body or "",
                scope=scope,
                project_name=project_name,
                origin="user",
                overwrite=False,
            )
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能写入失败：{e}")

    logger.info(
        "已创建技能：scope=%s project=%s id=%s", scope, project_name, record.id
    )
    _refresh_sandbox_skills(scope, project_name)
    return record.to_payload()


@dashboard_router.put("/skills/{skill_id}")
def dashboard_update_skill(
    skill_id: str, body: DashboardSkillUpdateRequest
) -> dict[str, Any]:
    """更新技能内容。

    Args:
        skill_id: 技能目录名。
        body: 更新请求体，未提供的字段沿用原值。

    Returns:
        更新后的技能摘要。

    Raises:
        HTTPException: 作用域非法、技能不存在或内容不合规时返回 400 / 404。
    """
    from services.skill_registry import SkillValidationError, split_frontmatter, update_skill

    scope, project_name = _skill_scope_context(body.scope, body.project_name)

    name = body.name
    description = body.description
    markup_body = body.body

    content = (body.content or "").strip()
    if content:
        from services.skill_registry import parse_frontmatter

        frontmatter = parse_frontmatter(content)
        _, markup_body = split_frontmatter(content)
        name = str(frontmatter.get("name") or name or "").strip() or name
        description = str(frontmatter.get("description") or description or "").strip() or description

    try:
        record = update_skill(
            skill_id,
            scope=scope,
            project_name=project_name,
            name=name,
            description=description,
            body=markup_body,
        )
    except SkillValidationError as e:
        message = str(e)
        if "不存在" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能更新失败：{e}")

    logger.info("已更新技能：scope=%s id=%s", scope, record.id)
    _refresh_sandbox_skills(scope, project_name)
    return record.to_payload()


@dashboard_router.delete("/skills/{skill_id}", status_code=204)
def dashboard_delete_skill(
    skill_id: str, scope: str = "global", project_name: str | None = None
) -> None:
    """删除技能。

    Args:
        skill_id: 技能目录名。
        scope: 作用域，``global`` / ``project_set`` / ``local``。
        project_name: 项目集合名或项目名（``global`` 时忽略）。

    Raises:
        HTTPException: 作用域非法或技能不存在时返回 400 / 404。
    """
    from services.skill_registry import SkillValidationError, delete_skill

    normalized, name = _skill_scope_context(scope, project_name)
    try:
        removed = delete_skill(skill_id, scope=normalized, project_name=name)
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能删除失败：{e}")
    if not removed:
        raise HTTPException(status_code=404, detail=f"技能「{skill_id}」不存在")
    _refresh_sandbox_skills(normalized, name)


@dashboard_router.post("/skills/upload", status_code=201)
def dashboard_upload_skill(
    file: UploadFile = File(..., description="技能 ZIP 包或单个 SKILL.md"),
    scope: str = Form("global", description="作用域：global / project_set / local"),
    project_name: str | None = Form(None, description="项目集合名或项目名"),
    name: str | None = Form(None, description="技能名，仅在单文件上传且缺少 frontmatter 时使用"),
    overwrite: bool = Form(False, description="同名技能已存在时是否覆盖"),
) -> dict[str, Any]:
    """通过上传 ZIP 包或单个 SKILL.md 创建技能。

    ZIP 包的根目录可以直接是技能目录（含 SKILL.md），也可以是包含多个
    技能子目录的集合。所有条目都会经过路径穿越校验，拒绝 ``..``、绝对路径
    和符号链接，并限制文件数量与总体积。

    Args:
        file: 上传的文件，``.zip`` 或 ``.md``。
        scope: 目标作用域。
        project_name: 项目集合名或项目名。
        name: 单文件上传时的回退技能名。
        overwrite: 是否覆盖同名技能。

    Returns:
        包含 ``created``（成功列表）与 ``failed``（失败详情）的字典。

    Raises:
        HTTPException: 作用域非法、文件类型不支持、超限或解压失败时。
    """
    import io
    import zipfile

    from services.skill_registry import (
        MAX_SKILL_TOTAL_FILES,
        MAX_SKILL_TOTAL_SIZE,
        SKILL_FILE_NAME,
        SkillValidationError,
        create_skill,
        ensure_unique_skill_name,
        parse_frontmatter,
        scope_dir,
        slugify_skill_name,
        split_frontmatter,
        validate_skill_content,
    )

    normalized, project = _skill_scope_context(scope, project_name)
    base_dir = scope_dir(normalized, project)
    filename = (file.filename or "").strip().lower()

    # 上传体积上限：ZIP 与单文件共用同一个总大小约束。
    raw = file.file.read(MAX_SKILL_TOTAL_SIZE + 1)
    if len(raw) > MAX_SKILL_TOTAL_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"上传内容过大（上限 {MAX_SKILL_TOTAL_SIZE // 1000} KB）",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    created: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    def _store(
        skill_name: str,
        directory_name: str,
        document: str,
        attachments: dict[str, bytes] | None = None,
    ) -> None:
        """校验并落盘一个技能。

        Args:
            skill_name: frontmatter 中的技能名。
            directory_name: 目标目录名。
            document: 完整 SKILL.md 文本。
            attachments: 随技能一并写入的附属文件，键为相对路径。
        """
        validate_skill_content(document)
        _, markup_body = split_frontmatter(document)
        final_name = directory_name
        if not overwrite:
            final_name = ensure_unique_skill_name(base_dir, directory_name)
        record = create_skill(
            name=skill_name,
            description=str(parse_frontmatter(document).get("description") or ""),
            body=markup_body,
            scope=normalized,
            project_name=project,
            origin="user",
            extra_files=attachments or {},
            overwrite=overwrite,
            directory_name=final_name,
        )
        created.append(record.to_payload())

    if filename.endswith(".md") or filename.endswith(".markdown"):
        document = raw.decode("utf-8", errors="replace")
        if not document.startswith("---"):
            fallback = (name or "").strip() or Path(filename).stem
            slug = slugify_skill_name(fallback)
            if not slug:
                raise HTTPException(
                    status_code=400,
                    detail="SKILL.md 缺少 frontmatter，且未能从文件名推导出合法技能名",
                )
            heading = re.search(r"^#\s+(.+)$", document, re.MULTILINE)
            description = heading.group(1).strip() if heading else f"由 {Path(filename).stem} 导入的技能。"
            from services.skill_registry import build_skill_document

            document = build_skill_document(slug, description, document)
        frontmatter = parse_frontmatter(document)
        skill_name = str(frontmatter.get("name") or "").strip()
        directory_name = ensure_unique_skill_name(
            base_dir, slugify_skill_name(skill_name) or slugify_skill_name(name or "") or "skill"
        )
        try:
            _store(skill_name, directory_name, document)
        except SkillValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"created": created, "failed": failed}

    if not filename.endswith(".zip"):
        raise HTTPException(
            status_code=400, detail="只支持上传 .zip 技能包或单个 .md 文件"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 包已损坏或格式不正确")

    with archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) > MAX_SKILL_TOTAL_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"技能包文件数过多（上限 {MAX_SKILL_TOTAL_FILES} 个）",
            )

        grouped: dict[str, dict[str, bytes]] = {}
        for item in members:
            # 路径穿越防护：拒绝绝对路径、盘符与 .. 片段。
            normalized_name = item.filename.replace("\\", "/")
            parts = [part for part in normalized_name.split("/") if part not in {"", "."}]
            if not parts or ".." in parts or normalized_name.startswith("/"):
                failed.append({"path": item.filename, "reason": "路径非法"})
                continue
            if re.match(r"^[A-Za-z]:", normalized_name):
                failed.append({"path": item.filename, "reason": "路径非法"})
                continue
            if item.file_size > MAX_SKILL_TOTAL_SIZE:
                failed.append({"path": item.filename, "reason": "单文件过大"})
                continue
            if parts[-1] == SKILL_FILE_NAME:
                if len(parts) < 2:
                    # 根级 SKILL.md：视为单个技能，目录名从内容推导。
                    grouped.setdefault("", {})[SKILL_FILE_NAME] = archive.read(item)
                    continue
                grouped.setdefault("/".join(parts[:-1]), {})[SKILL_FILE_NAME] = archive.read(item)
            else:
                if len(parts) < 2:
                    continue
                grouped.setdefault("/".join(parts[:-1]), {})[parts[-1]] = archive.read(item)

        if not grouped:
            raise HTTPException(status_code=400, detail="技能包中未找到 SKILL.md")

        # ZIP 中可能既有根级 SKILL.md 又有深层附属文件，这里把所有条目归一到
        # 「最外层含 SKILL.md 的目录」，避免 refs/a.md 被当成分散技能而丢失。
        skill_roots = sorted(
            (key for key, files in grouped.items() if SKILL_FILE_NAME in files),
            key=len,
        )

        def _owner_root(relative_dir: str) -> str | None:
            """找出拥有该目录的最外层技能根。

            Args:
                relative_dir: 条目所在的相对目录。

            Returns:
                匹配的技能根；没有归属时返回 ``None``。
            """
            for root in skill_roots:
                if relative_dir == root or relative_dir.startswith(f"{root}/"):
                    return root
            return None

        # 解包后可能形成嵌套技能目录，取最外层含 SKILL.md 的目录作为技能根。
        for root in skill_roots:
            document = grouped[root][SKILL_FILE_NAME].decode("utf-8", errors="replace")
            frontmatter = parse_frontmatter(document)
            skill_name = str(frontmatter.get("name") or "").strip()
            leaf = Path(root).name if root else ""
            candidate = slugify_skill_name(skill_name) or slugify_skill_name(leaf) or "skill"
            try:
                validate_skill_content(document)
            except SkillValidationError as e:
                failed.append({"path": root or leaf or SKILL_FILE_NAME, "reason": str(e)})
                continue

            # 收集归属该技能根的全部附属文件，保留其相对技能根的路径。
            # 注意：技能根自身的目录条目也要纳入，否则与 SKILL.md 同级的
            # helper.py 之类文件会被静默丢弃。
            attachments: dict[str, bytes] = {}
            for relative_dir, files in grouped.items():
                if _owner_root(relative_dir) != root:
                    continue
                prefix = "" if relative_dir == root else f"{relative_dir[len(root) + 1:]}/"
                for path, payload in files.items():
                    if path == SKILL_FILE_NAME:
                        continue
                    attachments[f"{prefix}{path}"] = payload

            try:
                _store(skill_name or candidate, candidate, document, attachments)
            except SkillValidationError as e:
                failed.append({"path": root or leaf, "reason": str(e)})

    if not created:
        detail = "技能包中没有可用的技能"
        if failed:
            detail = f"{detail}：{failed[0]['reason']}"
        raise HTTPException(status_code=400, detail=detail)

    logger.info(
        "已上传技能：scope=%s project=%s created=%d failed=%d",
        normalized,
        project,
        len(created),
        len(failed),
    )
    _refresh_sandbox_skills(normalized, project)
    return {"created": created, "failed": failed}


def _slug_or_fallback(raw_name: str) -> str:
    """把技能名规范化为合法的目录名，失败时给出编号回退。

    Args:
        raw_name: 原始技能名。

    Returns:
        合法的目录名。
    """
    from services.skill_registry import coerce_ascii_name

    return coerce_ascii_name(raw_name)


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


@dashboard_router.post("/threads/{thread_id}/approval/explanation")
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
    return build_streaming_response(thread_id=thread_id, repo_url=repo_url, content=body.content, model_id=body.model_id, backend_type=requested_backend, project_name=body.project_name)


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
            project_name=body.project_name,
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


@dashboard_router.get("/workspace/projects")
def dashboard_list_projects() -> dict[str, Any]:
    """列出项目目录下的所有子目录作为项目。

    每个项目会合并已持久化的本地文件夹绑定（``local_folder``），
    未绑定时该字段为 ``None``。

    Returns:
        dict: 包含 ``projects`` 列表，每项有 ``name``、``path`` 和 ``local_folder`` 字段。
    """
    settings = get_settings()
    projects_dir = settings.projects_dir

    bindings: dict[str, str] = {}
    try:
        for record in get_store().list_project_folders():
            name = record.get("project_name")
            folder = record.get("local_folder")
            if isinstance(name, str) and isinstance(folder, str) and folder.strip():
                bindings[name] = folder
    except Exception:
        logger.warning("读取项目本地文件夹绑定失败，返回未合并的目录列表", exc_info=True)

    if not projects_dir.exists():
        return {"projects": [], "projects_dir": str(projects_dir)}

    items: list[dict[str, Any]] = []
    for child in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
        if child.is_dir() and not child.name.startswith("."):
            items.append({
                "name": child.name,
                "path": str(child),
                "local_folder": bindings.get(child.name),
            })

    return {"projects": items, "projects_dir": str(projects_dir)}


@dashboard_router.patch("/workspace/projects/rename")
def dashboard_rename_project(body: DashboardProjectRenameRequest) -> dict[str, Any]:
    """重命名项目：把该项目下所有会话的 project_name 更新为新名称。

    Args:
        body: 包含 ``old_name`` 和 ``new_name`` 的请求体。

    Returns:
        dict: ``renamed`` 为受影响会话数，``name`` 为新项目名。

    Raises:
        HTTPException: 项目名称为空时返回 400。
    """
    old_name = body.old_name.strip()
    new_name = body.new_name.strip()
    if not old_name or not new_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if old_name == new_name:
        return {"renamed": 0, "name": new_name}
    affected = get_store().rename_project(old_name, new_name)
    return {"renamed": affected, "name": new_name}


@dashboard_router.post("/workspace/projects/bind-folder")
def dashboard_bind_project_folder(body: DashboardProjectBindFolderRequest) -> dict[str, Any]:
    """把项目绑定到一个本地文件夹，作为该项目的项目根。

    绑定后，该项目的所有会话都以这个文件夹为 Agent 工作根目录（文件夹即项目根），
    并在其中维护 ``.xihe-agent/{skills,memories,reviews}`` 布局。

    Args:
        body: 包含 ``project_name`` 和 ``local_folder`` 的请求体。

    Returns:
        dict: ``project_name`` 与绑定后的 ``local_folder``（已解析为绝对路径）。

    Raises:
        HTTPException: 文件夹不存在或不是目录时返回 400。
    """
    project_name = body.project_name.strip()
    folder = Path(body.local_folder.strip()).resolve()
    if not project_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=400, detail=f"本地文件夹不存在或不是目录：{folder}")
    get_store().upsert_project_folder(project_name, str(folder))
    return {"project_name": project_name, "local_folder": str(folder)}


@dashboard_router.delete("/workspace/projects/{project_name}/unbind-folder")
def dashboard_unbind_project_folder(project_name: str) -> dict[str, Any]:
    """解除项目的本地文件夹绑定，恢复使用全局工作区。

    Args:
        project_name: 项目名称。

    Returns:
        dict: ``project_name`` 与 ``unbound``（是否确实删除了一条绑定）。
    """
    name = project_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    removed = get_store().delete_project_folder(name)
    return {"project_name": name, "unbound": removed > 0}
