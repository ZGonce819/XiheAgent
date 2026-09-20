"""后端安全校验模块。

提供路径安全、命令白名单和 Git 参数校验三类安全防线：

1. 路径风险：防止 Agent 通过绝对路径或 ``..`` 访问工作区外文件。
2. 命令风险：限制模型只能执行少量项目需要的命令族，拦截管道、重定向、
   删除、关机等危险操作。
3. Git 参数风险：分支名、提交信息来自模型输出，必须在进入 shell 前做
   归一化和校验。

注意：
    这里不是完整的企业沙箱，只是课程版本地 Windows backend 的安全收敛层。
    真正生产环境还应结合容器、系统权限、审计、网络隔离和更严格的命令执行策略。
"""

from __future__ import annotations

import re
from pathlib import Path


class WorkspacePermissionError(PermissionError):
    """工作区或命令权限错误。

    继承 ``PermissionError`` 的好处：
    - 调用方可以按标准权限异常处理；
    - middleware 能识别这是可恢复的安全拒绝，而不是系统崩溃；
    - 错误语义比普通 ``ValueError`` 更明确。
    """


def assert_path_inside(path: Path, root: Path) -> Path:
    """确认 path 解析后仍位于 root 工作区内。

    Args:
        path: 待校验路径，可以是相对路径或绝对路径。
        root: 工作区根目录。

    Returns:
        解析后的绝对路径。

    Raises:
        WorkspacePermissionError: 如果 path 不在 root 内。
    """
    resolved_root = root.resolve()
    resolved_path = path.resolve()
    if resolved_path == resolved_root or resolved_root in resolved_path.parents:
        return resolved_path
    raise WorkspacePermissionError(f"Path is outside workspace: {resolved_path}")


def normalize_safe_command(command: str) -> str:
    """校验并归一化 Agent 准备执行的本地命令。

    课程版第一版只允许少量教学需要的命令族：
    ``python/py``、``pytest``、``pip``、``git``、``dir``、``type``、``ruff``。

    模型经常在命令末尾追加 ``2>&1`` 或 ``| tail -5``。
    前者是为了合并 stderr，后者是 Unix 查看末尾输出的习惯。
    课程版在 Python 中已经捕获 stdout/stderr，也会把完整输出返回给模型，
    所以这里剥离这两个尾部片段，既兼容模型习惯，又不放开任意管道/重定向能力。

    Args:
        command: 模型生成的待执行命令字符串。

    Returns:
        归一化后的安全命令字符串。

    Raises:
        WorkspacePermissionError: 命令不在白名单或包含危险操作符。
    """
    normalized = command.strip()
    normalized = re.sub(r"\s+\|\s*tail\s+-?\d+\s*$", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+2>&1\s*$", "", normalized)
    lowered = normalized.lower()
    first_word = normalized.split(maxsplit=1)[0].lower() if normalized else ""

    allowed_commands = {"python", "py", "pytest", "pip", "git", "dir", "type", "ruff"}
    if first_word not in allowed_commands:
        raise WorkspacePermissionError(f"Command is not allowed: {command}")

    shell_operators = ["&&", "||", "|", "&", ";", ">", "<", "`", "$(", "\n", "\r"]
    if any(operator in normalized for operator in shell_operators):
        raise WorkspacePermissionError(f"Blocked shell operator in command: {command}")

    blocked = [
        "format ",
        "shutdown",
        "restart-computer",
        "remove-item",
        "remove-item -recurse",
        "rm -rf",
        "reg delete",
        "del ",
        "del /s",
        "rmdir ",
        "rmdir /s",
        "cipher /w",
    ]
    if any(token in lowered for token in blocked):
        raise WorkspacePermissionError(f"Blocked dangerous command: {command}")
    return normalized
