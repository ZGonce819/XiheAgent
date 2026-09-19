"""Workspace directory preferences persisted for the local dashboard user."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config.constants import PROJECT_ROOT
from config.env_config import get_env

_FILENAME = "workspace_config.json"
_LOCK = threading.RLock()


def _path() -> Path:
    # 直接使用环境变量计算 data_dir，避免回调 get_settings() 造成无限递归
    data_dir = Path(
        get_env("XIHE_AGENT_DATA_DIR", str(PROJECT_ROOT / "data"))
    ).resolve()
    return data_dir / _FILENAME


def load_workspace_config() -> dict[str, str]:
    """Load persisted workspace configuration.

    Returns:
        Dictionary with workspace_root, projects_dir, skills_dir if persisted.
        Empty dict if no configuration has been saved.
    """
    path = _path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def save_workspace_config(
    workspace_root: str | None = None,
    projects_dir: str | None = None,
    skills_dir: str | None = None,
) -> dict[str, str]:
    """Persist workspace configuration.

    Only provided values are updated; others remain unchanged.

    Args:
        workspace_root: New workspace root path.
        projects_dir: New projects directory path.
        skills_dir: New skills directory path.

    Returns:
        The updated configuration dictionary.
    """
    stored = load_workspace_config()

    if workspace_root is not None:
        stored["workspace_root"] = workspace_root
    if projects_dir is not None:
        stored["projects_dir"] = projects_dir
    if skills_dir is not None:
        stored["skills_dir"] = skills_dir

    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    return stored
