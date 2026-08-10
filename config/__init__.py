"""Xihe 配置子包。

统一管理环境变量、应用配置和日志配置。
"""

from config.app_config import (
    Settings,
    agent_log_path,
    backend_log_path,
    get_settings,
    log_date_text,
)
from config.env_config import get_env, load_environment, require_env
from config.logging_config import configure_logging, read_recent_log

__all__ = [
    "Settings",
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
