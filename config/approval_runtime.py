"""暂停的 LangGraph 命令审批持久化元数据索引。

检查点（checkpoint）仍然是图状态的真实来源。本文件仅包含刷新后
渲染和恢复审批所需的元数据。
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import UTC, datetime
from typing import Any

from config.app_config import get_settings

_FILENAME = "pending_command_approvals.json"
_LOCK = threading.RLock()


def _path():
    """返回待审批元数据文件的完整路径。

    Returns:
        数据目录下的 ``pending_command_approvals.json`` 路径。
    """
    return get_settings().data_dir / _FILENAME


def _load() -> dict[str, dict[str, Any]]:
    """从磁盘加载所有待审批记录。

    文件不存在或格式损坏时返回空字典，不抛异常。

    Returns:
        以会话 ID 为键的待审批记录字典。
    """
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    """将待审批记录字典写入磁盘。

    Args:
        data: 以会话 ID 为键的待审批记录字典。
    """
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save_pending_approval(
    *,
    thread_id: str,
    run_id: str,
    interrupt_id: str,
    approval: dict[str, Any],
    repo_url: str,
    backend_type: str,
    task_kind: str,
    model_id: str | None,
) -> dict[str, Any]:
    """保存一条待审批记录，供前端刷新后恢复审批界面。

    Args:
        thread_id: 会话 ID。
        run_id: 当前运行 ID。
        interrupt_id: LangGraph 中断点 ID。
        approval: 包含 ``action_requests`` 的原始审批数据。
        repo_url: 关联的代码仓库地址。
        backend_type: 后端类型（``"local"`` 或 ``"sandbox"``）。
        task_kind: 任务类型标识。
        model_id: 使用的模型 ID，可为 ``None``。

    Returns:
        保存后的公开审批记录字典。
    """
    raw_actions = approval.get("action_requests")
    actions = raw_actions if isinstance(raw_actions, list) else []
    public_actions: list[dict[str, str]] = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        args = action.get("args") if isinstance(action.get("args"), dict) else {}
        public_actions.append(
            {
                "name": str(action.get("name") or "execute"),
                "command": str(args.get("command") or ""),
                "description": str(action.get("description") or "此操作需要人工确认"),
            }
        )
    record = {
        "id": interrupt_id,
        "threadId": thread_id,
        "runId": run_id,
        "actions": public_actions,
        "backendType": backend_type,
        "repoUrl": repo_url,
        "taskKind": task_kind,
        "modelId": model_id,
        "createdAt": datetime.now(UTC).isoformat(),
    }
    with _LOCK:
        data = _load()
        data[thread_id] = record
        _save(data)
    return record


def get_pending_approval(thread_id: str) -> dict[str, Any] | None:
    """获取指定会话的待审批记录。

    Args:
        thread_id: 会话 ID。

    Returns:
        待审批记录字典，不存在时返回 ``None``。
    """
    with _LOCK:
        return _load().get(thread_id)


def claim_pending_approval(thread_id: str, approval_id: str) -> dict[str, Any]:
    """将一次恢复请求绑定到确切的中断点，拒绝重复提交。

    Args:
        thread_id: 会话 ID。
        approval_id: 客户端提交的审批 ID，必须与当前记录一致。

    Returns:
        已标记为 ``claimed`` 的审批记录。

    Raises:
        ValueError: 审批 ID 不匹配或已被占用时。
    """
    with _LOCK:
        data = _load()
        record = data.get(thread_id)
        if record is None or str(record.get("id") or "") != approval_id:
            raise ValueError("待审批操作已变化，请刷新页面后重试")
        if record.get("claimed"):
            raise ValueError("该审批决定正在处理中")
        record["claimed"] = True
        data[thread_id] = record
        _save(data)
        return record


def release_approval_claim(thread_id: str) -> None:
    """释放审批占用标记，允许重新提交审批决定。

    Args:
        thread_id: 会话 ID。
    """
    with _LOCK:
        data = _load()
        record = data.get(thread_id)
        if record is not None and record.pop("claimed", None):
            data[thread_id] = record
            _save(data)


def clear_pending_approval(thread_id: str) -> None:
    """清除指定会话的待审批记录。

    Args:
        thread_id: 会话 ID。
    """
    with _LOCK:
        data = _load()
        if data.pop(thread_id, None) is not None:
            _save(data)


def public_approval(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """提取可公开给前端的审批字段子集。

    已被 ``claimed`` 的记录视为处理中，返回 ``None`` 以避免重复表决。

    Args:
        record: 原始审批记录，可为 ``None``。

    Returns:
        包含公开字段的审批字典；记录为空或已占用时返回 ``None``。
    """
    if record is None or record.get("claimed"):
        return None
    return {
        key: record.get(key)
        for key in ("id", "threadId", "runId", "actions", "backendType", "createdAt")
    }
