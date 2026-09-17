"""Command approval preferences persisted for the local dashboard user."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from config.app_config import get_settings

_FILENAME = "command_approval.json"
_LOCK = threading.RLock()

CONFIGURABLE_CATEGORIES = (
    "run_tests",
    "run_build",
    "run_project_script",
    "install_dependencies",
    "modify_workspace_files",
    "delete_workspace_files",
    "git_commit",
    "git_branch_change",
    "network_read",
    "process_management",
)

LOCAL_DEFAULTS: dict[str, str] = {
    "run_tests": "allow",
    "run_build": "approval",
    "run_project_script": "approval",
    "install_dependencies": "approval",
    "modify_workspace_files": "approval",
    "delete_workspace_files": "approval",
    "git_commit": "approval",
    "git_branch_change": "approval",
    "network_read": "approval",
    "process_management": "approval",
}

SANDBOX_DEFAULTS: dict[str, str] = {
    "run_tests": "allow",
    "run_build": "allow",
    "run_project_script": "allow",
    "install_dependencies": "allow",
    "modify_workspace_files": "allow",
    "delete_workspace_files": "approval",
    "git_commit": "approval",
    "git_branch_change": "allow",
    "network_read": "allow",
    "process_management": "allow",
}

CATEGORY_LABELS = {
    "run_tests": ("运行测试", "pytest、npm test 等只执行测试的命令"),
    "run_build": ("构建与检查", "构建、格式化和静态检查命令"),
    "run_project_script": ("项目脚本", "执行仓库中的 Python、Node 或 npm 脚本"),
    "install_dependencies": ("安装依赖", "pip、npm 等可能执行安装脚本的操作"),
    "modify_workspace_files": ("修改工作区文件", "复制、移动、创建目录等文件操作"),
    "delete_workspace_files": ("删除工作区文件", "删除当前工作区内的文件或目录"),
    "git_commit": ("创建 Git 提交", "git add、commit、merge、rebase 等写操作"),
    "git_branch_change": ("切换 Git 分支", "checkout、switch、pull 等会改变工作区的操作"),
    "network_read": ("只读网络访问", "下载网页、查询 DNS 等不写入远端的请求"),
    "process_management": ("进程管理", "查看、启动或结束工作区相关进程"),
}

LOCKED_RULES = (
    ("工作区外写入", "deny"),
    ("读取受保护密钥", "deny"),
    ("强制推送与破坏性重置", "deny"),
    ("提权、系统或磁盘配置", "deny"),
    ("凭据注入与沙箱逃逸", "deny"),
    ("向外部服务写入数据", "approval"),
)


def _path() -> Path:
    return get_settings().data_dir / _FILENAME


def _load_overrides() -> dict[str, dict[str, str]]:
    path = _path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _validated_profile(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {
        key: action
        for key, action in value.items()
        if key in CONFIGURABLE_CATEGORIES and action in {"allow", "approval"}
    }


def get_command_approval_preferences() -> dict[str, Any]:
    """Return effective local/sandbox policies and immutable rule metadata."""
    with _LOCK:
        overrides = _load_overrides()
    local = {**LOCAL_DEFAULTS, **_validated_profile(overrides.get("local"))}
    sandbox = {**SANDBOX_DEFAULTS, **_validated_profile(overrides.get("sandbox"))}
    categories = [
        {"id": key, "label": CATEGORY_LABELS[key][0], "description": CATEGORY_LABELS[key][1]}
        for key in CONFIGURABLE_CATEGORIES
    ]
    return {
        "local": local,
        "sandbox": sandbox,
        "categories": categories,
        "lockedRules": [
            {"label": label, "action": action} for label, action in LOCKED_RULES
        ],
    }


def update_command_approval_preferences(payload: Any) -> dict[str, Any]:
    """Persist only known, user-configurable categories."""
    if not isinstance(payload, dict):
        raise ValueError("审批设置格式无效")
    stored = {
        "local": _validated_profile(payload.get("local")),
        "sandbox": _validated_profile(payload.get("sandbox")),
    }
    path = _path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(stored, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return get_command_approval_preferences()


def preference_for(backend_type: str, category: str) -> str:
    preferences = get_command_approval_preferences()
    profile = preferences["sandbox" if backend_type == "sandbox" else "local"]
    return str(profile.get(category, "approval"))
