"""Persistent automation configuration for the dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.app_config import get_settings

_AUTOMATIONS_FILENAME = "automations.json"


def _automations_path() -> Path:
    return get_settings().data_dir / _AUTOMATIONS_FILENAME


def _load_automations() -> list[dict[str, Any]]:
    path = _automations_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_automations(automations: list[dict[str, Any]]) -> None:
    path = _automations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(automations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_automations() -> list[dict[str, Any]]:
    return sorted(
        _load_automations(),
        key=lambda item: str(item.get("updatedAt") or ""),
        reverse=True,
    )


def _normalize_checks(value: Any) -> list[Any]:
    """收下检查项配置。

    这里只保证「是一个列表」—— 语义校验（命令不能为空、超时是正整数）由
    :func:`services.automation_verification.parse_checks` 在真正要跑的时候做，
    因为 ``config`` 层不能反向依赖 ``services``。

    Args:
        value: 原始配置。

    Returns:
        列表形式的检查项；类型不对时返回空列表。
    """
    return list(value) if isinstance(value, list) else []


def _normalize_flag(value: Any) -> bool:
    """收下开关类配置。

    只认真正的布尔值：``"false"`` 这样的字符串是**真的**，传成字符串就是配置写错了。
    一个把人工闸门交出去的开关，宁可关着，也不能因为解析宽松而意外打开。

    Args:
        value: 原始配置。

    Returns:
        布尔值；不是布尔时返回 ``False``。
    """
    return value if isinstance(value, bool) else False


def get_automation(automation_id: str) -> dict[str, Any] | None:
    """按 id 取一个自动化定义。

    Args:
        automation_id: 自动化任务 id。

    Returns:
        找到返回定义，找不到返回 ``None``。
    """
    for automation in _load_automations():
        if automation.get("id") == automation_id:
            return automation
    return None


def create_automation(payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    automation = {
        "id": str(uuid.uuid4())[:8],
        "name": str(payload.get("name") or "").strip(),
        "prompt": str(payload.get("prompt") or "").strip(),
        "schedule": str(payload.get("schedule") or "").strip(),
        "repo": payload.get("repo") or None,
        "checks": _normalize_checks(payload.get("checks")),
        "model": payload.get("model_id") or "__builtin__",
        "effort": payload.get("effort") or "default",
        "autoApprove": _normalize_flag(payload.get("autoApprove")),
        "enabled": True,
        "cronId": None,
        "lastThreadId": None,
        "lastRunId": None,
        "lastTriggeredAt": None,
        "lastError": None,
        "lastErrorAt": None,
        "createdAt": now,
        "updatedAt": now,
    }
    automations = _load_automations()
    automations.append(automation)
    _save_automations(automations)
    return automation


def update_automation(
    automation_id: str, payload: dict[str, Any]
) -> dict[str, Any] | None:
    automations = _load_automations()
    aliases = {"model_id": "model"}
    allowed = {
        "name",
        "prompt",
        "schedule",
        "repo",
        "checks",
        "model_id",
        "effort",
        "enabled",
        "autoApprove",
    }
    for automation in automations:
        if automation.get("id") != automation_id:
            continue
        for key in allowed:
            if key not in payload or payload[key] is None:
                continue
            target = aliases.get(key, key)
            value = payload[key]
            automation[target] = value.strip() if isinstance(value, str) else value
        automation["repo"] = automation.get("repo") or None
        automation["checks"] = _normalize_checks(automation.get("checks"))
        automation["autoApprove"] = _normalize_flag(automation.get("autoApprove"))
        automation["updatedAt"] = datetime.now(UTC).isoformat()
        _save_automations(automations)
        return automation
    return None


def delete_automation(automation_id: str) -> bool:
    automations = _load_automations()
    remaining = [item for item in automations if item.get("id") != automation_id]
    if len(remaining) == len(automations):
        return False
    _save_automations(remaining)
    return True


def mark_automation_triggered(
    automation_id: str,
    *,
    thread_id: str | None = None,
    run_id: str | None = None,
    error: str | None = None,
    at: datetime | None = None,
) -> dict[str, Any] | None:
    """记录一次触发。

    ``lastTriggeredAt`` / ``lastThreadId`` / ``lastRunId`` / ``lastError`` /
    ``lastErrorAt`` 这几个字段在 :func:`create_automation` 里就留好了位置，
    但此前一直是 ``None`` —— 没有任何东西按执行计划触发过这些任务。本函数是
    第一个写它们的地方。

    触发时间是 :func:`services.automation_loop.is_due` 判断「这一分钟跑过没有」
    的依据，所以**必须在启动一轮之前写**，否则同一分钟会被重复触发。

    Args:
        automation_id: 自动化任务 id。
        thread_id: 本轮对应的会话 id。
        run_id: 本轮运行 id。
        error: 启动失败的原因；传 ``None`` 表示这次触发是成功的，会顺手清掉
            上一次的错误记录。
        at: 触发时刻；不传则取当前 UTC 时间。**扫描与标记必须用同一个时钟**，
            否则「同一分钟不重复触发」这条保证会失效；补跑历史时间时需要显式传入。

    Returns:
        更新后的自动化定义；id 不存在时返回 ``None``。
    """
    moment = at or datetime.now(UTC)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    now = moment.isoformat()
    automations = _load_automations()
    for automation in automations:
        if automation.get("id") != automation_id:
            continue
        automation["lastTriggeredAt"] = now
        automation["lastThreadId"] = thread_id
        automation["lastRunId"] = run_id
        automation["lastError"] = str(error) if error else None
        automation["lastErrorAt"] = now if error else None
        automation["updatedAt"] = now
        _save_automations(automations)
        return automation
    return None
