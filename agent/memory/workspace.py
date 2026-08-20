"""工作区级长期记忆。

从本地 ``workspace.md`` 中读取工作区事实描述，供上下文注入中间件使用。
这份记忆只描述固定目录的客观用途，不承载强制行为规则。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_WORKSPACE_MEMORY_PATH = _PROJECT_ROOT / "agent" / "memory" / "workspace.md"


def load_workspace_memory() -> str:
    """读取本地工作区事实记忆。

    返回 ``workspace.md`` 的纯文本内容。文件不存在时返回空字符串并记录警告，
    不会抛出异常，避免阻塞 Agent 启动流程。
    """

    try:
        return _WORKSPACE_MEMORY_PATH.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        logger.warning("工作区记忆文件不存在：%s", _WORKSPACE_MEMORY_PATH)
        return ""
