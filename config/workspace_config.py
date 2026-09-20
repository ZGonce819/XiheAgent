"""本地仪表板用户的工作区目录偏好持久化配置。"""

from __future__ import annotations

import json
import threading
from pathlib import Path

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
    """加载已持久化的工作区配置。

    Returns:
        包含 ``workspace_root``、``projects_dir``、``skills_dir`` 的字典；
        未保存过配置时返回空字典。
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
    """持久化工作区配置。

    仅更新已提供的字段，其余保持不变。

    Args:
        workspace_root: 新的工作区根目录路径。
        projects_dir: 新的项目目录路径。
        skills_dir: 新的技能目录路径。

    Returns:
        更新后的配置字典。
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
