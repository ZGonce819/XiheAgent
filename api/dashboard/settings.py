"""用户、模型与系统设置相关接口。"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from config.app_config import get_settings
from config.approval_config import (
    get_command_approval_preferences,
    update_command_approval_preferences,
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

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/me")
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
    }


@router.get("/options")
def dashboard_options() -> dict[str, Any]:
    """返回页面选项（模型列表等）。

    合并 .env 默认模型和自定义模型列表，确定默认模型 ID。

    Returns:
        包含模型列表和默认模型配置的字典。
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


@router.get("/custom-models")
def dashboard_list_custom_models() -> list[dict[str, Any]]:
    """返回所有自定义模型配置列表。

    Returns:
        自定义模型配置字典列表。
    """
    return list_custom_models()


@router.post("/custom-models", status_code=201)
def dashboard_create_custom_model(body: dict[str, Any]) -> dict[str, Any]:
    """新增自定义模型。

    Args:
        body: 包含 ``name``、``api_base``、``model_name`` 等字段的请求体。

    Returns:
        新创建的自定义模型配置字典。

    Raises:
        HTTPException: 必填字段缺失时。
    """
    if not body.get("name") or not body.get("api_base") or not body.get("model_name"):
        raise HTTPException(status_code=400, detail="名称、API 地址和模型名不能为空")
    return create_custom_model(body)


@router.put("/custom-models/{model_id}")
def dashboard_update_custom_model(model_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """更新自定义模型配置。

    Args:
        model_id: 自定义模型 ID。
        body: 需要更新的配置字段字典。

    Returns:
        更新后的自定义模型配置字典。

    Raises:
        HTTPException: 模型不存在时。
    """
    existing = get_custom_model(model_id)
    if not existing:
        raise HTTPException(status_code=404, detail="模型不存在")
    result = update_custom_model(model_id, body)
    if result is None:
        raise HTTPException(status_code=404, detail="模型不存在")
    return result


@router.delete("/custom-models/{model_id}", status_code=204)
def dashboard_delete_custom_model(model_id: str) -> None:
    """删除自定义模型。

    Args:
        model_id: 自定义模型 ID。

    Raises:
        HTTPException: 模型不存在时。
    """
    if not delete_custom_model(model_id):
        raise HTTPException(status_code=404, detail="模型不存在")


@router.get("/builtin-model")
def dashboard_get_builtin_model() -> dict[str, Any]:
    """返回内置模型配置（API Key 已脱敏）。

    Returns:
        内置模型配置字典，敏感字段已脱敏处理。
    """
    return get_builtin_model()


@router.put("/builtin-model")
def dashboard_update_builtin_model(body: dict[str, Any]) -> dict[str, Any]:
    """更新内置模型的用户覆盖配置。

    Args:
        body: 需要覆盖的配置字段字典。

    Returns:
        更新后的内置模型配置字典。
    """
    return update_builtin_model(body)


@router.get("/profile")
def dashboard_profile() -> dict[str, Any]:
    """返回当前用户的默认工作台配置。

    配置存储尚未启用时返回稳定的环境默认值，确保前端首屏不会因
    可选配置缺失而失败。

    Returns:
        包含默认模型、推理力度、仓库配置和后端类型等的工作台配置字典。
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


@router.get("/command-approval-settings")
def dashboard_command_approval_settings() -> dict[str, Any]:
    """返回命令审批偏好的当前配置。

    Returns:
        命令审批偏好配置字典。
    """
    return get_command_approval_preferences()


@router.put("/command-approval-settings")
def dashboard_update_command_approval_settings(body: dict[str, Any]) -> dict[str, Any]:
    """更新命令审批偏好配置。

    Args:
        body: 新的审批偏好配置字典。

    Returns:
        更新后的审批偏好配置。

    Raises:
        HTTPException: 配置内容格式非法时。
    """
    try:
        return update_command_approval_preferences(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/skill-retrieval-settings")
def dashboard_skill_retrieval_settings() -> dict[str, Any]:
    """返回技能检索（RAG）开关的当前配置。

    Returns:
        技能检索偏好配置字典。
    """
    return get_skill_retrieval_preferences()


@router.put("/skill-retrieval-settings")
def dashboard_update_skill_retrieval_settings(body: dict[str, Any]) -> dict[str, Any]:
    """更新技能检索开关。

    改动在下一条消息处理时生效，无需重启服务。

    Args:
        body: 新的技能检索配置字典。

    Returns:
        更新后的技能检索配置字典。

    Raises:
        HTTPException: 配置内容格式非法时。
    """
    try:
        return update_skill_retrieval_preferences(body)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/repos")
def dashboard_repos() -> dict[str, Any]:
    """返回可用代码仓库列表。

    Returns:
        包含 ``installations`` 和 ``repositories`` 列表的字典。
    """
    return {"installations": [], "repositories": []}
