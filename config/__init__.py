"""xihe-agent 配置子包。

统一管理环境变量、应用配置、日志配置和项目级共享常量。

子包结构:
    - ``env_config``: 从 ``.env`` 文件加载环境变量，空值不覆盖已有配置。
    - ``constants``: 集中管理散落在各模块中的魔法字符串和配置阈值。
    - ``app_config``: 提供 :class:`Settings` 单例，集中管理所有运行时配置项。
    - ``logging_config``: 按天轮转的日志系统，分离后端全量日志与 Agent 链路日志。

本模块将各子模块的公开名称 re-export 到统一命名空间，
调用方可直接 ``from config import ...`` 而无需关心底层模块路径。
"""

from config.app_config import (
    Settings,
    agent_log_path,
    backend_log_path,
    get_settings,
    log_date_text,
)
from config.constants import (
    CODEUP_URL_ARGUMENTS,
    GITEE_URL_ARGUMENTS,
    GITHUB_URL_ARGUMENTS,
    MIN_MESSAGE_LENGTH,
    PATH_ARGUMENTS,
    READ_FILE_INT_ARGUMENTS,
    READ_ONLY_TASK_KINDS,
    SENSITIVE_MARKERS,
    VALID_TASK_KINDS,
)
from config.env_config import get_env, load_environment, require_env
from config.logging_config import configure_logging, read_recent_log

__all__ = [
    "CODEUP_URL_ARGUMENTS",
    "GITEE_URL_ARGUMENTS",
    "GITHUB_URL_ARGUMENTS",
    "MIN_MESSAGE_LENGTH",
    "PATH_ARGUMENTS",
    "READ_FILE_INT_ARGUMENTS",
    "READ_ONLY_TASK_KINDS",
    "SENSITIVE_MARKERS",
    "Settings",
    "VALID_TASK_KINDS",
    "agent_log_path",
    "backend_log_path",
    "configure_logging",
    "get_env",
    "get_settings",
    "load_environment",
    "log_date_text",
    "read_recent_log",
    "require_env",
]
