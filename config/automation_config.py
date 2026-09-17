"""Persistent automation configuration for the dashboard."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
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


def create_automation(payload: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    automation = {
        "id": str(uuid.uuid4())[:8],
        "name": str(payload.get("name") or "").strip(),
        "prompt": str(payload.get("prompt") or "").strip(),
        "schedule": str(payload.get("schedule") or "").strip(),
        "repo": payload.get("repo") or None,
        "model": payload.get("model_id") or "__builtin__",
        "effort": payload.get("effort") or "default",
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
    allowed = {"name", "prompt", "schedule", "repo", "model_id", "effort", "enabled"}
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
        automation["updatedAt"] = datetime.now(timezone.utc).isoformat()
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
