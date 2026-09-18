"""Agent 后端能力包。

各文件职责：
- ``local_shell.py``：实现 DeepAgents 本地 Windows backend，负责文件读写、命令执行、
  Gitee Git 认证、虚拟路径映射和安全边界。
- ``workspace.py``：封装工作区根目录和路径解析，保证所有相对路径最终都落在工作区内。
- ``permissions.py``：提供更底层的路径、命令、Git 分支名和提交信息安全校验。
- ``opensandbox.py``：基于 OpenSandbox 的远程沙盒 backend 实现，提供内核级隔离。

讲课时要强调：
Prompt 只能告诉模型"应该怎么做"，但 backend 才决定模型"实际能做什么"。
这个包里的代码就是 Agent 写代码、读文件、执行命令时的最后一道工程边界。
"""

from __future__ import annotations

import logging

from deepagents.backends.sandbox import BaseSandbox

logger = logging.getLogger(__name__)


def create_backend(
    *,
    thread_id: str | None = None,
    backend_type: str | None = None,
    local_folder: str | None = None,
    global_workspace_root: str | None = None,
) -> BaseSandbox:
    """根据配置创建对应的 Backend 实例。

    优先使用调用方显式传入的 ``backend_type``（来自前端用户选择），
    未传入时回退到全局 ``USE_SANDBOX`` 配置。

    开启沙箱时返回 ``OpenSandboxBackend``（远程容器隔离），
    否则返回 ``LocalShellBackend``（本地受控执行层）。

    当传入 ``local_folder`` 且未使用沙箱时，返回「本地文件夹模式」的
    ``LocalShellBackend``：以该文件夹为项目根，写操作限制在文件夹内，
    读操作可访问只读全局工作区 ``global_workspace_root``。

    Args:
        thread_id: 可选的会话 ID，沙箱模式下用于复用和注册。
        backend_type: 可选的运行环境类型，``"local"`` 或 ``"sandbox"``。
            为 ``None`` 时根据全局 ``USE_SANDBOX`` 配置决定。
        local_folder: 可选的项目本地文件夹绝对路径；非空且非沙箱时启用本地文件夹模式。
        global_workspace_root: 本地文件夹模式下使用的只读全局工作区根目录。

    Returns:
        符合 DeepAgents ``BaseSandbox`` 协议的 backend 实例。
    """
    from config.app_config import get_settings

    # 优先使用前端传入的 backend_type，否则回退到全局配置
    if backend_type is None:
        use_sandbox = get_settings().use_sandbox
    else:
        use_sandbox = backend_type == "sandbox"

    if use_sandbox:
        try:
            from backends.opensandbox import create_opensandbox_backend

            logger.info("backend_type=sandbox，使用 OpenSandbox 远程沙盒")
            return create_opensandbox_backend(thread_id=thread_id)
        except Exception:
            logger.exception("OpenSandbox 创建失败，回退到 LocalShellBackend")

    if local_folder:
        from backends.local_shell import LocalShellBackend

        logger.info("backend_type=local（本地文件夹模式），root=%s", local_folder)
        return LocalShellBackend(
            workspace=local_folder,
            local_folder_mode=True,
            global_workspace_root=global_workspace_root or get_settings().workspace_root,
        )

    from backends.local_shell import get_local_shell_backend

    logger.info("backend_type=local，使用 LocalShellBackend")
    return get_local_shell_backend()
