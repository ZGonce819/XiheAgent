"""本地仪表板用户的命令审批偏好持久化配置。"""

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
    """返回审批偏好配置文件的完整路径。

    Returns:
        数据目录下的 ``command_approval.json`` 路径。
    """
    return get_settings().data_dir / _FILENAME


def _load_overrides() -> dict[str, dict[str, str]]:
    """从磁盘加载用户自定义的审批偏好覆盖。

    文件不存在或格式损坏时返回空字典，不抛异常。

    Returns:
        按后端类型分组的偏好覆盖字典。
    """
    path = _path()
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _validated_profile(value: Any) -> dict[str, str]:
    """过滤并校验单个后端的偏好配置，只保留合法类别和动作。

    Args:
        value: 原始偏好配置字典。

    Returns:
        经过类别和动作白名单过滤后的有效配置字典。
    """
    if not isinstance(value, dict):
        return {}
    return {
        key: action
        for key, action in value.items()
        if key in CONFIGURABLE_CATEGORIES and action in {"allow", "approval"}
    }


def get_command_approval_preferences() -> dict[str, Any]:
    """返回生效的本地/沙箱策略及不可变规则元数据。

    Returns:
        包含 ``local``、``sandbox``、``categories``、``lockedRules`` 的响应字典。
    """
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
    """持久化用户可配置的审批偏好，仅保留已知类别。

    Args:
        payload: 新的审批偏好配置，必须为字典格式。

    Returns:
        更新后的完整审批偏好配置。

    Raises:
        ValueError: ``payload`` 不是字典时。
    """
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
    """查询指定后端类型下某类命令的审批动作。

    Args:
        backend_type: 后端类型（``"local"`` 或 ``"sandbox"``）。
        category: 命令类别。

    Returns:
        对应的动作字符串（``"allow"``、``"approval"`` 或 ``"deny"``）。
    """
    preferences = get_command_approval_preferences()
    profile = preferences["sandbox" if backend_type == "sandbox" else "local"]
    return str(profile.get(category, "approval"))
