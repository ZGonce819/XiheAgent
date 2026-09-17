"""Durable metadata index for paused LangGraph command approvals.

The checkpoint remains the source of truth for graph state. This file contains
only the metadata required to render and resume an approval after a refresh.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

from config.app_config import get_settings

_FILENAME = "pending_command_approvals.json"
_LOCK = threading.RLock()


def _path():
    return get_settings().data_dir / _FILENAME


def _load() -> dict[str, dict[str, Any]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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
    with _LOCK:
        return _load().get(thread_id)


def claim_pending_approval(thread_id: str, approval_id: str) -> dict[str, Any]:
    """Bind one resume request to the exact interrupt and reject duplicates."""
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
    with _LOCK:
        data = _load()
        record = data.get(thread_id)
        if record is not None and record.pop("claimed", None):
            data[thread_id] = record
            _save(data)


def clear_pending_approval(thread_id: str) -> None:
    with _LOCK:
        data = _load()
        if data.pop(thread_id, None) is not None:
            _save(data)


def public_approval(record: dict[str, Any] | None) -> dict[str, Any] | None:
    if record is None or record.get("claimed"):
        return None
    return {
        key: record.get(key)
        for key in ("id", "threadId", "runId", "actions", "backendType", "createdAt")
    }
