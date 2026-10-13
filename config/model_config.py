"""自定义模型配置管理模块。

支持通过 API 动态增删改自定义模型，无需重启服务。
模型配置持久化到 ``data/custom_models.json`` 文件。
内置模型的用户覆盖配置（API Key、温度等）存储在 ``data/builtin_model.json``。
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from config.app_config import get_settings
from config.constants import DEEPSEEK_V4_MAX_TOKENS

_CUSTOM_MODELS_FILENAME = "custom_models.json"
_BUILTIN_MODEL_FILENAME = "builtin_model.json"


def _models_path() -> Path:
    """返回自定义模型配置文件路径。"""
    return get_settings().data_dir / _CUSTOM_MODELS_FILENAME


def _builtin_path() -> Path:
    """返回内置模型覆盖配置文件路径。"""
    return get_settings().data_dir / _BUILTIN_MODEL_FILENAME


def _load_models() -> list[dict[str, Any]]:
    """从磁盘加载自定义模型列表。"""
    path = _models_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_models(models: list[dict[str, Any]]) -> None:
    """将自定义模型列表写入磁盘。"""
    path = _models_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")


def list_custom_models() -> list[dict[str, Any]]:
    """返回所有自定义模型配置（API Key 已脱敏）。

    Returns:
        自定义模型配置字典列表，``api_key`` 字段已脱敏。
    """
    models = _load_models()
    result = []
    for m in models:
        result.append({
            **m,
            "api_key": _mask_key(m.get("api_key", "")),
        })
    return result


def get_custom_model(model_id: str) -> dict[str, Any] | None:
    """按 ID 查找自定义模型（返回完整配置，含 API Key）。

    Args:
        model_id: 自定义模型 ID。

    Returns:
        匹配的模型配置字典；未找到返回 ``None``。
    """
    for m in _load_models():
        if m.get("id") == model_id:
            return m
    return None


def create_custom_model(payload: dict[str, Any]) -> dict[str, Any]:
    """新增一个自定义模型。

    Args:
        payload: 包含 ``name``、``api_base``、``api_key``、``model_name`` 等字段的请求体。

    Returns:
        新创建的模型配置字典，``api_key`` 已脱敏。
    """
    models = _load_models()
    set_default = bool(payload.get("set_default", False))
    if set_default:
        for model in models:
            model["is_default"] = False
        _clear_builtin_default()
    new_model: dict[str, Any] = {
        "id": str(uuid.uuid4())[:8],
        "name": payload.get("name", ""),
        "api_base": payload.get("api_base", ""),
        "api_key": payload.get("api_key", ""),
        "model_name": payload.get("model_name", ""),
        "max_tokens": int(payload.get("max_tokens", 4096)),
        "temperature": float(payload.get("temperature", 0.7)),
        "is_default": set_default,
    }
    models.append(new_model)
    _save_models(models)
    return {**new_model, "api_key": _mask_key(new_model["api_key"])}


def update_custom_model(model_id: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    """更新自定义模型配置。

    Args:
        model_id: 自定义模型 ID。
        payload: 需要更新的字段字典。

    Returns:
        更新后的模型配置字典（``api_key`` 已脱敏）；ID 不存在返回 ``None``。
    """
    models = _load_models()
    for i, m in enumerate(models):
        if m.get("id") == model_id:
            for key in ("name", "api_base", "api_key", "model_name", "max_tokens", "temperature"):
                if key in payload:
                    val = payload[key]
                    if key == "max_tokens":
                        val = int(val)
                    elif key == "temperature":
                        val = float(val)
                    models[i][key] = val
            # 处理设为默认：清除其他自定义模型和内置模型的默认标记
            if payload.get("set_default"):
                for other in models:
                    other["is_default"] = False
                models[i]["is_default"] = True
                _clear_builtin_default()
            _save_models(models)
            return {**models[i], "api_key": _mask_key(models[i]["api_key"])}
    return None


def delete_custom_model(model_id: str) -> bool:
    """删除自定义模型。

    Args:
        model_id: 自定义模型 ID。

    Returns:
        成功删除返回 ``True``；ID 不存在返回 ``False``。
    """
    models = _load_models()
    new_models = [m for m in models if m.get("id") != model_id]
    if len(new_models) == len(models):
        return False
    _save_models(new_models)
    return True


def get_default_custom_model() -> dict[str, Any] | None:
    """返回标记为默认的自定义模型（含完整 API Key）。

    Returns:
        标记为默认的模型配置字典；无默认时返回 ``None``。
    """
    for m in _load_models():
        if m.get("is_default"):
            return m
    return None


def _mask_key(key: str) -> str:
    """脱敏 API Key，仅显示前4位和后4位。"""
    if not key or len(key) <= 8:
        return "****"
    return f"{key[:4]}...{key[-4:]}"


# ── 内置模型用户覆盖 ──────────────────────────────────────────────


def _load_builtin() -> dict[str, Any]:
    """加载内置模型覆盖配置。"""
    path = _builtin_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_builtin(data: dict[str, Any]) -> None:
    """写入内置模型覆盖配置。"""
    path = _builtin_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def get_builtin_model() -> dict[str, Any]:
    """返回内置模型的完整配置（含 .env 默认值 + 用户覆盖）。

    返回的字典包含 ``api_key_masked`` 字段用于前端展示。
    """
    from config.env_config import get_env, require_env

    overrides = _load_builtin()
    api_key = overrides.get("api_key") or get_env("MAIN_API_KEY") or require_env("DEEPSEEK_API_KEY")
    return {
        "id": "__builtin__",
        "name": overrides.get("name") or get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "model_name": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "api_base": overrides.get("api_base") or get_env("MAIN_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
        "api_key": api_key,
        "api_key_masked": _mask_key(api_key),
        "max_tokens": overrides.get("max_tokens", DEEPSEEK_V4_MAX_TOKENS),
        "temperature": overrides.get("temperature", 1.1),
        "is_default": overrides.get("is_default", False),
        "has_overrides": bool(overrides),
    }


def update_builtin_model(payload: dict[str, Any]) -> dict[str, Any]:
    """更新内置模型的用户覆盖配置。

    空字符串的 ``api_key`` / ``api_base`` 视为清除覆盖，回退到 .env 默认值。
    """
    overrides = _load_builtin()
    for key in ("max_tokens", "temperature", "is_default"):
        if key in payload:
            if key == "max_tokens":
                overrides[key] = int(payload[key])
            elif key == "temperature":
                overrides[key] = float(payload[key])
            elif key == "is_default":
                overrides[key] = bool(payload[key])
    if "api_key" in payload:
        if payload["api_key"]:
            overrides["api_key"] = payload["api_key"]
        else:
            overrides.pop("api_key", None)
    if "api_base" in payload:
        if payload["api_base"]:
            overrides["api_base"] = payload["api_base"]
        else:
            overrides.pop("api_base", None)
    _save_builtin(overrides)
    # 内置模型设为默认时，清除所有自定义模型的默认标记
    if overrides.get("is_default"):
        _clear_custom_defaults()
    return get_builtin_model()


def _clear_builtin_default() -> None:
    """清除内置模型的默认标记。"""
    overrides = _load_builtin()
    if overrides.get("is_default"):
        overrides["is_default"] = False
        _save_builtin(overrides)


def _clear_custom_defaults() -> None:
    """清除所有自定义模型的默认标记。"""
    models = _load_models()
    changed = False
    for m in models:
        if m.get("is_default"):
            m["is_default"] = False
            changed = True
    if changed:
        _save_models(models)


def get_builtin_override() -> dict[str, Any] | None:
    """返回内置模型的用户覆盖配置（含完整 API Key）。

    仅当用户已保存覆盖配置且标记为默认时返回，否则返回 ``None``。
    """
    overrides = _load_builtin()
    if not overrides or not overrides.get("is_default"):
        return None
    from config.env_config import get_env, require_env

    return {
        "model_name": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "api_base": overrides.get("api_base") or get_env("MAIN_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
        "api_key": overrides.get("api_key") or get_env("MAIN_API_KEY") or require_env("DEEPSEEK_API_KEY"),
        "max_tokens": overrides.get("max_tokens", DEEPSEEK_V4_MAX_TOKENS),
        "temperature": overrides.get("temperature", 1.1),
    }
