"""仪表板自动化任务配置的持久化存储。"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.app_config import get_settings

_AUTOMATIONS_FILENAME = "automations.json"


def _automations_path() -> Path:
    """返回自动化任务配置文件的完整路径。

    Returns:
        数据目录下的 ``automations.json`` 路径。
    """
    return get_settings().data_dir / _AUTOMATIONS_FILENAME


def _load_automations() -> list[dict[str, Any]]:
    """从磁盘加载所有自动化任务定义。

    文件不存在或格式损坏时返回空列表，不抛异常。

    Returns:
        自动化任务定义列表。
    """
    path = _automations_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_automations(automations: list[dict[str, Any]]) -> None:
    """将自动化任务定义列表写入磁盘。

    Args:
        automations: 待持久化的自动化任务定义列表。
    """
    path = _automations_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(automations, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def list_automations() -> list[dict[str, Any]]:
    """返回所有自动化任务定义，按更新时间倒序排列。

    Returns:
        按 ``updatedAt`` 降序排列的自动化任务定义列表。
    """
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
    """创建一条新的自动化任务定义并持久化。

    Args:
        payload: 包含任务名称、指令、执行计划等字段的请求体。

    Returns:
        新创建的自动化任务定义字典。
    """
    now = datetime.now(UTC).isoformat()
    automation = {
        "id": str(uuid.uuid4())[:8],
        "name": str(payload.get("name") or "").strip(),
        "prompt": str(payload.get("prompt") or "").strip(),
        "schedule": str(payload.get("schedule") or "").strip(),
        "repo": payload.get("repo") or None,
        "checks": _normalize_checks(payload.get("checks")),
        "model": payload.get("model_id") or "__builtin__",
        "autoApprove": _normalize_flag(payload.get("autoApprove")),
        "enabled": True,
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
    """更新一条自动化任务定义的可修改字段。

    Args:
        automation_id: 自动化任务 ID。
        payload: 需要更新的字段字典，仅处理已知可修改的键。

    Returns:
        更新后的自动化任务定义；ID 不存在时返回 ``None``。
    """
    automations = _load_automations()
    aliases = {"model_id": "model"}
    allowed = {
        "name",
        "prompt",
        "schedule",
        "repo",
        "checks",
        "model_id",
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
    """删除一条自动化任务定义。

    Args:
        automation_id: 自动化任务 ID。

    Returns:
        成功删除返回 ``True``，ID 不存在返回 ``False``。
    """
    automations = _load_automations()
    remaining = [item for item in automations if item.get("id") != automation_id]
    if len(remaining) == len(automations):
        return False
    _save_automations(remaining)
    return True


def mark_automation_run(automation_id: str, *, run_id: str | None) -> dict[str, Any] | None:
    """记下最近一轮运行的 id。

    和 :func:`mark_automation_triggered` 分开是因为**两件事发生的时刻不同**：
    触发标记必须在启动一轮**之前**写（``is_due`` 靠它判断「这一分钟跑过没有」），
    而运行的 id 只有跑完之后才知道。用触发那一个函数在跑完后再写一次的话，
    ``lastTriggeredAt`` 会被刷新成「跑完的时刻」，同一分钟内可能被再触发一次。

    因此这里**只动 ``lastRunId``**，不碰时间与错误字段。

    Args:
        automation_id: 自动化任务 id。
        run_id: 这一轮的运行 id；传 ``None`` 表示这一轮没跑起来，保持原值。

    Returns:
        更新后的自动化定义；id 不存在或 ``run_id`` 为空时返回 ``None``。
    """
    if not run_id:
        return None
    automations = _load_automations()
    for automation in automations:
        if automation.get("id") != automation_id:
            continue
        automation["lastRunId"] = str(run_id)
        automation["updatedAt"] = datetime.now(UTC).isoformat()
        _save_automations(automations)
        return automation
    return None


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
